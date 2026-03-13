"""
Tests for the scheduling bot components.

Tests the scheduling model, storage, name matching, project parsing,
term derivation, scheduling algorithm, and When2Meet scraping.
"""

import json
import tempfile
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from cdl_bot.models.scheduling_session import (
    SchedulingSession, SchedulingStatus,
)
from cdl_bot.scheduling_storage import SchedulingStorage
from cdl_bot.handlers.schedule import (
    _derive_term, _parse_projects, _fuzzy_match_names, _format_config_summary,
)
from cdl_bot.services.scheduling_service import (
    find_best_meeting_times, format_schedule_for_slack, format_announcement,
)
from cdl_bot.services.when2meet_service import When2MeetService


# ── Model Tests ──────────────────────────────────────────────────────────────

class TestSchedulingSession:
    def test_create_session(self):
        s = SchedulingSession(session_id="test1", initiated_by="U123")
        assert s.session_id == "test1"
        assert s.status == SchedulingStatus.CONFIGURING

    def test_serialization_roundtrip(self):
        s = SchedulingSession(
            session_id="test2", initiated_by="U456", term="Spring 2026",
        )
        s.groups = {"Lab Meeting": ["Alice", "Bob"], "Project X": ["Alice"]}
        s.preferred_durations = {"Lab Meeting": 4, "Project X": 2.5}
        s.project_emojis = {"Lab Meeting": ":microscope:"}
        s.pi = ["Jeremy"]
        s.senior = ["Alice"]

        d = s.to_dict()
        assert isinstance(d, dict)
        assert d["term"] == "Spring 2026"
        assert d["preferred_durations"]["Project X"] == 2.5

        s2 = SchedulingSession.from_dict(d)
        assert s2.session_id == "test2"
        assert s2.groups == s.groups
        assert s2.preferred_durations == s.preferred_durations
        assert s2.pi == ["Jeremy"]

    def test_get_all_members(self):
        s = SchedulingSession(session_id="t", initiated_by="U1")
        s.groups = {"A": ["Alice", "Bob"], "B": ["Bob", "Charlie"]}
        s.pi = ["Jeremy"]
        members = s.get_all_members()
        assert sorted(members) == ["Alice", "Bob", "Charlie", "Jeremy"]

    def test_update_status(self):
        s = SchedulingSession(session_id="t", initiated_by="U1")
        s.update_status(SchedulingStatus.SURVEY_POSTED)
        assert s.status == SchedulingStatus.SURVEY_POSTED
        assert s.error_message == ""

        s.update_status(SchedulingStatus.ERROR, "something broke")
        assert s.status == SchedulingStatus.ERROR
        assert s.error_message == "something broke"


# ── Storage Tests ────────────────────────────────────────────────────────────

class TestSchedulingStorage:
    def test_save_and_load(self, tmp_path):
        path = tmp_path / "sessions.json"
        storage = SchedulingStorage(path)

        session = SchedulingSession(session_id="s1", initiated_by="U1", term="Winter 2026")
        session.groups = {"Lab Meeting": ["Alice"]}
        storage.save(session)

        # Reload from disk
        storage2 = SchedulingStorage(path)
        loaded = storage2.get("s1")
        assert loaded is not None
        assert loaded.term == "Winter 2026"
        assert loaded.groups == {"Lab Meeting": ["Alice"]}

    def test_get_active(self, tmp_path):
        path = tmp_path / "sessions.json"
        storage = SchedulingStorage(path)

        s1 = SchedulingSession(session_id="s1", initiated_by="U1")
        s1.update_status(SchedulingStatus.COMPLETED)
        storage.save(s1)

        s2 = SchedulingSession(session_id="s2", initiated_by="U1")
        s2.update_status(SchedulingStatus.SURVEY_POSTED)
        storage.save(s2)

        active = storage.get_active()
        assert active is not None
        assert active.session_id == "s2"

    def test_get_latest_completed(self, tmp_path):
        path = tmp_path / "sessions.json"
        storage = SchedulingStorage(path)

        s1 = SchedulingSession(session_id="s1", initiated_by="U1")
        s1.update_status(SchedulingStatus.COMPLETED)
        s1.project_emojis = {"Lab Meeting": ":microscope:"}
        storage.save(s1)

        latest = storage.get_latest_completed()
        assert latest is not None
        assert latest.project_emojis == {"Lab Meeting": ":microscope:"}

    def test_delete(self, tmp_path):
        path = tmp_path / "sessions.json"
        storage = SchedulingStorage(path)

        session = SchedulingSession(session_id="s1", initiated_by="U1")
        storage.save(session)
        assert storage.get("s1") is not None

        storage.delete("s1")
        assert storage.get("s1") is None


