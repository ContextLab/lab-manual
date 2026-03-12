"""
Scheduling workflow handlers for Slack.

Implements the /cdl-schedule command and the multi-step scheduling flow:
1. Director runs /cdl-schedule → opens project configuration modal
2. Bot creates when2meet survey → posts link to #general
3. Director clicks "Collect Responses" → bot scrapes + fuzzy-matches names
4. Director confirms name mappings → algorithm runs
5. Director reviews proposed schedule → confirms
6. Bot posts announcement to #general
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
        # Off-cycle month — default to next upcoming term
        if month in (2,):
            return f"Spring {year}", f"{year}-03-25", f"{year}-06-03"
        elif month == 5:
            return f"Summer {year}", f"{year}-06-20", f"{year}-08-20"
        elif month in (10, 11):
            return f"Winter {year + 1}", f"{year + 1}-01-06", f"{year + 1}-03-10"
        else:
            return f"Winter {year}", f"{year}-01-06", f"{year}-03-10"


def _get_previous_emojis() -> dict:
    """Load project emoji mappings from the most recent completed session."""
    prev = get_latest_completed_session()
    if prev and prev.project_emojis:
        return dict(prev.project_emojis)
    return {}


def register_schedule_handlers(app: App, config: Config):
    """Register all scheduling-related handlers with the Slack app."""

    when2meet_service = When2MeetService()

    @app.command("/cdl-schedule")
    def handle_schedule_command(ack, command, client: WebClient, respond):
        """Handle the /cdl-schedule slash command."""
        ack()

        user_id = command["user_id"]

        # Admin only
        if user_id != config.slack.admin_user_id:
            respond("Only the lab director can initiate scheduling.")
            return

        # Check for existing active session
        active = get_active_session()
        if active:
            respond(
                f"There's already an active scheduling session for *{active.term}* "
                f"(status: {active.status.value}). Use the existing session or "
                f"complete/cancel it first."
            )
            return

        # Open the configuration modal
        term, term_start, term_end = _derive_term()
        prev_emojis = _get_previous_emojis()

        try:
            client.views_open(
                trigger_id=command["trigger_id"],
                view=_build_config_modal(term, term_start, term_end, prev_emojis),
            )
        except SlackApiError as e:
            logger.error(f"Error opening schedule config modal: {e}")
            respond(f"Error opening configuration: {e}")

    @app.view("scheduling_config_form")
    def handle_config_submit(ack, body, client: WebClient, view):
        """Handle submission of the scheduling configuration modal."""
        ack()

        user_id = body["user"]["id"]
        values = view["state"]["values"]

        # Parse form values
        term = values["term_block"]["term_input"]["value"]
        term_start = values["term_start_block"]["term_start_input"]["value"]
        term_end = values["term_end_block"]["term_end_input"]["value"]

        # Parse projects (one per line: "Project Name: member1, member2 | duration_blocks | emoji")
        projects_text = values["projects_block"]["projects_input"]["value"]
        groups, durations, emojis = _parse_projects(projects_text)

        # Parse priority lists
        pi_text = values["pi_block"]["pi_input"]["value"]
        pi = [n.strip() for n in pi_text.split(",") if n.strip()]

        senior_text = values.get("senior_block", {}).get("senior_input", {}).get("value", "")
        senior_list = [n.strip() for n in senior_text.split(",") if n.strip()] if senior_text else []

        external_text = values.get("external_block", {}).get("external_input", {}).get("value", "")
        external_list = [n.strip() for n in external_text.split(",") if n.strip()] if external_text else []

        # Create session
        session_id = f"sched_{int(time.time())}"
        session = SchedulingSession(
            session_id=session_id,
            initiated_by=user_id,
            term=term,
            term_start=term_start,
            term_end=term_end,
            groups=groups,
            preferred_durations=durations,
            project_emojis=emojis,
            pi=pi,
            senior=senior_list,
            external=external_list,
        )

        # Open DM with director
        try:
            dm = client.conversations_open(users=[user_id])
            session.dm_channel = dm["channel"]["id"]
        except SlackApiError as e:
            logger.error(f"Error opening DM: {e}")
            return

        save_session(session)

        # Show config summary and offer to create when2meet
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
                            "text": "Ready to create the When2Meet survey and post it to #general?",
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

    @app.action("sched_create_survey")
    def handle_create_survey(ack, body, client: WebClient, action):
        """Create When2Meet survey and post to #general."""
        ack()

        session_id = action["value"]
        session = get_session(session_id)
        if not session:
            return

        # Create the survey
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

        # Post to #general
        all_members = session.get_all_members()
        member_list = ", ".join(all_members)

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
                        f"It's time to schedule meetings for *{session.term}*!\n\n"
                        f"Please fill out this When2Meet with your availability "
                        f"(weekdays, 9 AM - 5 PM ET):\n\n"
                        f"*<{url}|Fill out When2Meet>*\n\n"
                        f"Use your *first name* so we can match you. "
                        f"Please complete this by end of day Friday."
                    ),
                },
            },
        ]

        try:
            # Try to find #general channel
            channel_id = _find_channel(client, "general")
            if not channel_id:
                channel_id = session.dm_channel
                logger.warning("Could not find #general, posting to DM instead")

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

        # Send control message to director
        client.chat_postMessage(
            channel=session.dm_channel,
            text=f"When2Meet survey created and posted!",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"Survey created and posted to <#{session.survey_channel}>.\n\n"
                            f"*Survey URL:* <{url}>\n\n"
                            f"When everyone has responded, click below to collect responses "
                            f"and run the scheduling algorithm."
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Collect Responses & Schedule"},
                            "style": "primary",
                            "action_id": "sched_collect_responses",
                            "value": session_id,
                        },
                    ],
                },
            ],
        )

    @app.action("sched_collect_responses")
    def handle_collect_responses(ack, body, client: WebClient, action):
        """Scrape When2Meet responses, fuzzy-match names, and run algorithm."""
        ack()

        session_id = action["value"]
        session = get_session(session_id)
        if not session:
            return

        session.update_status(SchedulingStatus.COLLECTING)
        save_session(session)

        # Status message
        client.chat_postMessage(
            channel=session.dm_channel,
            text="Collecting When2Meet responses...",
        )

        # Scrape responses
        try:
            import pandas as pd
            availability = when2meet_service.parse_responses(session.when2meet_url)
        except Exception as e:
            logger.error(f"Error scraping When2Meet: {e}")
            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Error scraping When2Meet: {e}",
            )
            session.update_status(SchedulingStatus.ERROR, str(e))
            save_session(session)
            return

        if availability.empty:
            client.chat_postMessage(
                channel=session.dm_channel,
                text="No responses found on the When2Meet survey yet. Try again later.",
            )
            session.update_status(SchedulingStatus.SURVEY_POSTED)
            save_session(session)
            return

        respondent_names = list(availability.columns)
        expected_members = session.get_all_members()

        # Fuzzy match names
        name_mapping, unmatched = _fuzzy_match_names(respondent_names, expected_members)
        session.name_mapping = name_mapping
        session.unmatched_names = unmatched

        # If there are unmatched names, ask director to confirm
        if unmatched:
            session.update_status(SchedulingStatus.NAME_MATCHING)
            save_session(session)

            match_summary = "*Name Matching Results:*\n\n"
            match_summary += "*Matched:*\n"
            for w2m_name, member_name in name_mapping.items():
                match_summary += f"  {w2m_name} → {member_name}\n"
            match_summary += f"\n*Unmatched ({len(unmatched)}):*\n"
            for name in unmatched:
                match_summary += f"  {name} (not matched to any expected member)\n"
            match_summary += "\n_Unmatched respondents will be ignored. Continue?_"

            client.chat_postMessage(
                channel=session.dm_channel,
                text="Name matching results",
                blocks=[
                    {
                        "type": "section",
                        "text": {"type": "mrkdwn", "text": match_summary},
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Continue with Scheduling"},
                                "style": "primary",
                                "action_id": "sched_run_algorithm",
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
            return

        # All matched — proceed directly to scheduling
        _run_scheduling(client, session, availability, name_mapping)

    @app.action("sched_run_algorithm")
    def handle_run_algorithm(ack, body, client: WebClient, action):
        """Run the scheduling algorithm after name confirmation."""
        ack()

        session_id = action["value"]
        session = get_session(session_id)
        if not session:
            return

        # Re-scrape and apply confirmed mappings
        try:
            availability = when2meet_service.parse_responses(session.when2meet_url)
        except Exception as e:
            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Error re-scraping When2Meet: {e}",
            )
            return

        _run_scheduling(client, session, availability, session.name_mapping)

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

        # Build announcement
        import pandas as pd
        schedule_df = pd.DataFrame(session.scheduled.get("_schedule_df_data", []))
        if not schedule_df.empty and "Meeting" in schedule_df.columns:
            schedule_df = schedule_df.set_index("Meeting")

        announcement = format_announcement(
            session.scheduled, schedule_df,
            session.groups, session.project_emojis, session.term,
        )

        # Post to #general (or the survey channel)
        channel = session.survey_channel or session.dm_channel
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

            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Announcement posted to <#{channel}>! Scheduling complete for {session.term}.",
            )
        except SlackApiError as e:
            logger.error(f"Error posting announcement: {e}")
            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Error posting announcement: {e}\n\nHere's the text to post manually:\n\n{announcement}",
            )
            session.update_status(SchedulingStatus.ERROR, str(e))
            save_session(session)

    @app.action("sched_edit_schedule")
    def handle_edit_schedule(ack, body, client: WebClient, action):
        """Director wants to re-run with adjustments."""
        ack()

        session_id = action["value"]
        session = get_session(session_id)
        if not session:
            return

        client.chat_postMessage(
            channel=session.dm_channel,
            text=(
                "To adjust the schedule, you can:\n"
                "1. Re-run `/cdl-schedule` with different project config\n"
                "2. Click 'Collect Responses & Schedule' again to re-run the algorithm\n\n"
                "The algorithm optimizes for attendance, day concentration, and contiguity. "
                "If a specific meeting needs a different time, you can manually adjust "
                "after posting."
            ),
        )

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


