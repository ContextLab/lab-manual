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
from datetime import datetime, date, timedelta

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
    Derive the upcoming term name and dates by scraping Dartmouth's academic calendar.

    Scrapes the registrar's term calendar index to find calendar page links,
    then parses the relevant page for classes begin/end dates.
    Falls back to month-based approximations if scraping fails.

    Returns (term_name, start_date_iso, end_date_iso).
    """
    try:
        terms = _scrape_dartmouth_calendar()
        if terms:
            return _pick_upcoming_term(terms)
    except Exception as e:
        logger.warning(f"Calendar scrape failed, using fallback: {e}")

    return _derive_term_fallback()


def _scrape_dartmouth_calendar() -> list[dict]:
    """
    Scrape Dartmouth registrar for term dates.

    1. Fetch index page to discover term calendar page URLs
    2. Fetch each calendar page and parse <dl> structure for dates

    Returns list of {name, start, end} dicts sorted by start date.
    """
    import requests
    from bs4 import BeautifulSoup

    base_url = "https://www.dartmouth.edu/reg/calendar/term/"
    index_resp = requests.get(base_url, timeout=10)
    index_resp.raise_for_status()
    index_soup = BeautifulSoup(index_resp.text, "html.parser")

    # Find links to term calendar pages (e.g., "25_26_term_calendar.html")
    calendar_links = set()
    for a in index_soup.find_all("a", href=True):
        href = a["href"]
        if "term_calendar" not in href:
            continue
        # Strip anchor fragments (#a, #b, etc.)
        href = href.split("#")[0]
        if not href.endswith(".html"):
            continue
        # Normalize to absolute URL
        if href.startswith("/"):
            calendar_links.add("https://www.dartmouth.edu" + href)
        elif not href.startswith("http"):
            calendar_links.add(base_url + href)
        else:
            calendar_links.add(href)

    all_terms = []
    for url in sorted(calendar_links):
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            terms = _parse_term_calendar_page(resp.text)
            all_terms.extend(terms)
        except Exception as e:
            logger.warning(f"Failed to parse {url}: {e}")

    return sorted(all_terms, key=lambda t: t["start"])


def _parse_term_calendar_page(html: str) -> list[dict]:
    """
    Parse a Dartmouth term calendar page for term dates.

    Structure: <table class="tableizer-table"> with:
    - Term headers in <tr class="tableizer-firstrow"><th>Summer Term 2025</th></tr>
    - Date entries in <tr><td>description</td><td>date</td></tr>
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    # Look in the main content area
    content = soup.find("div", id="b-content") or soup

    terms = []
    current_term = None
    classes_begin = None
    classes_end = None

    for table in content.find_all("table"):
        for row in table.find_all("tr"):
            # Check for term header row
            th = row.find("th")
            if th:
                # Save previous term if complete
                if current_term and classes_begin and classes_end:
                    terms.append({
                        "name": current_term,
                        "start": classes_begin,
                        "end": classes_end,
                    })
                term_match = re.search(
                    r"(Summer|Fall|Winter|Spring)\s+Term\s+(\d{4})",
                    th.get_text(strip=True), re.IGNORECASE,
                )
                if term_match:
                    season = term_match.group(1).capitalize()
                    year = term_match.group(2)
                    current_term = f"{season} {year}"
                    classes_begin = None
                    classes_end = None
                continue

            # Parse data rows
            cells = row.find_all("td")
            if len(cells) >= 2 and current_term:
                desc = cells[0].get_text(strip=True).lower()
                date_text = cells[1].get_text(strip=True)

                if "classes begin" in desc and not classes_begin:
                    parsed = _parse_calendar_date(date_text)
                    if parsed:
                        classes_begin = parsed
                elif "classes end" in desc and not classes_end:
                    parsed = _parse_calendar_date(date_text)
                    if parsed:
                        classes_end = parsed

    # Don't forget the last term
    if current_term and classes_begin and classes_end:
        terms.append({
            "name": current_term,
            "start": classes_begin,
            "end": classes_end,
        })

    return terms