# ── Project Store Tests ──────────────────────────────────────────────────────

class TestProjectStore:
    def test_load_default_database(self):
        """Test loading the shipped projects.json database."""
        from cdl_bot.project_store import ProjectStore
        store = ProjectStore()
        active = store.list_active()
        assert "Lab Meeting" in active
        assert "Kraken" in active
        assert active["Lab Meeting"]["emoji"] == ":raising_hand:"
        assert active["Kraken"]["default_duration"] == 4

    def test_channels_populated(self):
        """Test that channels are populated from the database."""
        from cdl_bot.project_store import ProjectStore
        store = ProjectStore()
        kraken = store.get("Kraken")
        assert "#kraken" in kraken["channels"]
        el = store.get("Efficient Learning")
        assert "#efficientlearning" in el["channels"]

    def test_descriptions_populated(self):
        """Test that descriptions are populated from the database."""
        from cdl_bot.project_store import ProjectStore
        store = ProjectStore()
        assert store.get("Kraken")["description"] == "LLMs"
        assert "Optimizing" in store.get("Efficient Learning")["description"]

    def test_upsert_new_project(self, tmp_path):
        """Test adding a new project to the database."""
        from cdl_bot.project_store import ProjectStore
        db_path = tmp_path / "projects.json"
        db_path.write_text("{}")
        store = ProjectStore(db_path)

        store.upsert("New Project", emoji=":rocket:", channels=["#new"],
                      description="A new project", default_duration=3)

        reloaded = ProjectStore(db_path)
        proj = reloaded.get("New Project")
        assert proj is not None
        assert proj["emoji"] == ":rocket:"
        assert proj["channels"] == ["#new"]
        assert proj["default_duration"] == 3
        assert proj["active"] is True

    def test_deactivate_project(self, tmp_path):
        """Test deactivating a project hides it from active list."""
        from cdl_bot.project_store import ProjectStore
        db_path = tmp_path / "projects.json"
        db_path.write_text('{"Old": {"emoji": ":x:", "channels": [], "description": "Old", "default_duration": 2, "active": true}}')
        store = ProjectStore(db_path)

        assert "Old" in store.list_active()
        store.deactivate("Old")
        assert "Old" not in store.list_active()
        # Still in database, just inactive
        assert store.get("Old") is not None

    def test_sync_from_session(self, tmp_path):
        """Test syncing session projects back to the database."""
        from cdl_bot.project_store import ProjectStore
        db_path = tmp_path / "projects.json"
        db_path.write_text('{"Existing": {"emoji": ":old:", "channels": ["#existing"], "description": "Existing project", "default_duration": 2, "active": true}}')
        store = ProjectStore(db_path)

        # Session has existing + new project
        store.sync_from_session(
            ["Existing", "Brand New"],
            {"Existing": 4, "Brand New": 2},
            {"Existing": ":updated:", "Brand New": ":star:"},
        )

        reloaded = ProjectStore(db_path)
        # Existing project updated emoji/duration, kept channels
        ex = reloaded.get("Existing")
        assert ex["emoji"] == ":updated:"
        assert ex["default_duration"] == 4
        assert ex["channels"] == ["#existing"]
        # New project created
        nw = reloaded.get("Brand New")
        assert nw is not None
        assert nw["emoji"] == ":star:"
        assert nw["active"] is True

    def test_get_config_text(self):
        """Test formatting projects for the config modal."""
        from cdl_bot.project_store import ProjectStore
        store = ProjectStore()
        text = store.get_config_text()
        assert "Lab Meeting | 4 | :raising_hand:" in text
        assert "Kraken | 4 | :octopus:" in text

    def test_get_survey_project_list(self):
        """Test formatting projects for survey announcement."""
        from cdl_bot.project_store import ProjectStore
        store = ProjectStore()
        text = store.get_survey_project_list(
            ["Kraken", "Efficient Learning"],
            {"Kraken": ":octopus:", "Efficient Learning": ":teacher:"},
        )
        assert "#kraken" in text
        assert "LLMs" in text
        assert "#efficientlearning" in text
        # Should be bulleted
        assert "•" in text

    def test_survey_list_excludes_office_hours(self):
        """Test that office hours are excluded from survey project list."""
        from cdl_bot.project_store import ProjectStore
        store = ProjectStore()
        text = store.get_survey_project_list(
            ["Lab Meeting", "Jeremy Office Hours"],
            {"Lab Meeting": ":raising_hand:"},
            exclude_from_survey=["Office Hours"],
        )
        assert "Lab meeting" in text
        assert "Office Hours" not in text