def _run_scheduling(client: WebClient, session: SchedulingSession,
                    availability, name_mapping: dict):
    """Run the scheduling algorithm and present results to director."""
    import pandas as pd

    session.update_status(SchedulingStatus.SCHEDULING)
    save_session(session)

    client.chat_postMessage(
        channel=session.dm_channel,
        text="Running scheduling algorithm...",
    )

    # Rename columns from when2meet names to expected member names
    rename_map = {w2m: member for w2m, member in name_mapping.items()}
    availability = availability.rename(columns=rename_map)

    # Drop columns not in our expected members (unmatched respondents)
    expected = set(session.get_all_members())
    cols_to_keep = [c for c in availability.columns if c in expected]
    availability = availability[cols_to_keep]

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

    # Store results — serialize scheduled dict (convert non-serializable types)
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

    # Store schedule_df data for later use
    if schedule_df is not None and not schedule_df.empty:
        df_data = schedule_df.reset_index().to_dict("records")
        serializable_scheduled["_schedule_df_data"] = df_data

    session.scheduled = serializable_scheduled
    session.update_status(SchedulingStatus.REVIEW)

    # Format for Slack
    slack_summary = format_schedule_for_slack(
        scheduled, schedule_df, session.project_emojis
    )
    session.schedule_summary = slack_summary
    save_session(session)

    # Count unscheduled meetings
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
                    "text": {"type": "plain_text", "text": "Approve & Post Announcement"},
                    "style": "primary",
                    "action_id": "sched_approve_schedule",
                    "value": session.session_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Re-run Algorithm"},
                    "action_id": "sched_collect_responses",
                    "value": session.session_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Edit Config"},
                    "action_id": "sched_edit_schedule",
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


