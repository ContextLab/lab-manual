"""
Scheduling workflow handlers for Slack.

Implements the /cdl-schedule command and the multi-step scheduling flow:
1. Director runs /cdl-schedule → configures project names, durations, emojis
2. Bot creates when2meet survey → posts link to #general
3. Director clicks "Collect Responses" → bot scrapes respondent names
4. Director assigns respondents to projects + marks senior/external via modal
5. Algorithm runs → director reviews → approves → announcement posted
"""

import json
import logging
import re
import time
from datetime import datetime, date

from slack_bolt import App
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..config import Config
from ..models.scheduling_session import SchedulingSession, SchedulingStatus
from ..scheduling_storage import (
    get_active_session, get_latest_completed_session,
    get_session, save_session,
)
from ..project_store import get_project_store
from ..services.when2meet_service import When2MeetService
from ..services.scheduling_service import (
    find_best_meeting_times, format_schedule_for_slack, format_announcement,
)

logger = logging.getLogger(__name__)


def _derive_term() -> tuple[str, str, str]:
    """
    Derive the current term name and approximate start/end dates from the month.

    Returns (term_name, start_date_iso, end_date_iso).
    Term derivation: Dec-Jan→Winter, Mar-Apr→Spring, Jun-Jul→Summer, Aug-Sep→Fall.
    """
    today = date.today()
    month = today.month
    year = today.year

    if month in (12, 1):
        term_year = year if month == 12 else year
        return f"Winter {term_year}", f"{term_year}-01-06", f"{term_year}-03-10"
    elif month in (3, 4):
        return f"Spring {year}", f"{year}-03-25", f"{year}-06-03"
    elif month in (6, 7):
        return f"Summer {year}", f"{year}-06-20", f"{year}-08-20"
    elif month in (8, 9):
        return f"Fall {year}", f"{year}-09-15", f"{year}-11-20"
    else:
        if month in (2,):
            return f"Spring {year}", f"{year}-03-25", f"{year}-06-03"
        elif month == 5:
            return f"Summer {year}", f"{year}-06-20", f"{year}-08-20"
        elif month in (10, 11):
            return f"Winter {year + 1}", f"{year + 1}-01-06", f"{year + 1}-03-10"
        else:
            return f"Winter {year}", f"{year}-01-06", f"{year}-03-10"


def _get_previous_projects_text() -> str:
    """Get pre-populated project text from the project database."""
    return get_project_store().get_config_text()