def _parse_calendar_date(text: str) -> str:
    """
    Parse a date string like "January 5, 2026" or "March 10, 2026" to ISO format.
    Returns "YYYY-MM-DD" or None if parsing fails.
    """
    from datetime import datetime as dt

    # Clean up: remove asterisks, footnote markers, extra whitespace
    clean = re.sub(r"[*†‡§]", "", text).strip()
    # Try multiple date formats
    for fmt in ("%B %d, %Y", "%b %d, %Y", "%B %d %Y", "%b %d %Y"):
        try:
            return dt.strptime(clean, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    # Try extracting a date from longer text
    match = re.search(r"(\w+ \d{1,2},?\s*\d{4})", clean)
    if match:
        for fmt in ("%B %d, %Y", "%B %d %Y"):
            try:
                return dt.strptime(match.group(1), fmt).strftime("%Y-%m-%d")
            except ValueError:
                continue
    return None


def _pick_upcoming_term(terms: list[dict]) -> tuple[str, str, str]:
    """
    Pick the most relevant upcoming term from the scraped list.

    Logic: find the next term whose classes haven't ended yet,
    or if we're between terms, the next one starting.
    """
    today_str = date.today().isoformat()

    # Find terms that haven't ended yet
    upcoming = [t for t in terms if t["end"] >= today_str]
    if upcoming:
        # Pick the one that starts soonest (or is currently in progress)
        best = upcoming[0]
        return best["name"], best["start"], best["end"]

    # All terms are in the past — shouldn't happen, fall back
    if terms:
        last = terms[-1]
        return last["name"], last["start"], last["end"]

    raise ValueError("No terms found")


def _derive_term_fallback() -> tuple[str, str, str]:
    """Fallback: approximate term dates from the current month."""
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
    elif month == 2:
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

        # Parse projects: "Name | duration | emoji | description | #ch1, #ch2"
        projects_text = values["projects_block"]["projects_input"]["value"]
        project_names, durations, emojis, descriptions, channels = _parse_projects(projects_text)

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
            project_descriptions=descriptions,
            project_channels=channels,
            pi=pi,
        )

        # Sync new/changed projects to the database immediately
        # so the survey announcement can use descriptions and channels
        get_project_store().sync_from_session(
            project_names, durations, emojis, descriptions, channels,
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
        term_start_friendly = _friendly_date(session.term_start)
        deadline = _friday_before(session.term_start)

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
                        f"Hey @channel!{season_emojis}\n\n"
                        f"I'd like to nail down our meeting times for the upcoming "
                        f"*{session.term}* term! Please fill out your availability "
                        f"for weekly meetings (lab meetings + project meetings):\n\n"
                        f"*<{url}|Fill out When2Meet>*\n\n"
                        f"Regular meetings will start up again on *{term_start_friendly}* "
                        f"(i.e., on the first day of the term)."
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"Here's the list of our weekly meetings for this term:\n\n"
                        f"{project_list_text}"
                    ),
                },
            },
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        f"Please react to this message with the appropriate emoji(s) "
                        f"for meetings you want to attend (everyone should be at "
                        f"full-lab meetings if possible). If you'd like a recurring "
                        f"individual meeting with me, react with :zoom:\n\n"
                        f"Anyone is welcome at any project meeting — the lab's policy "
                        f"is: anyone can work on any project they are interested in. "
                        f"Feel free to explore!\n\n"
                        f"All group meetings are hybrid: in person (Moore 416) and "
                        f"via Zoom (<https://dartmouth.zoom.us/my/contextlab|link>). "
                        f"Individual meetings in Moore 349 or same Zoom link.\n\n"
                        f"Use your *first name and last initial* on When2Meet. "
                        f"Fill out availability + add emoji reactions by "
                        f"*end of day {deadline}*. "
                        f"(If you don't, your preferences won't be taken into account.)"
                    ),
                },
            },
        ]

        try:
            channel_id = _find_channel(client, "general")
            if not channel_id:
                channel_id = session.dm_channel  # Fallback to DM if #general not found

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

        # Auto-detect PI respondent names and set up merges so PI availability
        # is used as a real constraint (not assumed always-available).
        pi_merges = _auto_match_pi_names(session.pi, clean_names)
        if pi_merges:
            session.name_merges.update(pi_merges)
            logger.info(f"Auto-matched PI names: {pi_merges}")

        # Store respondent names on the session for the assignment modal
        session.name_mapping = {name: name for name in clean_names}  # identity for now
        session.update_status(SchedulingStatus.NAME_MATCHING)
        save_session(session)

        # Check for :zoom: reactions on the survey message (individual meeting requests)
        zoom_requesters = _get_zoom_reactors(client, session)
        if zoom_requesters:
            session.zoom_requests = [
                {"user_id": uid, "name": name, "accepted": True, "duration_blocks": 2}
                for uid, name in zoom_requesters
            ]

        # Auto-populate project assignments from emoji reactions
        _auto_populate_from_reactions(client, session, clean_names)

        # Auto-populate senior members from #senior-lab-stuff channel
        _auto_populate_senior(client, session, clean_names)

        save_session(session)

        names_list = "\n".join(f"  • {name}" for name in clean_names)
        project_list = "\n".join(
            f"  • {session.project_emojis.get(p, '')} {p}"
            for p in session.groups
        )

        # Auto-detect potential duplicate respondents by first name
        potential_dupes = _detect_potential_duplicates(clean_names)
        dupe_text = ""
        if potential_dupes:
            dupe_lines = []
            for group in potential_dupes:
                dupe_lines.append(f"  :warning: {' / '.join(group)}")
            dupe_text = (
                f"\n\n*Potential duplicates detected:*\n"
                + "\n".join(dupe_lines)
                + "\n_Use 'Resolve Names' to merge duplicates or confirm distinct._"
            )

        # Build action buttons
        action_elements = []

        # Always show "Resolve Names" first (recommended if duplicates detected)
        action_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Resolve Names"},
            "style": "primary" if potential_dupes else None,
            "action_id": "sched_resolve_names",
            "value": session_id,
        })
        # Remove None style (Slack doesn't accept it)
        if action_elements[-1]["style"] is None:
            del action_elements[-1]["style"]

        if zoom_requesters:
            action_elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": f"Review {len(zoom_requesters)} 1-on-1 Request(s)"},
                "action_id": "sched_review_zoom",
                "value": session_id,
            })
        if not zoom_requesters and not potential_dupes:
            # No zoom requests and no duplicates — can go straight to assignment
            action_elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": "Assign Members to Projects"},
                "action_id": "sched_open_assignment",
                "value": session_id,
            })
        action_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Re-collect"},
            "action_id": "sched_collect_responses",
            "value": session_id,
        })

        zoom_text = ""
        if zoom_requesters:
            zoom_names = ", ".join(name for _, name in zoom_requesters)
            zoom_text = (
                f"\n\n:zoom: *Individual meeting requests:* {zoom_names}\n"
                f"_Review these after resolving names._"
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
                            f"*{len(clean_names)} respondents found:*\n{names_list}"
                            f"{dupe_text}"
                            f"{zoom_text}\n\n"
                            f"*Projects to assign:*\n{project_list}"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": action_elements,
                },
            ],
        )

    # ── Step 3b: Resolve names (merge duplicates) ───────────────────────

    @app.action("sched_resolve_names")
    def handle_resolve_names(ack, body, client: WebClient, action):
        """Open a modal for the director to merge duplicate respondents."""
        ack()

        session_id = action["value"]
        session = get_session(session_id)
        if not session:
            return

        try:
            client.views_open(
                trigger_id=body["trigger_id"],
                view=_build_name_resolution_modal(session),
            )
        except SlackApiError as e:
            logger.error(f"Error opening name resolution modal: {e}")
            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Error opening name resolution: {e}",
            )

    @app.view("sched_resolve_names_submit")
    def handle_resolve_names_submit(ack, body, client: WebClient, view):
        """Process name resolution decisions."""
        ack()

        metadata = json.loads(view["private_metadata"])
        session_id = metadata["session_id"]
        session = get_session(session_id)
        if not session:
            return

        values = view["state"]["values"]
        merge_text = values["name_merge_block"]["name_merge_input"]["value"]

        # Parse the merge text
        merges, canonical_names, parse_errors = _parse_name_merges(
            merge_text, list(session.name_mapping.keys())
        )

        if parse_errors:
            error_text = "\n".join(f"  :warning: {e}" for e in parse_errors)
            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Name resolution notes:\n{error_text}",
            )

        # Store merges on the session
        session.name_merges = merges

        # Update name_mapping to only contain canonical names
        old_mapping = dict(session.name_mapping)
        session.name_mapping = {name: name for name in canonical_names}

        # Update groups: replace alias names with canonical names
        for project_name in session.groups:
            updated = []
            for member in session.groups[project_name]:
                canonical = merges.get(member, member)
                if canonical not in updated:
                    updated.append(canonical)
            session.groups[project_name] = updated

        save_session(session)

        # Build summary
        merge_lines = []
        if merges:
            # Group by canonical name
            from collections import defaultdict
            canonical_to_aliases = defaultdict(list)
            for alias, canonical in merges.items():
                canonical_to_aliases[canonical].append(alias)
            for canonical, aliases in canonical_to_aliases.items():
                merge_lines.append(
                    f"  :link: *{canonical}* ← {', '.join(aliases)}"
                )

        merge_summary = "\n".join(merge_lines) if merge_lines else "  No merges."

        # Show next steps
        next_elements = []
        if session.zoom_requests:
            next_elements.append({
                "type": "button",
                "text": {"type": "plain_text", "text": f"Review {len(session.zoom_requests)} 1-on-1 Request(s)"},
                "style": "primary",
                "action_id": "sched_review_zoom",
                "value": session.session_id,
            })
        next_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Assign Members to Projects"},
            "style": "primary" if not session.zoom_requests else None,
            "action_id": "sched_open_assignment",
            "value": session.session_id,
        })
        # Remove None style
        if next_elements[-1].get("style") is None:
            next_elements[-1].pop("style", None)
        next_elements.append({
            "type": "button",
            "text": {"type": "plain_text", "text": "Re-resolve Names"},
            "action_id": "sched_resolve_names",
            "value": session.session_id,
        })

        client.chat_postMessage(
            channel=session.dm_channel,
            text="Name resolution complete",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": (
                            f"*Name Resolution Complete*\n\n"
                            f"*{len(canonical_names)} unique respondents* "
                            f"(was {len(old_mapping)} before merges)\n\n"
                            f"*Merges:*\n{merge_summary}"
                        ),
                    },
                },
                {
                    "type": "actions",
                    "elements": next_elements,
                },
            ],
        )

    # ── Step 3c: Review :zoom: individual meeting requests ──────────────

    @app.action("sched_review_zoom")
    def handle_review_zoom(ack, body, client: WebClient, action):
        """Open modal for director to accept/deny individual meeting requests."""
        ack()

        session_id = action["value"]
        session = get_session(session_id)
        if not session or not session.zoom_requests:
            return

        try:
            client.views_open(
                trigger_id=body["trigger_id"],
                view=_build_zoom_review_modal(session),
            )
        except SlackApiError as e:
            logger.error(f"Error opening zoom review modal: {e}")
            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Error opening meeting request review: {e}",
            )

    @app.view("sched_zoom_review_submit")
    def handle_zoom_review_submit(ack, body, client: WebClient, view):
        """Process director's decisions on individual meeting requests."""
        ack()

        metadata = json.loads(view["private_metadata"])
        session_id = metadata["session_id"]
        session = get_session(session_id)
        if not session:
            return

        values = view["state"]["values"]

        accepted_meetings = []
        for i, req in enumerate(session.zoom_requests):
            # Accept/deny is in zoom_req_{i} block (section accessory)
            accept_block = values.get(f"zoom_req_{i}", {})
            accept_action = accept_block.get(f"zoom_accept_{i}", {})
            selected = accept_action.get("selected_option", {})
            is_accepted = selected.get("value", "deny") == "accept"

            # Duration is in zoom_dur_block_{i} block (actions element)
            dur_block = values.get(f"zoom_dur_block_{i}", {})
            dur_action = dur_block.get(f"zoom_dur_{i}", {})
            dur_selected = dur_action.get("selected_option", {})
            duration = float(dur_selected.get("value", "2")) if dur_selected else 2.0

            req["accepted"] = is_accepted
            req["duration_blocks"] = duration
            if is_accepted:
                accepted_meetings.append(req)

        session.zoom_requests = session.zoom_requests  # keep all for record
        save_session(session)

        # Add accepted meetings to session groups and durations.
        # Match Slack display names to When2Meet respondent names so the
        # algorithm can look up their availability correctly.
        respondent_names = list(session.name_mapping.keys())
        for req in accepted_meetings:
            slack_name = req["name"]
            # Match to a respondent name
            matched = _match_display_to_respondent(slack_name, respondent_names)
            respondent_name = matched or slack_name
            req["respondent_name"] = respondent_name

            meeting_name = f"{respondent_name} one-on-one"
            # Use PI canonical name + matched respondent name
            session.groups[meeting_name] = [respondent_name] + list(session.pi)
            session.preferred_durations[meeting_name] = req["duration_blocks"]
            session.project_emojis[meeting_name] = ":zoom:"
        save_session(session)

        # Build summary and show "Assign Members" button
        summary_lines = []
        if accepted_meetings:
            for req in accepted_meetings:
                dur = req["duration_blocks"]
                dur_min = int(dur) * 15 if dur == int(dur) else int(dur) * 15
                freq = "biweekly" if dur != int(dur) else "weekly"
                summary_lines.append(f"  :white_check_mark: {req['name']} — {dur_min}min {freq}")
        denied = [r for r in session.zoom_requests if not r.get("accepted")]
        for req in denied:
            summary_lines.append(f"  :x: {req['name']} — denied")

        summary_text = "\n".join(summary_lines) if summary_lines else "No requests."

        client.chat_postMessage(
            channel=session.dm_channel,
            text=f":zoom: 1-on-1 meeting decisions",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":zoom: *1-on-1 Meeting Decisions:*\n{summary_text}\n\n"
                                f"Now assign members to projects.",
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
                            "value": session.session_id,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Re-review 1-on-1s"},
                            "action_id": "sched_review_zoom",
                            "value": session.session_id,
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

        # Parse project assignments (deduplicate to prevent double-entries)
        for project_name in list(session.groups.keys()):
            block_id = f"proj_{_safe_id(project_name)}"
            action_id = f"assign_{_safe_id(project_name)}"
            selected = values.get(block_id, {}).get(action_id, {}).get("selected_options", [])
            seen = set()
            deduped = []
            for opt in selected:
                if opt["value"] not in seen:
                    seen.add(opt["value"])
                    deduped.append(opt["value"])
            session.groups[project_name] = deduped

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

        # Parse required external PIs per project
        session.required_members = {}
        for project_name in list(session.groups.keys()):
            if project_name == "Lab Meeting":
                continue
            block_id = f"reqpi_{_safe_id(project_name)}"
            action_id = f"reqpi_assign_{_safe_id(project_name)}"
            selected = values.get(block_id, {}).get(action_id, {}).get("selected_options", [])
            if selected:
                session.required_members[project_name] = [opt["value"] for opt in selected]

        # Auto-add PI to every project group (PI attends all meetings)
        for project_name in session.groups:
            for pi_name in session.pi:
                if pi_name not in session.groups[project_name]:
                    session.groups[project_name].append(pi_name)

        # Lab Meeting gets everyone not external
        all_assigned = set()
        for members in session.groups.values():
            all_assigned.update(members)
        all_assigned.update(session.pi)

        # If Lab Meeting exists and has only the PI, auto-populate with all respondents
        pi_set = set(session.pi)
        if "Lab Meeting" in session.groups:
            non_pi = [m for m in session.groups["Lab Meeting"] if m not in pi_set]
            if not non_pi:
                session.groups["Lab Meeting"] = list(session.pi) + [
                    n for n in respondent_names if n not in pi_set
                ]

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

        # Apply name merges (duplicate respondents → union of availability)
        if session.name_merges:
            availability = _apply_name_merges_to_availability(
                availability, session.name_merges
            )

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

        channel = session.survey_channel or _find_channel(client, "general") or session.dm_channel
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
                session.project_descriptions,
                session.project_channels,
            )

            # Create Google Calendar events if credentials are configured
            cal_summary = _create_calendar_events(client, session, schedule_df)

            done_text = f"Scheduling complete for {session.term}!"
            if cal_summary:
                done_text += f"\n\n{cal_summary}"

            client.chat_postMessage(
                channel=session.dm_channel,
                text=done_text,
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

    # ── Step 5b: Edit schedule ──────────────────────────────────────────

    @app.action("sched_edit_schedule")
    def handle_edit_schedule(ack, body, client: WebClient, action):
        """Open a modal to manually edit the proposed schedule."""
        ack()

        session_id = action["value"]
        session = get_session(session_id)
        if not session:
            return

        try:
            client.views_open(
                trigger_id=body["trigger_id"],
                view=_build_schedule_edit_modal(session),
            )
        except SlackApiError as e:
            logger.error(f"Error opening schedule edit modal: {e}")
            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Error opening schedule editor: {e}",
            )

    @app.view("sched_edit_schedule_submit")
    def handle_edit_schedule_submit(ack, body, client: WebClient, view):
        """Process the edited schedule and show updated review."""
        ack()

        metadata = json.loads(view["private_metadata"])
        session_id = metadata["session_id"]
        session = get_session(session_id)
        if not session:
            return

        values = view["state"]["values"]
        schedule_text = values["schedule_edit_block"]["schedule_edit_input"]["value"]

        # Parse the edited text back into scheduled data
        new_scheduled, parse_errors = _parse_edited_schedule(schedule_text, session)

        if parse_errors:
            error_text = "\n".join(f"  :warning: {e}" for e in parse_errors)
            client.chat_postMessage(
                channel=session.dm_channel,
                text=f"Some lines could not be parsed:\n{error_text}\n\n"
                     f"Valid changes were applied.",
            )

        # Rebuild the schedule_df_data from the new schedule
        import pandas as pd
        if new_scheduled:
            # Keep _schedule_df_data key for the approve step
            df_data = []
            day_order = {d: i for i, d in enumerate(
                ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            )}
            for meeting_name, details in new_scheduled.items():
                if meeting_name.startswith("_"):
                    continue
                times = details["times"]
                last_time = times[-1] if times else "00:00"
                # Calculate end time
                try:
                    from datetime import datetime as dt, timedelta
                    t = dt.strptime(str(last_time)[:5], "%H:%M")
                    end_t = (t + timedelta(minutes=15)).strftime("%H:%M:%S")
                except Exception:
                    end_t = last_time

                freq = "Biweekly" if details.get("is_biweekly") else "Weekly"
                df_data.append({
                    "Meeting": meeting_name,
                    "Day": details["day"],
                    "Start Time": str(times[0]) if times else "",
                    "End Time": str(end_t),
                    "Duration (min)": len(times) * 15,
                    "Frequency": freq,
                    "Senior Availability": "—",
                    "Total Available": "—",
                })
            df_data.sort(key=lambda r: (day_order.get(r["Day"], 99), r["Start Time"]))
            new_scheduled["_schedule_df_data"] = df_data

        session.scheduled = new_scheduled

        # Rebuild slack summary
        schedule_df = pd.DataFrame(new_scheduled.get("_schedule_df_data", []))
        if not schedule_df.empty and "Meeting" in schedule_df.columns:
            schedule_df = schedule_df.set_index("Meeting")

        slack_summary = format_schedule_for_slack(
            new_scheduled, schedule_df, session.project_emojis,
        )
        session.schedule_summary = slack_summary
        save_session(session)

        # Show updated review
        blocks = [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": f"Edited Schedule: {session.term}"},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": slack_summary},
            },
            {"type": "divider"},
            {
                "type": "actions",
                "elements": [
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Edit Schedule"},
                        "style": "primary",
                        "action_id": "sched_edit_schedule",
                        "value": session.session_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Approve & Post"},
                        "action_id": "sched_approve_schedule",
                        "value": session.session_id,
                    },
                    {
                        "type": "button",
                        "text": {"type": "plain_text", "text": "Re-assign Members"},
                        "action_id": "sched_reassign",
                        "value": session.session_id,
                    },
                ],
            },
        ]

        client.chat_postMessage(
            channel=session.dm_channel,
            text=f"Updated schedule for {session.term}",
            blocks=blocks,
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

    # Filter out projects with 0 non-PI members.
    # "Office hours" style meetings (only the PI) are kept as free blocks.
    pi_set = set(session.pi)
    active_groups = {}
    empty_projects = []
    for name, members in session.groups.items():
        non_pi_members = [m for m in members if m not in pi_set]
        is_office_hours = "office hours" in name.lower()
        if non_pi_members or is_office_hours:
            active_groups[name] = members
        else:
            empty_projects.append(name)
    if empty_projects:
        logger.info(f"Skipping PI-only projects: {empty_projects}")

    active_durations = {
        name: dur for name, dur in session.preferred_durations.items()
        if name in active_groups
    }

    try:
        # Build required_members for active groups only
        active_required = {
            name: members for name, members in session.required_members.items()
            if name in active_groups
        }

        scheduled, schedule_df = find_best_meeting_times(
            availability=availability,
            PI=session.pi,
            senior=session.senior,
            external=session.external,
            groups=active_groups,
            preferred_durations=active_durations,
            required_members=active_required,
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

    unscheduled = [name for name in active_groups if name not in scheduled]

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

    if empty_projects:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":wastebasket: *Removed (0 members):* {', '.join(empty_projects)}",
            },
        })

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
                    "text": {"type": "plain_text", "text": "Edit Schedule"},
                    "style": "primary",
                    "action_id": "sched_edit_schedule",
                    "value": session.session_id,
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Approve & Post"},
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
        "One project per line:\n"
        "Name | duration | emoji | description | #ch1, #ch2\n\n"
        "Duration = number of 15-min blocks (4=60min). "
        "Add .5 for biweekly (2.5 = biweekly 30min).\n"
        "Description and channels are optional but recommended for new projects."
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
                "label": {"type": "plain_text", "text": "Projects (name | dur | emoji | desc | channels)"},
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

    blocks.append({"type": "divider"})

    # Required external PIs — their availability is a hard constraint
    # One multi-select per project to mark which external members are required
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": (
                "*Required External PIs*\n"
                "_Select external collaborators who MUST be available at their "
                "assigned meeting times (their availability filters the schedule "
                "like the PI's does). Only select people for meetings they attend._"
            ),
        },
    })

    for project_name in session.groups:
        if project_name == "Lab Meeting":
            continue  # External PIs skip lab meeting anyway
        block_id = f"reqpi_{_safe_id(project_name)}"
        action_id = f"reqpi_assign_{_safe_id(project_name)}"
        emoji = session.project_emojis.get(project_name, "")
        label = f"{emoji} {project_name}" if emoji else project_name

        # Pre-select from existing required_members
        initial = None
        existing_req = session.required_members.get(project_name, [])
        if existing_req:
            initial = [opt for opt in member_options if opt["value"] in existing_req]

        element = {
            "type": "multi_static_select",
            "action_id": action_id,
            "placeholder": {"type": "plain_text", "text": "None (optional)"},
            "options": member_options,
        }
        if initial:
            element["initial_options"] = initial

        blocks.append({
            "type": "input",
            "block_id": block_id,
            "optional": True,
            "element": element,
            "label": {"type": "plain_text", "text": f"Required for: {label}"[:75]},
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

def _parse_projects(text: str) -> tuple[list, dict, dict, dict, dict]:
    """
    Parse the projects text block into names, durations, emojis,
    descriptions, and channels.

    Format per line: "Project Name | duration | emoji | description | #ch1, #ch2"
    Last two fields are optional.

    Returns (project_names, durations, emojis, descriptions, channels).
    """
    project_names = []
    durations = {}
    emojis = {}
    descriptions = {}
    channels = {}

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

        if len(parts) > 3 and parts[3]:
            descriptions[name] = parts[3].strip()

        if len(parts) > 4 and parts[4]:
            channels[name] = [c.strip() for c in parts[4].split(",") if c.strip()]

    return project_names, durations, emojis, descriptions, channels


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
    """Find a channel ID by name, joining the channel if needed."""
    try:
        cursor = None
        while True:
            kwargs = {"types": "public_channel", "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            result = client.conversations_list(**kwargs)
            for ch in result["channels"]:
                if ch["name"] == channel_name:
                    channel_id = ch["id"]
                    # Join the channel so the bot can post
                    if not ch.get("is_member"):
                        try:
                            client.conversations_join(channel=channel_id)
                            logger.info(f"Joined #{channel_name}")
                        except SlackApiError as e:
                            logger.warning(f"Could not join #{channel_name}: {e}")
                    return channel_id
            cursor = result.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
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


def _get_zoom_reactors(client: WebClient, session) -> list:
    """
    Fetch :zoom: reactions from the survey message.
    Returns list of (user_id, display_name) tuples, excluding the bot and director.
    """
    if not session.survey_message_ts or not session.survey_channel:
        return []

    try:
        result = client.reactions_get(
            channel=session.survey_channel,
            timestamp=session.survey_message_ts,
        )
    except SlackApiError as e:
        logger.error(f"Error fetching reactions: {e}")
        return []

    message = result.get("message", {})
    reactions = message.get("reactions", [])

    zoom_user_ids = []
    for reaction in reactions:
        if reaction["name"] == "zoom":
            zoom_user_ids = reaction.get("users", [])
            break

    if not zoom_user_ids:
        return []

    # Resolve user IDs to display names, skip the director
    reactors = []
    for uid in zoom_user_ids:
        if uid == session.initiated_by:
            continue
        try:
            user_info = client.users_info(user=uid)
            profile = user_info["user"]["profile"]
            name = profile.get("real_name") or profile.get("display_name") or uid
            reactors.append((uid, name))
        except SlackApiError:
            reactors.append((uid, uid))

    return reactors


def _build_zoom_review_modal(session) -> dict:
    """
    Build a modal for the director to accept/deny individual meeting requests
    and set duration for each.
    """
    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    ":zoom: *Individual Meeting Requests*\n\n"
                    "The following people reacted with :zoom: to request a "
                    "recurring one-on-one meeting. For each, choose whether to "
                    "accept and set the duration."
                ),
            },
        },
        {"type": "divider"},
    ]

    duration_options = [
        {"text": {"type": "plain_text", "text": "15 min"}, "value": "1"},
        {"text": {"type": "plain_text", "text": "30 min"}, "value": "2"},
        {"text": {"type": "plain_text", "text": "30 min biweekly"}, "value": "1.5"},
        {"text": {"type": "plain_text", "text": "45 min"}, "value": "3"},
        {"text": {"type": "plain_text", "text": "60 min"}, "value": "4"},
        {"text": {"type": "plain_text", "text": "60 min biweekly"}, "value": "2.5"},
    ]

    for i, req in enumerate(session.zoom_requests):
        block_id = f"zoom_req_{i}"
        blocks.append({
            "type": "section",
            "block_id": block_id,
            "text": {
                "type": "mrkdwn",
                "text": f"*{req['name']}*",
            },
            "accessory": {
                "type": "static_select",
                "action_id": f"zoom_accept_{i}",
                "placeholder": {"type": "plain_text", "text": "Accept/Deny"},
                "initial_option": {
                    "text": {"type": "plain_text", "text": "Accept"},
                    "value": "accept",
                },
                "options": [
                    {"text": {"type": "plain_text", "text": "Accept"}, "value": "accept"},
                    {"text": {"type": "plain_text", "text": "Deny"}, "value": "deny"},
                ],
            },
        })
        blocks.append({
            "type": "actions",
            "block_id": f"zoom_dur_block_{i}",
            "elements": [
                {
                    "type": "static_select",
                    "action_id": f"zoom_dur_{i}",
                    "placeholder": {"type": "plain_text", "text": "Duration"},
                    "initial_option": {
                        "text": {"type": "plain_text", "text": "30 min"},
                        "value": "2",
                    },
                    "options": duration_options,
                },
            ],
        })

    metadata = json.dumps({"session_id": session.session_id})

    return {
        "type": "modal",
        "callback_id": "sched_zoom_review_submit",
        "private_metadata": metadata,
        "title": {"type": "plain_text", "text": "Meeting Requests"},
        "submit": {"type": "plain_text", "text": "Save Decisions"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": blocks,
    }


def _auto_match_pi_names(pi_names: list, respondent_names: list) -> dict:
    """
    Auto-detect which respondent corresponds to each PI name.

    If the PI name (e.g., "Jeremy") doesn't exactly match a respondent but
    a respondent starts with that name (e.g., "Jeremy M"), create a merge
    mapping: "Jeremy M" → "Jeremy" so the algorithm uses the right column.

    Returns dict of respondent_name -> pi_name (merge mapping).
    """
    merges = {}
    for pi in pi_names:
        pi_lower = pi.lower().strip()
        # Check exact match first
        if any(r.lower() == pi_lower for r in respondent_names):
            continue  # Already matches, no merge needed

        # Find respondents whose first name matches the PI name
        candidates = []
        for r in respondent_names:
            r_first = r.lower().strip().split()[0]
            if r_first == pi_lower or r.lower().startswith(pi_lower):
                candidates.append(r)

        if len(candidates) == 1:
            merges[candidates[0]] = pi
            logger.info(f"Auto-matched PI '{pi}' to respondent '{candidates[0]}'")
        elif len(candidates) > 1:
            # Multiple matches — pick the one most likely (shortest name or exact first-name match)
            exact_first = [c for c in candidates if c.lower().split()[0] == pi_lower]
            if len(exact_first) == 1:
                merges[exact_first[0]] = pi
                logger.info(f"Auto-matched PI '{pi}' to respondent '{exact_first[0]}' (from {candidates})")
            else:
                logger.warning(
                    f"Multiple respondents match PI '{pi}': {candidates}. "
                    f"Use 'Resolve Names' to pick the right one."
                )

    return merges


def _detect_potential_duplicates(names: list) -> list[list[str]]:
    """
    Detect groups of respondent names that might be the same person.
    Groups by shared first name (case-insensitive).
    Returns list of groups (each group is a list of 2+ names).
    """
    from collections import defaultdict
    first_name_groups = defaultdict(list)
    for name in names:
        first = name.strip().split()[0].lower() if name.strip() else name.lower()
        first_name_groups[first].append(name)

    # Also check for prefix matches (e.g., "Dan" / "Daniel")
    firsts = list(first_name_groups.keys())
    merged_groups = {}
    for first in firsts:
        merged_groups[first] = set(first_name_groups[first])

    for i, f1 in enumerate(firsts):
        for f2 in firsts[i + 1:]:
            if f1.startswith(f2) or f2.startswith(f1):
                # Merge these groups
                shorter = min(f1, f2, key=len)
                longer = max(f1, f2, key=len)
                if shorter in merged_groups and longer in merged_groups:
                    merged_groups[shorter] |= merged_groups[longer]
                    del merged_groups[longer]

    return [sorted(group) for group in merged_groups.values() if len(group) > 1]


def _build_name_resolution_modal(session) -> dict:
    """
    Build a modal for resolving duplicate respondent names.

    Format per line:
    - Plain name = keep as-is
    - "AliasName → CanonicalName" = merge alias into canonical
    - Lines starting with # are comments

    Auto-populates with detected duplicates as merge suggestions.
    """
    respondent_names = sorted(session.name_mapping.keys())
    potential_dupes = _detect_potential_duplicates(respondent_names)

    # Build existing merges (for re-resolution)
    existing_merges = session.name_merges or {}

    lines = []
    already_listed = set()

    # Show potential duplicate groups first with merge suggestions
    if potential_dupes:
        lines.append("# Potential duplicates — use → to merge (or leave separate):")
        for group in potential_dupes:
            canonical = group[0]  # Default: first alphabetically is canonical
            # Check if there's an existing merge for this group
            for name in group:
                if name in existing_merges:
                    canonical = existing_merges[name]
                    break
            for name in group:
                if name in existing_merges:
                    lines.append(f"{name} → {existing_merges[name]}")
                elif name == canonical and len(group) > 1:
                    lines.append(f"{name}")
                else:
                    lines.append(f"{name}")
                already_listed.add(name)
            lines.append("")  # Blank line between groups

    # List remaining names
    if already_listed:
        lines.append("# Other respondents:")
    for name in respondent_names:
        if name not in already_listed:
            if name in existing_merges:
                lines.append(f"{name} → {existing_merges[name]}")
            else:
                lines.append(name)
            already_listed.add(name)

    merge_text = "\n".join(lines)

    return {
        "type": "modal",
        "callback_id": "sched_resolve_names_submit",
        "private_metadata": json.dumps({"session_id": session.session_id}),
        "title": {"type": "plain_text", "text": "Resolve Names"},
        "submit": {"type": "plain_text", "text": "Apply"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Resolve duplicate or ambiguous respondent names.*\n\n"
                        "• To merge duplicates: `Paxton → Paxton F`\n"
                        "  _(merges Paxton's availability into Paxton F)_\n"
                        "• To keep names separate: leave them on their own lines\n"
                        "• To remove someone: delete their line\n"
                        "• Lines starting with `#` are comments"
                    ),
                },
            },
            {
                "type": "input",
                "block_id": "name_merge_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "name_merge_input",
                    "multiline": True,
                    "initial_value": merge_text,
                },
                "label": {"type": "plain_text", "text": "Respondent Names"},
            },
        ],
    }