# ── Handler Utility Tests ────────────────────────────────────────────────────

class TestTermDerivation:
    def test_returns_tuple_of_three(self):
        term, start, end = _derive_term()
        assert isinstance(term, str)
        assert isinstance(start, str)
        assert isinstance(end, str)
        # Term should contain a year
        assert any(c.isdigit() for c in term)

    def test_term_has_season(self):
        term, _, _ = _derive_term()
        seasons = ["Winter", "Spring", "Summer", "Fall"]
        assert any(s in term for s in seasons)


class TestParseProjects:
    def test_basic_parsing(self):
        text = "Lab Meeting | 4 | :raising_hand:"
        names, durs, emojis = _parse_projects(text)
        assert names == ["Lab Meeting"]
        assert durs == {"Lab Meeting": 4}
        assert emojis == {"Lab Meeting": ":raising_hand:"}

    def test_no_emoji(self):
        text = "Office Hours | 6 |"
        names, durs, emojis = _parse_projects(text)
        assert "Office Hours" in names
        assert durs["Office Hours"] == 6
        assert "Office Hours" not in emojis

    def test_biweekly_duration(self):
        text = "Project X | 2.5 | :calendar:"
        _, durs, _ = _parse_projects(text)
        assert durs["Project X"] == 2.5

    def test_multiline(self):
        text = """
Lab Meeting | 4 | :raising_hand:
Kraken | 4 | :octopus:
1:1 (Claudia) | 2 |
"""
        names, durs, emojis = _parse_projects(text)
        assert len(names) == 3
        assert "Kraken" in names
        assert emojis.get("1:1 (Claudia)") is None

    def test_default_projects(self):
        """Test parsing the default pre-populated project list from database."""
        from cdl_bot.project_store import ProjectStore
        store = ProjectStore()  # loads from data/projects.json
        text = store.get_config_text()
        names, durs, emojis = _parse_projects(text)
        assert "Lab Meeting" in names
        assert "Kraken" in names
        assert durs["Lab Meeting"] == 4
        assert emojis["Lab Meeting"] == ":raising_hand:"
        assert emojis["Kraken"] == ":octopus:"


class TestFuzzyMatchNames:
    def test_exact_match(self):
        mapping, unmatched = _fuzzy_match_names(
            ["Jeremy", "Paxton"], ["Jeremy", "Paxton"]
        )
        assert mapping == {"Jeremy": "Jeremy", "Paxton": "Paxton"}
        assert unmatched == []

    def test_case_insensitive(self):
        mapping, _ = _fuzzy_match_names(["claudia"], ["Claudia"])
        assert mapping == {"claudia": "Claudia"}

    def test_first_name_extraction(self):
        mapping, _ = _fuzzy_match_names(
            ["Aaron Raycove", "Jacob Bacus"], ["Aaron", "Jacob"]
        )
        assert mapping == {"Aaron Raycove": "Aaron", "Jacob Bacus": "Jacob"}

    def test_email_skipped(self):
        _, unmatched = _fuzzy_match_names(
            ["test@example.com"], ["Alice"]
        )
        assert "test@example.com" in unmatched

    def test_unmatched_names(self):
        mapping, unmatched = _fuzzy_match_names(
            ["Jeremy", "UnknownPerson"], ["Jeremy", "Alice"]
        )
        assert mapping == {"Jeremy": "Jeremy"}
        assert "UnknownPerson" in unmatched

    def test_real_world_data(self):
        """Test with actual When2Meet respondent names from CDL survey."""
        respondents = [
            "Aaron Raycove", "Alexandra Wingo", "Alishba Tahir", "Angelyn",
            "claudia", "Colson Duncan", "Daniel", "Jacob Bacus", "Jay",
            "Jennifer", "Jeremy", "Kevin", "MJ", "Om", "Paxton",
            "Will lehman",
        ]
        expected = [
            "Aaron", "Alex", "Alishba", "Angelyn", "Claudia", "Colson",
            "Daniel", "Jacob", "Jay", "Jennifer", "Jeremy", "Kevin",
            "MJ", "Om", "Paxton", "Will",
        ]
        mapping, unmatched = _fuzzy_match_names(respondents, expected)
        # All should match
        assert len(mapping) == 16
        assert unmatched == []
        assert mapping["Aaron Raycove"] == "Aaron"
        assert mapping["claudia"] == "Claudia"
        assert mapping["Will lehman"] == "Will"

    def test_xin_jin_not_xinming(self):
        """Xin Jin should match to Xin Jin, not Xinming."""
        mapping, unmatched = _fuzzy_match_names(
            ["Xin"], ["Xin Jin", "Xinming"]
        )
        # "Xin" as first name should match "Xin Jin" (first-name match)
        # not "Xinming" (which just starts with "Xin")
        assert mapping.get("Xin") == "Xin Jin"