def register_schedule_handlers(app: App, config: Config):
    """Register all scheduling-related handlers with the Slack app."""

    when2meet_service = When2MeetService()

    # ── Step 1: /cdl-schedule → config modal ─────────────────────────────

    @app.command("/cdl-schedule")
    def handle_schedule_command(ack, command, client: WebClient, respond):
        """Handle the /cdl-schedule slash command."""
        ack()

        user_id = command["user_id"]

        if user_id != config.slack.admin_user_id:
            respond("Only the lab director can initiate scheduling.")
            return

        active = get_active_session()
        if active:
            respond(
                f"There's already an active scheduling session for *{active.term}* "
                f"(status: {active.status.value}). Use the existing session or "
                f"complete/cancel it first."
            )
            return

        term, term_start, term_end = _derive_term()
        projects_text = _get_previous_projects_text()

        try:
            client.views_open(
                trigger_id=command["trigger_id"],
                view=_build_config_modal(term, term_start, term_end, projects_text),
            )
        except SlackApiError as e:
            logger.error(f"Error opening schedule config modal: {e}")
            respond(f"Error opening configuration: {e}")

    @app.view("scheduling_config_form")
    def handle_config_submit(ack, body, client: WebClient, view):
        """Handle submission of the project configuration modal."""
        ack()

        user_id = body["user"]["id"]
        values = view["state"]["values"]

        term = values["term_block"]["term_input"]["value"]
        term_start = values["term_start_block"]["term_start_input"]["value"]
        term_end = values["term_end_block"]["term_end_input"]["value"]
        pi_text = values["pi_block"]["pi_input"]["value"]
        pi = [n.strip() for n in pi_text.split(",") if n.strip()]

        # Parse projects: "Project Name | duration | emoji" (NO members)
        projects_text = values["projects_block"]["projects_input"]["value"]
        project_names, durations, emojis = _parse_projects(projects_text)

        session_id = f"sched_{int(time.time())}"
        session = SchedulingSession(
            session_id=session_id,
            initiated_by=user_id,
            term=term,
            term_start=term_start,
            term_end=term_end,
            groups={name: [] for name in project_names},  # Empty until assignment
            preferred_durations=durations,
            project_emojis=emojis,
            pi=pi,
        )

        try:
            dm = client.conversations_open(users=[user_id])
            session.dm_channel = dm["channel"]["id"]
        except SlackApiError as e:
            logger.error(f"Error opening DM: {e}")
            return

        save_session(session)

        summary = _format_config_summary(session)
        try:
            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Scheduling configuration for {term}",
                blocks=[
                    {
                        "type": "header",
                        "text": {"type": "plain_text", "text": f"Scheduling: {term}"},
                    },
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": summary},
                    },
                    {"type": "divider"},
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": (
                                "Ready to create the When2Meet survey?\n"
                                "After people respond, you'll assign them to projects."
                            ),
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Create When2Meet & Post"},
                                "style": "primary",
                                "action_id": "sched_create_survey",
                                "value": session_id,
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Cancel"},
                                "style": "danger",
                                "action_id": "sched_cancel",
                                "value": session_id,
                            },
                        ],
                    },
                ],
            )
        except SlackApiError as e:
            logger.error(f"Error sending config summary: {e}")

    # ── Step 2: Create When2Meet → post to channel ───────────────────────

    @app.action("sched_create_survey")
    def handle_create_survey(ack, body, client: WebClient, action):
        """Create When2Meet survey and post to channel."""
        ack()

        session_id = action["value"]
        session = get_session(session_id)
        if not session:
            return

        try:
            survey_name = f"CDL {session.term} Availability"
            url = when2meet_service.create_survey(survey_name)
            session.when2meet_url = url
            save_session(session)
        except Exception as e:
            logger.error(f"Error creating When2Meet survey: {e}")
            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Error creating When2Meet survey: {e}",
            )
            return

        # Build project list with descriptions and channel links from database
        project_store = get_project_store()
        project_names = list(session.groups.keys())
        channel_id_map = _build_channel_id_map(client, project_store, project_names)
        project_list_text = project_store.get_survey_project_list(
            project_names, session.project_emojis,
            exclude_from_survey=["Office Hours"],
            channel_id_map=channel_id_map,
        )

        season_emojis = _season_emojis(session.term)

        survey_blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"{session.term} Meeting Scheduling"},
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"It's time to schedule meetings for *{session.term}*!{season_emojis}\n\n"
                        f"Please fill out this When2Meet with your availability "
                        f"(weekdays, 9 AM - 5 PM ET):\n\n"
                        f"*<{url}|Fill out When2Meet>*\n\n"
                        f"Here's the list of our weekly meetings for this term:\n\n"
                        f"{project_list_text}\n\n"
                        f"Please tag this message with the appropriate emoji(s) "
                        f"for meetings you want to attend. "
                        f"If you'd like a recurring individual meeting with me, "
                        f"react with :zoom:\n\n"
                        f"Use your *first name and last initial* on When2Meet so "
                        f"we can match you. "
                        f"Please complete this by end of day Friday."
                    ),
                },
            },
        ]

        try:
            # TODO: Change back to #general for production
            # channel_id = _find_channel(client, "general")
            channel_id = session.dm_channel  # TEST MODE: post to DM instead of #general
            if not channel_id:
                channel_id = session.dm_channel

            result = client.chat_postMessage(
                channel=channel_id,
                text=f"{session.term} meeting scheduling — fill out When2Meet!",
                blocks=survey_blocks,
            )
            session.survey_message_ts = result["ts"]
            session.survey_channel = channel_id
            session.update_status(SchedulingStatus.SURVEY_POSTED)
            save_session(session)
        except SlackApiError as e:
            logger.error(f"Error posting survey to channel: {e}")

        client.chat_postMessage(
            channel=session.dm_channel,
            text="When2Meet survey created and posted!",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"Survey created!\n\n"
                            f"*Survey URL:* <{url}>\n\n"
                            f"When everyone has responded, click below to collect "
                            f"responses and assign people to projects."
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Collect Responses"},
                            "style": "primary",
                            "action_id": "sched_collect_responses",
                            "value": session_id,
                        },
                    ],
                },
            ],
        )

    # ── Step 3: Collect responses → show respondents ─────────────────────

    @app.action("sched_collect_responses")
    def handle_collect_responses(ack, body, client: WebClient, action):
        """Scrape When2Meet responses and present respondent list."""
        ack()

        session_id = action["value"]
        session = get_session(session_id)
        if not session:
            return

        session.update_status(SchedulingStatus.COLLECTING)
        save_session(session)

        client.chat_postMessage(
            channel=session.dm_channel,
            text="Collecting When2Meet responses...",
        )

        try:
            respondent_names = when2meet_service.get_respondent_names(session.when2meet_url)
        except Exception as e:
            logger.error(f"Error scraping When2Meet: {e}")
            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Error scraping When2Meet: {e}",
            )
            session.update_status(SchedulingStatus.ERROR, str(e))
            save_session(session)
            return

        if not respondent_names:
            client.chat_postMessage(
                channel=session.dm_channel,
                text="No responses found on the When2Meet survey yet. Try again later.",
            )
            session.update_status(SchedulingStatus.SURVEY_POSTED)
            save_session(session)
            return

        # Clean up names (skip emails, normalize)
        clean_names = []
        for name in respondent_names:
            if "@" in name:
                continue
            clean_names.append(name.strip())

        # Store respondent names on the session for the assignment modal
        session.name_mapping = {name: name for name in clean_names}  # identity for now
        session.update_status(SchedulingStatus.NAME_MATCHING)
        save_session(session)

        names_list = "\n".join(f"  • {name}" for name in clean_names)
        project_list = "\n".join(
            f"  • {session.project_emojis.get(p, '')} {p}"
            for p in session.groups
        )

        client.chat_postMessage(
            channel=session.dm_channel,
            text=f"Found {len(clean_names)} respondents",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*{len(clean_names)} respondents found:*\n{names_list}\n\n"
                            f"*Projects to assign:*\n{project_list}\n\n"
                            f"Click below to assign people to projects and "
                            f"set senior/external designations."
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Assign Members to Projects"},
                            "style": "primary",
                            "action_id": "sched_open_assignment",
                            "value": session_id,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Re-collect"},
                            "action_id": "sched_collect_responses",
                            "value": session_id,
                        },
                    ],
                },
            ],
        )

    # ── Step 4: Assignment modal ─────────────────────────────────────────

    @app.action("sched_open_assignment")
    def handle_open_assignment(ack, body, client: WebClient, action):
        """Open the member assignment modal."""
        ack()

        session_id = action["value"]
        session = get_session(session_id)
        if not session:
            return

        respondent_names = list(session.name_mapping.keys())

        try:
            client.views_open(
                trigger_id=body["trigger_id"],
                view=_build_assignment_modal(session, respondent_names),
            )
        except SlackApiError as e:
            logger.error(f"Error opening assignment modal: {e}")
            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Error opening assignment modal: {e}",
            )

    @app.view("scheduling_assignment_form")
    def handle_assignment_submit(ack, body, client: WebClient, view):
        """Handle submission of the member assignment modal."""
        ack()

        values = view["state"]["values"]

        # Extract session_id from private_metadata
        session_id = view.get("private_metadata", "")
        session = get_session(session_id)
        if not session:
            logger.error(f"No session found for {session_id}")
            return

        respondent_names = list(session.name_mapping.keys())

        # Parse project assignments
        for project_name in list(session.groups.keys()):
            block_id = f"proj_{_safe_id(project_name)}"
            action_id = f"assign_{_safe_id(project_name)}"
            selected = values.get(block_id, {}).get(action_id, {}).get("selected_options", [])
            session.groups[project_name] = [opt["value"] for opt in selected]

        # Parse senior members
        senior_selected = values.get("senior_block", {}).get("senior_select", {}).get("selected_options", [])
        session.senior = [opt["value"] for opt in senior_selected]

        # Parse external members
        external_selected = values.get("external_block", {}).get("external_select", {}).get("selected_options", [])
        session.external = [opt["value"] for opt in external_selected]

        # Parse extra external names (not in survey)
        extra_text = values.get("extra_external_block", {}).get("extra_external_input", {}).get("value", "")
        if extra_text:
            extra_names = [n.strip() for n in extra_text.split(",") if n.strip()]
            session.external.extend(extra_names)
            # Add extra externals to their assigned projects (they won't be in availability
            # but the algorithm handles missing names gracefully)

        # Lab Meeting gets everyone not external
        all_assigned = set()
        for members in session.groups.values():
            all_assigned.update(members)
        all_assigned.update(session.pi)

        # If Lab Meeting exists and is empty, auto-populate with all respondents
        if "Lab Meeting" in session.groups and not session.groups["Lab Meeting"]:
            session.groups["Lab Meeting"] = respondent_names

        save_session(session)

        # Now scrape full availability and run the algorithm
        try:
            availability = when2meet_service.parse_responses(session.when2meet_url)
        except Exception as e:
            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Error scraping When2Meet: {e}",
            )
            return

        _run_scheduling(client, session, availability)

    # ── Step 5: Review and approve ───────────────────────────────────────

    @app.action("sched_approve_schedule")
    def handle_approve_schedule(ack, body, client: WebClient, action):
        """Director approves the proposed schedule — post announcement."""
        ack()

        session_id = action["value"]
        session = get_session(session_id)
        if not session:
            return

        session.update_status(SchedulingStatus.ANNOUNCING)
        save_session(session)

        import pandas as pd
        schedule_df = pd.DataFrame(session.scheduled.get("_schedule_df_data", []))
        if not schedule_df.empty and "Meeting" in schedule_df.columns:
            schedule_df = schedule_df.set_index("Meeting")

        announcement = format_announcement(
            session.scheduled, schedule_df,
            session.groups, session.project_emojis, session.term,
        )

        # TODO: Change back to survey_channel for production
        # channel = session.survey_channel or session.dm_channel
        channel = session.dm_channel  # TEST MODE
        try:
            result = client.chat_postMessage(
                channel=channel,
                text=f"{session.term} meeting schedule",
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": announcement},
                    },
                ],
            )
            session.announcement_message_ts = result["ts"]
            session.update_status(SchedulingStatus.COMPLETED)
            save_session(session)

            # Sync projects back to database for future terms
            get_project_store().sync_from_session(
                list(session.groups.keys()),
                session.preferred_durations,
                session.project_emojis,
            )

            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Scheduling complete for {session.term}!",
            )
        except SlackApiError as e:
            logger.error(f"Error posting announcement: {e}")
            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Error posting announcement: {e}\n\nManual text:\n\n{announcement}",
            )
            session.update_status(SchedulingStatus.ERROR, str(e))
            save_session(session)

    @app.action("sched_reassign")
    def handle_reassign(ack, body, client: WebClient, action):
        """Re-open the assignment modal to change project assignments."""
        ack()

        session_id = action["value"]
        session = get_session(session_id)
        if not session:
            return

        respondent_names = list(session.name_mapping.keys())

        try:
            client.views_open(
                trigger_id=body["trigger_id"],
                view=_build_assignment_modal(session, respondent_names),
            )
        except SlackApiError as e:
            logger.error(f"Error opening assignment modal: {e}")

    @app.action("sched_cancel")
    def handle_cancel(ack, body, client: WebClient, action):
        """Cancel the scheduling session."""
        ack()

        session_id = action["value"]
        session = get_session(session_id)
        if not session:
            return

        session.update_status(SchedulingStatus.ERROR, "Cancelled by director")
        save_session(session)

        client.chat_postMessage(
            channel=session.dm_channel,
            text="Scheduling session cancelled.",
        )


