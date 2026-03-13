"""
When2Meet integration service.

Handles creating surveys and scraping responses.
"""

import logging
import re
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

import requests
import pandas as pd

logger = logging.getLogger(__name__)

# When2Meet base URL
BASE_URL = "https://www.when2meet.com"

# Default survey hours: 9 AM to 5 PM, Monday-Friday
DEFAULT_START_HOUR = 9
DEFAULT_END_HOUR = 17
DEFAULT_TIMEZONE = "America/New_York"


class When2MeetService:
    """Service for creating and scraping When2Meet surveys."""

    def __init__(self, timezone_str: str = DEFAULT_TIMEZONE):
        self.timezone_str = timezone_str

    def create_survey(self, name: str, days: list[str] = None,
                      start_hour: int = DEFAULT_START_HOUR,
                      end_hour: int = DEFAULT_END_HOUR) -> str:
        """
        Create a new When2Meet survey via HTTP POST.

        Args:
            name: Survey title (e.g., "CDL Spring 2026 Availability")
            days: List of date strings in MM/DD/YYYY format. If None, uses next Mon-Fri.
            start_hour: Start hour (0-23)
            end_hour: End hour (0-23)

        Returns:
            Full URL of the created survey
        """
        if days is None:
            days = self._next_weekdays()

        # When2Meet expects dates as pipe-separated list of Unix timestamps
        # for midnight of each day in the survey timezone
        tz = ZoneInfo(self.timezone_str)
        date_timestamps = []
        for day_str in days:
            dt = datetime.strptime(day_str, "%m/%d/%Y")
            dt_tz = dt.replace(tzinfo=tz)
            date_timestamps.append(str(int(dt_tz.timestamp())))

        data = {
            "NewEventName": name,
            "DateTypes": "SpecificDates",
            "PossibleDates": "|".join(date_timestamps),
            "NoEarlierThan": start_hour,
            "NoLaterThan": end_hour,
            "TimeZone": self.timezone_str,
        }

        logger.info(f"Creating When2Meet survey: {name} ({len(days)} days, {start_hour}-{end_hour})")

        resp = requests.post(f"{BASE_URL}/SaveNewEvent.php", data=data, timeout=30,
                             allow_redirects=False)

        # When2Meet redirects to the new survey URL
        if resp.status_code in (301, 302):
            location = resp.headers.get("Location", "")
            if location:
                url = f"{BASE_URL}/{location}" if not location.startswith("http") else location
                logger.info(f"Created When2Meet survey: {url}")
                return url

        # Fallback: parse from response body (JS redirect or link)
        if resp.status_code == 200:
            # Try window.location JS redirect: window.location='./?35550844-wQkFY'
            match = re.search(r"window\.location\s*=\s*['\"]\.(/\?[\w-]+)['\"]", resp.text)
            if not match:
                # Try href link
                match = re.search(r'href="(/\?[\w-]+)"', resp.text)
            if match:
                url = f"{BASE_URL}{match.group(1)}"
                logger.info(f"Created When2Meet survey: {url}")
                return url

        raise RuntimeError(
            f"Failed to create When2Meet survey (status {resp.status_code}). "
            f"Response: {resp.text[:200]}"
        )

    def parse_responses(self, url: str) -> pd.DataFrame:
        """
        Parse a When2Meet survey into a tidy DataFrame.

        Args:
            url: When2Meet survey URL

        Returns:
            DataFrame with (Day, Time) MultiIndex, one column per respondent (0/1)
        """
        r = requests.get(url, timeout=30)
        r.raise_for_status()
        html = r.text

        # Extract timezone
        tz_match = re.search(r'timezone\s*=\s*"([^"]+)"', html)
        event_tz = tz_match.group(1) if tz_match else "UTC"

        # Extract people: PeopleNames[IDX] = 'Name'; PeopleIDs[IDX] = 12345;
        people = re.findall(
            r"PeopleNames\[\d+\]\s*=\s*'([^']+)';\s*PeopleIDs\[\d+\]\s*=\s*(\d+);",
            html
        )
        people_names = [name for name, _ in people]
        people_ids = [int(pid) for _, pid in people]

        # Extract TimeOfSlot: TimeOfSlot[IDX]=UNIX_TIMESTAMP;
        time_pairs = re.findall(r"TimeOfSlot\[(\d+)\]\s*=\s*(\d+);", html)
        slot_to_unixtime = {int(idx): int(ts) for idx, ts in time_pairs}

        # Extract availability: AvailableAtSlot[SLOT].push(PERSON_ID);
        avail_pairs = re.findall(
            r"AvailableAtSlot\[(\d+)\]\.push\((\d+)\);", html
        )
        slot_avail = defaultdict(set)
        for s_idx, p_id in avail_pairs:
            slot_avail[int(s_idx)].add(int(p_id))

        # Normalize names (deduplicate)
        seen = {}
        norm_names = []
        for n in people_names:
            base = n.strip()
            if base not in seen:
                seen[base] = 0
                norm_names.append(base)

        # Build rows sorted by timestamp
        tz = ZoneInfo(event_tz)
        rows = []
        sorted_slots = sorted(slot_to_unixtime.items(), key=lambda x: x[1])

        for slot_idx, ts in sorted_slots:
            dt_local = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(tz)
            day = dt_local.strftime("%A")
            time_str = dt_local.strftime("%H:%M:%S")

            avail_set = slot_avail.get(slot_idx, set())
            row = {"Day": day, "Time": time_str}
            for pid, cname in zip(people_ids, norm_names):
                row[cname] = 1 if pid in avail_set else 0
            rows.append(row)

        df = pd.DataFrame(rows)

        if df.empty:
            base_cols = ["Day", "Time"] + norm_names
            return pd.DataFrame(columns=base_cols)

        df.set_index(["Day", "Time"], inplace=True)

        logger.info(f"Parsed When2Meet: {len(norm_names)} respondents, {len(rows)} time slots")
        return df

    def get_respondent_names(self, url: str) -> list[str]:
        """Get just the list of respondent names from a When2Meet survey."""
        r = requests.get(url, timeout=30)
        r.raise_for_status()

        people = re.findall(
            r"PeopleNames\[\d+\]\s*=\s*'([^']+)';", r.text
        )
        seen = set()
        names = []
        for name in people:
            base = name.strip()
            if base not in seen:
                seen.add(base)
                names.append(base)
        return names

    def _next_weekdays(self) -> list[str]:
        """Get MM/DD/YYYY strings for next Monday-Friday."""
        today = datetime.now()
        # Find next Monday
        days_until_monday = (7 - today.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        monday = today + timedelta(days=days_until_monday)

        return [
            (monday + timedelta(days=i)).strftime("%m/%d/%Y")
            for i in range(5)
        ]