def _build_config_modal(term: str, term_start: str, term_end: str,
                        prev_emojis: dict) -> dict:
    """Build the scheduling configuration modal."""

    # Pre-populate projects from previous emojis if available
    projects_hint = (
        "One project per line. Format:\n"
        "Project Name: member1, member2 | duration_blocks | emoji\n\n"
        "Examples:\n"
        "Lab Meeting: everyone | 4 | :microscope:\n"
        "Kraken: Paxton, Jacob, MJ | 4 | :octopus:\n"
        "1:1 (Claudia): Claudia | 2 | :speech_balloon:\n"
        "Office Hours: | 6 |\n\n"
        "Duration = number of 15-min blocks (4=60min). "
        "Add .5 for biweekly (2.5 = biweekly 30min).\n"
        "Use 'everyone' to include all members."
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
                    "placeholder": {
                        "type": "plain_text",
                        "text": "Lab Meeting: everyone | 4 | :microscope:",
                    },
                },
                "label": {"type": "plain_text", "text": "Projects & Members"},
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
            },
            {
                "type": "input",
                "block_id": "senior_block",
                "optional": True,
                "element": {
                    "type": "plain_text_input",
                    "action_id": "senior_input",
                    "placeholder": {"type": "plain_text", "text": "Paxton, Claudia, MJ"},
                },
                "label": {"type": "plain_text", "text": "Senior Members (3x weight, comma-separated)"},
            },
            {
                "type": "input",
                "block_id": "external_block",
                "optional": True,
                "element": {
                    "type": "plain_text_input",
                    "action_id": "external_input",
                    "placeholder": {"type": "plain_text", "text": "Dan, MJ, Jay"},
                },
                "label": {"type": "plain_text", "text": "External Members (skip lab meeting, comma-separated)"},
            },
        ],
    }