# ── Run scheduling algorithm ─────────────────────────────────────────────

def _run_scheduling(client: WebClient, session: SchedulingSession, availability):
    """Run the scheduling algorithm and present results to director."""
    import pandas as pd

    session.update_status(SchedulingStatus.SCHEDULING)
    save_session(session)

    client.chat_postMessage(
        channel=session.dm_channel,
        text="Running scheduling algorithm...",
    )

    # The availability columns are When2Meet names. We use them directly
    # since the director assigned people using those same names.
    # No renaming needed — the group members ARE the When2Meet names.

    try:
        scheduled, schedule_df = find_best_meeting_times(
            availability=availability,
            PI=session.pi,
            senior=session.senior,
            external=session.external,
            groups=session.groups,
            preferred_durations=session.preferred_durations,
        )
    except Exception as e:
        logger.error(f"Scheduling algorithm error: {e}")
        client.chat_postMessage(
            channel=session.dm_channel,
            text=f"Scheduling algorithm error: {e}",
        )
        session.update_status(SchedulingStatus.ERROR, str(e))
        save_session(session)
        return

    # Serialize results
    serializable_scheduled = {}
    for meeting_name, details in scheduled.items():
        serializable_scheduled[meeting_name] = {
            "day": details["day"],
            "times": [str(t) for t in details["times"]],
            "pi_available": details["pi_available"],
            "senior_available": int(details["senior_available"]),
            "other_available": int(details["other_available"]),
            "total_group_size": details["total_group_size"],
            "is_biweekly": details.get("is_biweekly", False),
            "shares_slot": details.get("shares_slot", False),
            "shares_with": details.get("shares_with"),
        }

    if schedule_df is not None and not schedule_df.empty:
        df_data = schedule_df.reset_index().to_dict("records")
        serializable_scheduled["_schedule_df_data"] = df_data

    session.scheduled = serializable_scheduled
    session.update_status(SchedulingStatus.REVIEW)

    slack_summary = format_schedule_for_slack(
        scheduled, schedule_df, session.project_emojis
    )
    session.schedule_summary = slack_summary
    save_session(session)

    unscheduled = [name for name in session.groups if name not in scheduled]

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": f"Proposed Schedule: {session.term}"},
        },
        {
            "type": "section",
            "text": {"type": "mrkdwn", "text": slack_summary},
        },
    ]

    if unscheduled:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":warning: *Could not schedule:* {', '.join(unscheduled)}",
            },
        })

    blocks.extend([
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve & Post"},
                    "style": "primary",
                    "action_id": "sched_approve_schedule",
                    "value": session.session_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Re-assign Members"},
                    "action_id": "sched_reassign",
                    "value": session.session_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Re-collect Responses"},
                    "action_id": "sched_collect_responses",
                    "value": session.session_id,
                },
            ],
        },
    ])

    client.chat_postMessage(
        channel=session.dm_channel,
        text=f"Proposed schedule for {session.term}",
        blocks=blocks,
    )