class TestFormatConfigSummary:
    def test_basic_summary(self):
        s = SchedulingSession(session_id="t", initiated_by="U1", term="Spring 2026",
                              term_start="2026-03-25", term_end="2026-06-03")
        s.pi = ["Jeremy"]
        s.groups = {"Lab Meeting": [], "Kraken": []}
        s.preferred_durations = {"Lab Meeting": 4, "Kraken": 4}
        summary = _format_config_summary(s)
        assert "Spring 2026" in summary
        assert "Jeremy" in summary
        assert "Lab Meeting" in summary
        assert "Members will be assigned" in summary


# ── Scheduling Algorithm Tests ───────────────────────────────────────────────

def _make_test_availability():
    """Create a small test availability DataFrame."""
    days = ["Monday"] * 8 + ["Tuesday"] * 8
    times = [f"{h}:{m:02d}:00" for h in range(10, 12) for m in (0, 15, 30, 45)] * 2

    data = {
        "Day": days,
        "Time": times,
        "Jeremy": [1] * 16,
        "Alice": [1] * 8 + [0] * 8,
        "Bob": [1] * 16,
        "Charlie": [0] * 4 + [1] * 4 + [1] * 8,
    }
    df = pd.DataFrame(data).set_index(["Day", "Time"])
    return df


class TestSchedulingAlgorithm:
    def test_basic_scheduling(self):
        availability = _make_test_availability()
        PI = ["Jeremy"]
        senior = ["Alice"]
        external = []
        groups = {
            "Lab Meeting": ["Alice", "Bob", "Charlie"],
            "Project A": ["Alice", "Bob"],
        }
        durations = {"Lab Meeting": 4, "Project A": 2}

        scheduled, schedule_df = find_best_meeting_times(
            availability, PI, senior, external, groups, durations,
        )

        assert "Lab Meeting" in scheduled
        assert "Project A" in scheduled
        assert not schedule_df.empty

    def test_pi_required(self):
        """PI must be available for all scheduled meetings."""
        availability = _make_test_availability()
        # Make Jeremy unavailable on Tuesday
        availability.loc["Tuesday", "Jeremy"] = 0

        PI = ["Jeremy"]
        groups = {"Meeting": ["Alice"]}
        durations = {"Meeting": 2}

        scheduled, _ = find_best_meeting_times(
            availability, PI, [], [], groups, durations,
        )

        if "Meeting" in scheduled:
            assert scheduled["Meeting"]["day"] == "Monday"

    def test_no_overlap(self):
        """Two weekly meetings should not overlap."""
        availability = _make_test_availability()
        PI = ["Jeremy"]
        groups = {
            "Meeting A": ["Alice"],
            "Meeting B": ["Bob"],
        }
        durations = {"Meeting A": 4, "Meeting B": 4}

        scheduled, _ = find_best_meeting_times(
            availability, PI, [], [], groups, durations,
        )

        if "Meeting A" in scheduled and "Meeting B" in scheduled:
            a_slots = set((scheduled["Meeting A"]["day"], t) for t in scheduled["Meeting A"]["times"])
            b_slots = set((scheduled["Meeting B"]["day"], t) for t in scheduled["Meeting B"]["times"])
            assert a_slots.isdisjoint(b_slots)

    def test_senior_weighting(self):
        """Senior members should be weighted higher in scoring."""
        availability = _make_test_availability()
        PI = ["Jeremy"]
        senior = ["Alice"]
        groups = {"Meeting": ["Alice", "Bob", "Charlie"]}
        durations = {"Meeting": 2}

        scheduled, _ = find_best_meeting_times(
            availability, PI, senior, [], groups, durations,
        )

        # Alice is available Monday only; meeting should prefer Monday
        assert scheduled["Meeting"]["day"] == "Monday"