def _parse_projects(text: str) -> tuple[dict, dict, dict]:
    """
    Parse the projects text block into groups, durations, and emojis.

    Format per line: "Project Name: member1, member2 | duration | emoji"
    'everyone' as member list means all members (will be resolved later).
    """
    groups = {}
    durations = {}
    emojis = {}

    for line in text.strip().split("\n"):
        line = line.strip()
        if not line:
            continue

        # Split on | for parts
        parts = line.split("|")

        # First part: "Project Name: member1, member2"
        name_members = parts[0].strip()
        if ":" in name_members:
            name, members_str = name_members.split(":", 1)
            name = name.strip()
            members_str = members_str.strip()

            if members_str.lower() == "everyone":
                members = ["everyone"]  # Special marker, resolved at runtime
            else:
                members = [m.strip() for m in members_str.split(",") if m.strip()]
        else:
            name = name_members
            members = []

        groups[name] = members

        # Second part: duration (number of 15-min blocks)
        if len(parts) > 1:
            dur_str = parts[1].strip()
            try:
                durations[name] = float(dur_str) if "." in dur_str else int(dur_str)
            except ValueError:
                durations[name] = 2  # Default 30 min

        # Third part: emoji
        if len(parts) > 2:
            emoji = parts[2].strip()
            if emoji:
                emojis[name] = emoji

    return groups, durations, emojis


def _format_config_summary(session: SchedulingSession) -> str:
    """Format the session configuration as a readable summary."""
    lines = [
        f"*Term:* {session.term} ({session.term_start} to {session.term_end})",
        f"*PI:* {', '.join(session.pi)}",
    ]

    if session.senior:
        lines.append(f"*Senior:* {', '.join(session.senior)}")
    if session.external:
        lines.append(f"*External:* {', '.join(session.external)}")

    lines.append(f"\n*Projects ({len(session.groups)}):*")
    for name, members in session.groups.items():
        dur = session.preferred_durations.get(name, 2)
        dur_min = int(dur) * 15 if dur == int(dur) else int(dur) * 15
        biweekly = " (biweekly)" if dur != int(dur) else ""
        emoji = session.project_emojis.get(name, "")
        emoji_str = f" {emoji}" if emoji else ""
        member_str = ", ".join(members) if members else "(open)"
        lines.append(f"  {emoji_str} *{name}* — {dur_min}min{biweekly}: {member_str}")

    return "\n".join(lines)


def _fuzzy_match_names(respondent_names: list, expected_members: list) -> tuple[dict, list]:
    """
    Fuzzy match When2Meet respondent names to expected member names.

    Strategy:
    1. Exact match (case-insensitive)
    2. First-name match (respondent's first word matches expected name)
    3. Fuzzy match via rapidfuzz (score >= 60)
    4. First-name prefix match (fallback)

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

    # Build first-name lookup: "Aaron" -> "Aaron" (expected members are typically first names)
    member_lower_map = {m.lower(): m for m in remaining_members}

    for resp_name in respondent_names:
        matched = False
        resp_lower = resp_name.lower().strip()
        resp_first = resp_lower.split()[0] if resp_lower.split() else resp_lower

        # Skip obvious non-names (emails)
        if "@" in resp_name:
            unmatched.append(resp_name)
            continue

        # 1. Exact match (case-insensitive)
        if resp_lower in member_lower_map:
            member = member_lower_map[resp_lower]
            mapping[resp_name] = member
            remaining_members.remove(member)
            member_lower_map = {m.lower(): m for m in remaining_members}
            continue

        # 2a. First-name match: "Aaron Raycove" -> "Aaron"
        if resp_first in member_lower_map:
            member = member_lower_map[resp_first]
            mapping[resp_name] = member
            remaining_members.remove(member)
            member_lower_map = {m.lower(): m for m in remaining_members}
            continue

        # 2b. Match respondent to first name of a multi-word expected member:
        #     "Xin" -> "Xin Jin" (not "Xinming")
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

        # 3. Fuzzy match on full name
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

        # 4. First-name prefix/contains fallback
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