# ── Modal builders ───────────────────────────────────────────────────────

def _safe_id(name: str) -> str:
    """Convert a project name to a safe block_id (alphanumeric + underscore, max 255)."""
    safe = re.sub(r'[^a-zA-Z0-9]', '_', name)[:100]
    return safe


def _build_config_modal(term: str, term_start: str, term_end: str,
                        projects_text: str = "") -> dict:
    """Build the initial configuration modal (projects only, no members)."""

    projects_hint = (
        "One project per line. Format: Project Name | duration | emoji\n\n"
        "Duration = number of 15-min blocks (4=60min). "
        "Add .5 for biweekly (2.5 = biweekly 30min).\n"
        "Edit/add/remove projects as needed for this term."
    )

    return {
        "type": "modal",
        "callback_id": "scheduling_config_form",
        "title": {"type": "plain_text", "text": "Schedule Meetings"},
        "submit": {"type": "plain_text", "text": "Configure"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "input",
                "block_id": "term_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "term_input",
                    "initial_value": term,
                },
                "label": {"type": "plain_text", "text": "Term"},
            },
            {
                "type": "input",
                "block_id": "term_start_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "term_start_input",
                    "initial_value": term_start,
                },
                "label": {"type": "plain_text", "text": "Term Start Date (YYYY-MM-DD)"},
            },
            {
                "type": "input",
                "block_id": "term_end_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "term_end_input",
                    "initial_value": term_end,
                },
                "label": {"type": "plain_text", "text": "Term End Date (YYYY-MM-DD)"},
            },
            {
                "type": "input",
                "block_id": "projects_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "projects_input",
                    "multiline": True,
                    **({"initial_value": projects_text} if projects_text else {
                        "placeholder": {
                            "type": "plain_text",
                            "text": "Lab Meeting | 4 | :raising_hand:\nKraken | 4 | :octopus:",
                        },
                    }),
                },
                "label": {"type": "plain_text", "text": "Projects (name | duration | emoji)"},
                "hint": {"type": "plain_text", "text": projects_hint},
            },
            {
                "type": "input",
                "block_id": "pi_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "pi_input",
                    "initial_value": "Jeremy",
                },
                "label": {"type": "plain_text", "text": "PI Name(s) (comma-separated)"},
                "hint": {
                    "type": "plain_text",
                    "text": "Must match their When2Meet name exactly.",
                },
            },
        ],
    }