class TestFormatScheduleForSlack:
    def test_basic_format(self):
        availability = _make_test_availability()
        scheduled, schedule_df = find_best_meeting_times(
            availability, ["Jeremy"], [], [], {"Meeting": ["Alice"]}, {"Meeting": 2},
        )
        result = format_schedule_for_slack(scheduled, schedule_df, {"Meeting": ":star:"})
        assert "Meeting" in result
        assert ":star:" in result

    def test_empty_schedule(self):
        result = format_schedule_for_slack({}, pd.DataFrame(), {})
        assert "No meetings" in result


class TestFormatAnnouncement:
    def test_basic_announcement(self):
        availability = _make_test_availability()
        groups = {"Lab Meeting": ["Alice", "Bob"]}
        scheduled, schedule_df = find_best_meeting_times(
            availability, ["Jeremy"], [], [], groups, {"Lab Meeting": 4},
        )
        result = format_announcement(
            scheduled, schedule_df, groups,
            {"Lab Meeting": ":microscope:"}, "Spring 2026",
        )
        assert "Spring 2026" in result
        assert "Lab Meeting" in result
        assert ":microscope:" in result


# ── When2Meet Service Tests (live) ───────────────────────────────────────────

class TestWhen2MeetService:
    def test_parse_real_survey(self):
        """Parse an actual When2Meet survey (the one from the CDL notebook)."""
        svc = When2MeetService()
        df = svc.parse_responses("https://www.when2meet.com/?34050837-QFUDh")

        assert not df.empty
        assert len(df.columns) >= 15  # At least 15 respondents
        assert "Jeremy" in df.columns
        assert df.index.names == ["Day", "Time"]
        # All values should be 0 or 1
        assert set(df.values.flatten()) <= {0, 1}

    def test_get_respondent_names(self):
        svc = When2MeetService()
        names = svc.get_respondent_names("https://www.when2meet.com/?34050837-QFUDh")
        assert len(names) >= 15
        assert "Jeremy" in names

    def test_create_survey(self):
        """Test creating a When2Meet survey with DaysOfTheWeek mode."""
        svc = When2MeetService()
        url = svc.create_survey("Test Survey Creation")
        assert "when2meet.com/?" in url
        # Verify it has time slots
        names = svc.get_respondent_names(url)
        # New survey has no respondents yet
        assert names == []


# ── Integration Test ─────────────────────────────────────────────────────────

class TestEndToEndScheduling:
    def test_full_pipeline(self):
        """Test the complete pipeline: scrape → match → schedule → format."""
        svc = When2MeetService()
        availability = svc.parse_responses("https://www.when2meet.com/?34050837-QFUDh")

        expected = [
            "Aaron", "Alex", "Alishba", "Angelyn", "Claudia", "Colson",
            "Daniel", "Jacob", "Jay", "Jennifer", "Jeremy", "Kevin",
            "MJ", "Om", "Paxton", "Will",
        ]
        mapping, unmatched = _fuzzy_match_names(list(availability.columns), expected)
        assert len(mapping) >= 14  # Most should match

        availability = availability.rename(columns=mapping)
        expected_set = set(expected)
        availability = availability[[c for c in availability.columns if c in expected_set]]

        PI = ["Jeremy"]
        senior = ["Paxton", "Claudia"]
        external = []
        groups = {
            "Lab Meeting": list(availability.columns),
            "Project A": ["Paxton", "Jacob"],
        }
        durations = {"Lab Meeting": 4, "Project A": 2}
        emojis = {"Lab Meeting": ":microscope:", "Project A": ":star:"}

        scheduled, schedule_df = find_best_meeting_times(
            availability, PI, senior, external, groups, durations,
        )

        assert "Lab Meeting" in scheduled
        assert not schedule_df.empty

        slack_msg = format_schedule_for_slack(scheduled, schedule_df, emojis)
        assert "Lab Meeting" in slack_msg
        assert ":microscope:" in slack_msg

        announcement = format_announcement(
            scheduled, schedule_df, groups, emojis, "Spring 2026",
        )
        assert "Spring 2026" in announcement