def _parse_name_merges(text: str, original_names: list) -> tuple[dict, list, list]:
    """
    Parse the name resolution text.

    Returns (merges_dict, canonical_names_list, errors_list).
    merges_dict: alias_name -> canonical_name
    canonical_names_list: all unique canonical names (in order)
    """
    merges = {}
    canonical_names = []
    errors = []
    original_set = {n.lower(): n for n in original_names}

    for line_num, line in enumerate(text.strip().split("\n"), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        # Check for merge syntax: "Alias → Canonical" or "Alias -> Canonical"
        merge_match = re.split(r'\s*(?:→|->)\s*', line, maxsplit=1)
        if len(merge_match) == 2:
            alias = merge_match[0].strip()
            canonical = merge_match[1].strip()
            if not alias or not canonical:
                errors.append(f"Line {line_num}: empty name in merge")
                continue
            if alias == canonical:
                # Not a merge, just a name
                if canonical not in canonical_names:
                    canonical_names.append(canonical)
                continue
            merges[alias] = canonical
            # Ensure canonical is in the list
            if canonical not in canonical_names:
                canonical_names.append(canonical)
        else:
            # Plain name — keep as canonical
            name = line.strip()
            if name and name not in canonical_names:
                canonical_names.append(name)

    return merges, canonical_names, errors


def _apply_name_merges_to_availability(availability, merges: dict):
    """
    Apply name merges to an availability DataFrame.
    For each alias → canonical merge, OR the alias column into the canonical
    column, then drop the alias column.

    Parameters
    ----------
    availability : pd.DataFrame
        MultiIndex (Day, Time) -> one column per person.
    merges : dict
        alias_name -> canonical_name

    Returns
    -------
    pd.DataFrame with merged columns.
    """
    import pandas as pd

    if not merges:
        return availability

    df = availability.copy()

    for alias, canonical in merges.items():
        if alias not in df.columns:
            continue
        if canonical in df.columns:
            # OR: available if either entry says available
            df[canonical] = df[[canonical, alias]].max(axis=1)
        else:
            # Canonical not present yet — just rename
            df = df.rename(columns={alias: canonical})

    # Drop alias columns that were merged (not renamed)
    cols_to_drop = [alias for alias in merges if alias in df.columns and merges[alias] in df.columns]
    df = df.drop(columns=cols_to_drop, errors="ignore")

    return df


def _build_schedule_edit_modal(session) -> dict:
    """
    Build a modal with the current schedule as editable text.
    One meeting per line: "Meeting Name | Day | HH:MM | HH:MM | weekly/biweekly"
    """
    lines = []
    scheduled = session.scheduled or {}

    day_order = {d: i for i, d in enumerate(
        ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    )}

    # Build from _schedule_df_data for consistent display
    df_data = scheduled.get("_schedule_df_data", [])
    if df_data:
        sorted_data = sorted(df_data, key=lambda r: (day_order.get(r.get("Day", ""), 99), r.get("Start Time", "")))
        for row in sorted_data:
            name = row.get("Meeting", "")
            day = row.get("Day", "")
            start = str(row.get("Start Time", ""))[:5]
            end = str(row.get("End Time", ""))[:5]
            freq = "biweekly" if "Biweekly" in str(row.get("Frequency", "")) else "weekly"
            lines.append(f"{name} | {day} | {start} | {end} | {freq}")
    else:
        # Fallback: build from scheduled dict
        entries = []
        for meeting_name, details in scheduled.items():
            if meeting_name.startswith("_"):
                continue
            times = details.get("times", [])
            if not times:
                continue
            day = details.get("day", "")
            start = str(times[0])[:5]
            from datetime import datetime as dt, timedelta
            try:
                t = dt.strptime(str(times[-1])[:5], "%H:%M")
                end = (t + timedelta(minutes=15)).strftime("%H:%M")
            except Exception:
                end = str(times[-1])[:5]
            freq = "biweekly" if details.get("is_biweekly") else "weekly"
            entries.append((day_order.get(day, 99), start, f"{meeting_name} | {day} | {start} | {end} | {freq}"))
        entries.sort()
        lines = [e[2] for e in entries]

    schedule_text = "\n".join(lines)

    return {
        "type": "modal",
        "callback_id": "sched_edit_schedule_submit",
        "private_metadata": json.dumps({"session_id": session.session_id}),
        "title": {"type": "plain_text", "text": "Edit Schedule"},
        "submit": {"type": "plain_text", "text": "Update Schedule"},
        "close": {"type": "plain_text", "text": "Cancel"},
        "blocks": [
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": (
                        "*Edit the schedule below.*\n"
                        "One meeting per line:\n"
                        "`Meeting Name | Day | Start | End | weekly/biweekly`\n\n"
                        "• Change times or days by editing the line\n"
                        "• Delete a line to remove a meeting\n"
                        "• Add a new line to add a meeting"
                    ),
                },
            },
            {
                "type": "input",
                "block_id": "schedule_edit_block",
                "element": {
                    "type": "plain_text_input",
                    "action_id": "schedule_edit_input",
                    "multiline": True,
                    "initial_value": schedule_text,
                },
                "label": {"type": "plain_text", "text": "Schedule"},
            },
        ],
    }