def _build_assignment_modal(session: SchedulingSession,
                            respondent_names: list) -> dict:
    """
    Build the member assignment modal.

    One multi-select per project (pick from respondent names),
    plus senior and external designation selects.
    """
    # Build options from respondent names
    member_options = [
        {"text": {"type": "plain_text", "text": name}, "value": name}
        for name in sorted(respondent_names)
    ]

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Assign the {len(respondent_names)} respondents to projects.\n"
                    f"_Lab Meeting will default to all non-external members if left empty._"
                ),
            },
        },
        {"type": "divider"},
    ]

    # One multi-select per project
    for project_name in session.groups:
        block_id = f"proj_{_safe_id(project_name)}"
        action_id = f"assign_{_safe_id(project_name)}"
        emoji = session.project_emojis.get(project_name, "")
        label = f"{emoji} {project_name}" if emoji else project_name

        # Pre-select members if already assigned (for re-assignment)
        initial = None
        existing = session.groups.get(project_name, [])
        if existing:
            initial = [
                opt for opt in member_options
                if opt["value"] in existing
            ]

        element = {
            "type": "multi_static_select",
            "action_id": action_id,
            "placeholder": {"type": "plain_text", "text": "Select members..."},
            "options": member_options,
        }
        if initial:
            element["initial_options"] = initial

        blocks.append({
            "type": "input",
            "block_id": block_id,
            "optional": True,
            "element": element,
            "label": {"type": "plain_text", "text": label[:75]},  # Slack limit
        })

    blocks.append({"type": "divider"})

    # Senior members select
    senior_element = {
        "type": "multi_static_select",
        "action_id": "senior_select",
        "placeholder": {"type": "plain_text", "text": "Select senior members..."},
        "options": member_options,
    }
    if session.senior:
        initial_senior = [opt for opt in member_options if opt["value"] in session.senior]
        if initial_senior:
            senior_element["initial_options"] = initial_senior

    blocks.append({
        "type": "input",
        "block_id": "senior_block",
        "optional": True,
        "element": senior_element,
        "label": {"type": "plain_text", "text": "Senior Members (3x scheduling weight)"},
    })

    # External members select
    external_element = {
        "type": "multi_static_select",
        "action_id": "external_select",
        "placeholder": {"type": "plain_text", "text": "Select external members..."},
        "options": member_options,
    }
    if session.external:
        initial_ext = [opt for opt in member_options if opt["value"] in session.external]
        if initial_ext:
            external_element["initial_options"] = initial_ext

    blocks.append({
        "type": "input",
        "block_id": "external_block",
        "optional": True,
        "element": external_element,
        "label": {"type": "plain_text", "text": "External Members (skip Lab Meeting)"},
    })

    # Extra external people not in survey
    blocks.append({
        "type": "input",
        "block_id": "extra_external_block",
        "optional": True,
        "element": {
            "type": "plain_text_input",
            "action_id": "extra_external_input",
            "placeholder": {"type": "plain_text", "text": "Dan, MJ (names not in survey)"},
        },
        "label": {"type": "plain_text", "text": "Additional External Members (not in survey, comma-separated)"},
    })

    return {
        "type": "modal",
        "callback_id": "scheduling_assignment_form",
        "private_metadata": session.session_id,
        "title": {"type": "plain_text", "text": "Assign Members"},
        "submit": {"type": "plain_text", "text": "Run Scheduler"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }


# ── Helper functions ─────────────────────────────────────────────────────

def _parse_projects(text: str) -> tuple[list, dict, dict]:
    """
    Parse the projects text block into names, durations, and emojis.

    Format per line: "Project Name | duration | emoji"
    No members — those are assigned after collecting When2Meet responses.

    Returns (project_names list, durations dict, emojis dict).
    """
    project_names = []
    durations = {}
    emojis = {}

    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        parts = [p.strip() for p in line.split("|")]
        name = parts[0].strip()
        if not name:
            continue

        project_names.append(name)

        if len(parts) > 1 and parts[1]:
            dur_str = parts[1].strip()
            try:
                durations[name] = float(dur_str) if "." in dur_str else int(dur_str)
            except ValueError:
                durations[name] = 2

        if len(parts) > 2 and parts[2]:
            emojis[name] = parts[2].strip()

    return project_names, durations, emojis


def _format_config_summary(session: SchedulingSession) -> str:
    """Format the session configuration as a readable summary."""
    lines = [
        f"*Term:* {session.term} ({session.term_start} to {session.term_end})",
        f"*PI:* {', '.join(session.pi)}",
        f"\n*Projects ({len(session.groups)}):*",
    ]

    for name in session.groups:
        dur = session.preferred_durations.get(name, 2)
        dur_min = int(dur) * 15 if dur == int(dur) else int(dur) * 15
        biweekly = " (biweekly)" if dur != int(dur) else ""
        emoji = session.project_emojis.get(name, "")
        emoji_str = f" {emoji}" if emoji else ""
        lines.append(f"  {emoji_str} *{name}* — {dur_min}min{biweekly}")

    lines.append("\n_Members will be assigned after collecting When2Meet responses._")

    return "\n".join(lines)


def _fuzzy_match_names(respondent_names: list, expected_members: list) -> tuple[dict, list]:
    """
    Fuzzy match When2Meet respondent names to expected member names.

    Strategy:
    1. Exact match (case-insensitive)
    2. First-name match (respondent's first word matches expected name)
    3. Match respondent to first name of multi-word expected member
    4. Fuzzy match via rapidfuzz (score >= 60)
    5. First-name prefix match (fallback)

    Returns (mapping dict, unmatched list).
    """
    try:
        from rapidfuzz import fuzz, process
        has_rapidfuzz = True
    except ImportError:
        has_rapidfuzz = False
        logger.warning("rapidfuzz not installed — falling back to prefix matching")

    mapping = {}
    unmatched = []
    remaining_members = list(expected_members)
    member_lower_map = {m.lower(): m for m in remaining_members}

    for resp_name in respondent_names:
        matched = False
        resp_lower = resp_name.lower().strip()
        resp_first = resp_lower.split()[0] if resp_lower.split() else resp_lower

        if "@" in resp_name:
            unmatched.append(resp_name)
            continue

        # 1. Exact match
        if resp_lower in member_lower_map:
            member = member_lower_map[resp_lower]
            mapping[resp_name] = member
            remaining_members.remove(member)
            member_lower_map = {m.lower(): m for m in remaining_members}
            continue

        # 2. First-name match: "Aaron Raycove" -> "Aaron"
        if resp_first in member_lower_map:
            member = member_lower_map[resp_first]
            mapping[resp_name] = member
            remaining_members.remove(member)
            member_lower_map = {m.lower(): m for m in remaining_members}
            continue

        # 3. Match to first name of multi-word expected: "Xin" -> "Xin Jin"
        first_name_matches = [
            m for m in remaining_members
            if m.lower().split()[0] == resp_first and len(m.split()) > 1
        ]
        if len(first_name_matches) == 1:
            member = first_name_matches[0]
            mapping[resp_name] = member
            remaining_members.remove(member)
            member_lower_map = {m.lower(): m for m in remaining_members}
            continue

        # 4. Fuzzy match
        if has_rapidfuzz and remaining_members:
            result = process.extractOne(
                resp_name, remaining_members,
                scorer=fuzz.token_sort_ratio,
                score_cutoff=60,
            )
            if result:
                match_name, score, _ = result
                mapping[resp_name] = match_name
                remaining_members.remove(match_name)
                member_lower_map = {m.lower(): m for m in remaining_members}
                continue

        # 5. Prefix fallback
        for member in remaining_members:
            member_lower = member.lower()
            if (resp_first.startswith(member_lower) or
                    member_lower.startswith(resp_first)):
                mapping[resp_name] = member
                remaining_members.remove(member)
                member_lower_map = {m.lower(): m for m in remaining_members}
                matched = True
                break

        if not matched:
            unmatched.append(resp_name)

    return mapping, unmatched


def _find_channel(client: WebClient, channel_name: str) -> str:
    """Find a channel ID by name."""
    try:
        result = client.conversations_list(types="public_channel", limit=200)
        for ch in result["channels"]:
            if ch["name"] == channel_name:
                return ch["id"]
    except SlackApiError as e:
        logger.error(f"Error listing channels: {e}")
    return ""


def _build_channel_id_map(client: WebClient, project_store, project_names: list) -> dict:
    """
    Look up Slack channel IDs for all channels referenced in the project database.
    Returns dict of "#channel-name" -> "C12345".
    """
    # Collect all channel names we need
    needed = set()
    for name in project_names:
        info = project_store.get(name)
        if info:
            for ch in info.get("channels", []):
                needed.add(ch.lstrip("#"))

    if not needed:
        return {}

    # Fetch workspace channels (paginated)
    channel_map = {}
    try:
        cursor = None
        while True:
            kwargs = {"types": "public_channel", "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            result = client.conversations_list(**kwargs)
            for ch in result["channels"]:
                if ch["name"] in needed:
                    channel_map[f"#{ch['name']}"] = ch["id"]
            cursor = result.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
    except SlackApiError as e:
        logger.error(f"Error listing channels for ID lookup: {e}")

    return channel_map


def _season_emojis(term: str) -> str:
    """Return season-appropriate emojis for the term name."""
    term_lower = term.lower()
    if "winter" in term_lower:
        return " :snowflake: :snowman:"
    elif "spring" in term_lower:
        return " :cherry_blossom: :tulip: :sunny:"
    elif "summer" in term_lower:
        return " :sunny: :palm_tree: :ocean:"
    elif "fall" in term_lower:
        return " :fallen_leaf: :maple_leaf: :jack_o_lantern:"
    return ""