def _parse_edited_schedule(text: str, session) -> tuple[dict, list]:
    """
    Parse user-edited schedule text back into a scheduled dict.

    Each line: "Meeting Name | Day | HH:MM | HH:MM | weekly/biweekly"
    Returns (scheduled_dict, list_of_errors).
    """
    scheduled = {}
    errors = []

    valid_days = {"Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"}

    for line_num, line in enumerate(text.strip().split("\n"), 1):
        line = line.strip()
        if not line:
            continue

        parts = [p.strip() for p in line.split("|")]
        if len(parts) < 4:
            errors.append(f"Line {line_num}: expected at least 4 fields (name|day|start|end), got {len(parts)}")
            continue

        meeting_name = parts[0]
        day = parts[1]
        start_str = parts[2]
        end_str = parts[3]
        freq = parts[4].lower().strip() if len(parts) > 4 else "weekly"

        if not meeting_name:
            errors.append(f"Line {line_num}: empty meeting name")
            continue

        # Validate day
        day_match = [d for d in valid_days if d.lower() == day.lower()]
        if not day_match:
            errors.append(f"Line {line_num}: invalid day '{day}'")
            continue
        day = day_match[0]

        # Validate times
        from datetime import datetime as dt, timedelta
        try:
            start_time = dt.strptime(start_str[:5], "%H:%M")
            end_time = dt.strptime(end_str[:5], "%H:%M")
        except ValueError:
            errors.append(f"Line {line_num}: invalid time format (use HH:MM)")
            continue

        if end_time <= start_time:
            errors.append(f"Line {line_num}: end time must be after start time")
            continue

        # Build time slots (15-min blocks)
        times = []
        current = start_time
        while current < end_time:
            times.append(current.strftime("%H:%M:%S"))
            current += timedelta(minutes=15)

        is_biweekly = "biweekly" in freq or "bi-weekly" in freq

        scheduled[meeting_name] = {
            "day": day,
            "times": times,
            "pi_available": len(session.pi),
            "senior_available": 0,
            "other_available": 0,
            "total_group_size": len(session.groups.get(meeting_name, [])),
            "is_biweekly": is_biweekly,
            "shares_slot": False,
            "shares_with": None,
        }

    return scheduled, errors


def _auto_populate_from_reactions(client: WebClient, session, respondent_names: list):
    """
    Fetch emoji reactions from the survey message and use them to auto-populate
    project assignments. Maps Slack user IDs (from reactions) to When2Meet
    respondent names via fuzzy matching on display names.
    """
    if not session.survey_message_ts or not session.survey_channel:
        return

    try:
        result = client.reactions_get(
            channel=session.survey_channel,
            timestamp=session.survey_message_ts,
        )
    except SlackApiError as e:
        logger.error(f"Error fetching reactions for auto-populate: {e}")
        return

    message = result.get("message", {})
    reactions = message.get("reactions", [])

    if not reactions:
        return

    # Build reverse map: emoji name -> project name
    emoji_to_project = {}
    for project_name, emoji in session.project_emojis.items():
        # Strip colons: ":octopus:" -> "octopus"
        clean_emoji = emoji.strip(":")
        if clean_emoji:
            emoji_to_project[clean_emoji] = project_name

    # Build a user ID -> respondent name map by resolving reactor profiles.
    # Track which respondent names have already been claimed to prevent
    # multiple Slack users from fuzzy-matching to the same respondent.
    uid_to_respondent = {}
    claimed_respondents = set()

    for reaction in reactions:
        emoji_name = reaction["name"]
        if emoji_name not in emoji_to_project:
            continue

        project_name = emoji_to_project[emoji_name]
        user_ids = reaction.get("users", [])

        for uid in user_ids:
            # Resolve display name if not cached
            if uid not in uid_to_respondent:
                try:
                    user_info = client.users_info(user=uid)
                    profile = user_info["user"]["profile"]
                    display = profile.get("real_name") or profile.get("display_name") or ""
                    # Fuzzy match to respondent names, excluding already-claimed ones
                    available = [n for n in respondent_names if n not in claimed_respondents]
                    matched = _match_display_to_respondent(display, available)
                    uid_to_respondent[uid] = matched  # None if no match
                    if matched:
                        claimed_respondents.add(matched)
                except SlackApiError:
                    uid_to_respondent[uid] = None

            matched_name = uid_to_respondent[uid]
            if matched_name and matched_name not in session.groups.get(project_name, []):
                if project_name in session.groups:
                    session.groups[project_name].append(matched_name)

    # Final deduplication pass on all groups
    for project_name in session.groups:
        seen = []
        for member in session.groups[project_name]:
            if member not in seen:
                seen.append(member)
        session.groups[project_name] = seen


def _match_display_to_respondent(display_name: str, respondent_names: list):
    """
    Match a Slack display name to a When2Meet respondent name.
    Tries exact match, first-name match, then fuzzy match.
    Returns the matched respondent name or None.
    """
    if not display_name:
        return None

    display_lower = display_name.lower().strip()

    # Exact match
    for name in respondent_names:
        if name.lower() == display_lower:
            return name

    # First name match (respondents typically use "FirstName LastInitial")
    display_first = display_lower.split()[0] if display_lower else ""
    for name in respondent_names:
        resp_first = name.lower().split()[0] if name else ""
        if display_first and resp_first and display_first == resp_first:
            return name

    # Fuzzy match
    try:
        from rapidfuzz import fuzz, process
        result = process.extractOne(
            display_name, respondent_names,
            scorer=fuzz.token_sort_ratio,
            score_cutoff=60,
        )
        if result:
            return result[0]
    except ImportError:
        pass

    return None


def _auto_populate_senior(client: WebClient, session, respondent_names: list):
    """
    Look up members of #senior-lab-stuff channel and cross-reference with
    respondent names to auto-populate the senior members list.
    """
    try:
        # Find the channel ID for #senior-lab-stuff
        channel_id = None
        cursor = None
        while True:
            kwargs = {"types": "public_channel,private_channel", "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            result = client.conversations_list(**kwargs)
            for ch in result["channels"]:
                if ch["name"] == "senior-lab-stuff":
                    channel_id = ch["id"]
                    break
            if channel_id:
                break
            cursor = result.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break

        if not channel_id:
            logger.info("Could not find #senior-lab-stuff channel for auto-populating seniors")
            return

        # Get channel members
        members_result = client.conversations_members(channel=channel_id, limit=200)
        member_ids = members_result.get("members", [])

        # Resolve each member's display name and match to respondents
        senior_names = []
        for uid in member_ids:
            try:
                user_info = client.users_info(user=uid)
                profile = user_info["user"]["profile"]
                display = profile.get("real_name") or profile.get("display_name") or ""
                matched = _match_display_to_respondent(display, respondent_names)
                if matched and matched not in senior_names:
                    senior_names.append(matched)
            except SlackApiError:
                continue

        # Remove PI from senior list (they're weighted separately)
        senior_names = [n for n in senior_names if n not in session.pi]

        if senior_names:
            session.senior = senior_names
            logger.info(f"Auto-populated senior members from #senior-lab-stuff: {senior_names}")

    except SlackApiError as e:
        logger.error(f"Error auto-populating senior members: {e}")


def _create_calendar_events(client: WebClient, session, schedule_df) -> str:
    """
    Create Google Calendar recurring events for all scheduled meetings.
    Returns a summary string, or empty string if calendar is not configured.
    """
    import os
    credentials_file = os.environ.get("GOOGLE_CREDENTIALS_FILE")
    if not credentials_file:
        logger.info("GOOGLE_CREDENTIALS_FILE not set — skipping calendar event creation")
        return ""

    calendar_id_env = os.environ.get("GOOGLE_CALENDAR_CONTEXTUAL_DYNAMICS_LAB")
    if not calendar_id_env:
        logger.info("GOOGLE_CALENDAR_CONTEXTUAL_DYNAMICS_LAB not set — skipping events")
        return ""

    if not session.term_start or not session.term_end:
        logger.warning("Term start/end dates missing — skipping calendar events")
        return ""

    try:
        from ..services.calendar_service import CalendarService
        cal = CalendarService(credentials_file)

        results = cal.create_schedule_events(
            calendar_id=calendar_id_env,
            schedule_df=schedule_df,
            groups=session.groups,
            term_start=session.term_start,
            term_end=session.term_end,
        )

        created = [r for r in results if r["success"]]
        failed = [r for r in results if not r["success"]]

        # Store event IDs on session for potential rollback
        session.calendar_event_ids = [r["event_id"] for r in created if r["event_id"]]

        lines = []
        if created:
            lines.append(f":calendar: Created {len(created)} calendar events.")
        if failed:
            lines.append(f":warning: Failed to create {len(failed)} events:")
            for r in failed:
                lines.append(f"  • {r['meeting_name']}: {r['error']}")

        return "\n".join(lines)

    except Exception as e:
        logger.error(f"Error creating calendar events: {e}")
        return f":warning: Calendar event creation failed: {e}"


def _friendly_date(iso_date: str) -> str:
    """Convert "2026-03-30" to "Monday, March 30"."""
    try:
        d = date.fromisoformat(iso_date)
        return d.strftime("%A, %B %-d")
    except (ValueError, TypeError):
        return iso_date


def _friday_before(iso_date: str) -> str:
    """
    Find the Friday before a term start date.
    Returns a friendly string like "Friday, March 27".
    """
    try:
        d = date.fromisoformat(iso_date)
        # Find the Friday before (or on) the day before term starts
        day_before = d - timedelta(days=1)
        days_since_friday = (day_before.weekday() - 4) % 7
        friday = day_before - timedelta(days=days_since_friday)
        return friday.strftime("Friday, %B %-d")
    except (ValueError, TypeError):
        return "Friday before the term starts"
