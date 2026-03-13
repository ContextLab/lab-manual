"""
Meeting scheduling service.

Ported from CDL_scheduler.ipynb. Finds optimal meeting times given
availability data, group definitions, and priority constraints.
"""

import logging
from collections import defaultdict
from datetime import datetime, timedelta

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


def find_best_meeting_times(
    availability: pd.DataFrame,
    PI: list,
    senior: list,
    external: list,
    groups: dict,
    preferred_durations: dict = None,
    pi_unencumbered_weight: float = 2.0,
    day_concentration_weight: float = 3.0,
    contiguity_weight: float = 1.5,
) -> tuple[dict, pd.DataFrame]:
    """
    Find optimal meeting times that maximize attendance with priority weighting.

    Parameters
    ----------
    availability : pd.DataFrame
        MultiIndex (Day, Time) -> one column per person, 1=available 0=not.
        15-minute slots.
    PI : list
        PI names (required for all meetings).
    senior : list
        Senior members (weighted 3x in scoring).
    external : list
        External members (excluded from Lab Meeting attendance).
    groups : dict
        meeting_name -> list of non-PI member names.
    preferred_durations : dict
        meeting_name -> number of 15-min blocks.
        Durations ending in .5 indicate biweekly meetings
        (e.g., 2.5 = biweekly 30-min meeting).
    pi_unencumbered_weight : float
        Weight for preserving PI's free time blocks.
    day_concentration_weight : float
        Weight for concentrating meetings on fewer days.
    contiguity_weight : float
        Weight for scheduling meetings back-to-back.

    Returns
    -------
    tuple of (scheduled_dict, schedule_df)
        scheduled_dict: meeting_name -> placement details
        schedule_df: summary DataFrame sorted by day/time
    """

    def is_biweekly(duration):
        return duration != int(duration)

    def get_actual_blocks(duration):
        return int(np.floor(duration)) if is_biweekly(duration) else int(duration)

    def time_to_minutes(time_str):
        if isinstance(time_str, str):
            parts = time_str.split(":")
            return int(parts[0]) * 60 + int(parts[1])
        elif hasattr(time_str, "hour"):
            return time_str.hour * 60 + time_str.minute
        return 0

    def calculate_pi_time_score(day, block_times, current_day_blocks, days_with_meetings):
        score = 0.0
        if day in days_with_meetings:
            score += day_concentration_weight

        if current_day_blocks:
            start_minutes = time_to_minutes(block_times[0])
            end_minutes = time_to_minutes(block_times[-1]) + 15

            for existing_start, existing_end in current_day_blocks:
                if start_minutes == existing_end or end_minutes == existing_start:
                    score += contiguity_weight
                elif start_minutes > existing_end:
                    gap = start_minutes - existing_end
                    if gap <= 60:
                        score -= (60 - gap) / 60 * pi_unencumbered_weight

        return score

    # Tracking state
    scheduled = {}
    used_times = set()
    biweekly_times = {}
    meeting_end_times = {}
    days_with_meetings = set()
    day_meeting_blocks = defaultdict(list)
    default_duration = 2

    # Separate and sort meetings
    weekly_meetings = []
    biweekly_meetings = []

    for meeting_name, group_members in groups.items():
        duration = preferred_durations.get(meeting_name, default_duration) if preferred_durations else default_duration
        if is_biweekly(duration):
            biweekly_meetings.append((meeting_name, group_members))
        else:
            weekly_meetings.append((meeting_name, group_members))

    def sort_key(item):
        name, members = item
        if name == "Lab Meeting":
            return (0, -len(members))
        return (1, -len(members))

    weekly_meetings.sort(key=sort_key)
    biweekly_meetings.sort(key=lambda x: -len(x[1]))

    def process_meeting(meeting_name, group_members, is_biweekly_meeting=False):
        candidates = []

        if meeting_name == "Lab Meeting":
            group_members = [m for m in group_members if m not in external]

        # Deduplicate: PI may also appear in group_members (e.g., from When2Meet)
        full_group = PI + [m for m in group_members if m not in PI]
        missing = [name for name in full_group if name not in availability.columns]
        if missing:
            logger.info(f"{meeting_name}: {missing} not in survey — assuming always available")

        duration = preferred_durations.get(meeting_name, default_duration) if preferred_durations else default_duration
        target_blocks = get_actual_blocks(duration)
        freq = "biweekly" if is_biweekly_meeting else "weekly"
        logger.info(f"Scheduling {meeting_name} ({freq}, {target_blocks * 15}min)...")

        for day in availability.index.get_level_values(0).unique():
            day_df = availability.loc[day]
            time_slots = list(day_df.index)

            if len(time_slots) < target_blocks:
                continue

            current_day_blocks = day_meeting_blocks.get(day, [])

            for i in range(len(time_slots) - target_blocks + 1):
                block_times = time_slots[i : i + target_blocks]
                block_index = [(day, t) for t in block_times]

                # Conflict checks
                if not is_biweekly_meeting:
                    if any(t in used_times for t in block_index):
                        continue
                    if any(t in biweekly_times for t in block_index):
                        continue
                else:
                    if any(t in used_times for t in block_index):
                        continue
                    overlapping = [biweekly_times[t] for t in block_index if t in biweekly_times]
                    if overlapping:
                        unique_overlapping = set(overlapping)
                        if len(unique_overlapping) > 1:
                            continue
                        other_meeting = list(unique_overlapping)[0]
                        other_details = scheduled.get(other_meeting)
                        if other_details:
                            other_times = set((other_details["day"], t) for t in other_details["times"])
                            this_times = set(block_index)
                            if other_times != this_times:
                                continue

                # PI must be available for all blocks
                pi_check = all(
                    day_df.loc[block_times, pi].all() if pi in day_df.columns else True
                    for pi in PI
                )
                if not pi_check:
                    continue

                # Score attendance
                seniors_in_group = [p for p in group_members if p in senior]
                other_members = [p for p in group_members if p not in senior]

                senior_available = sum(
                    day_df.loc[block_times, p].all() if p in day_df.columns else True
                    for p in seniors_in_group
                )
                other_available = sum(
                    day_df.loc[block_times, p].all() if p in day_df.columns else True
                    for p in other_members
                )

                if meeting_name == "Lab Meeting":
                    attendance_score = (senior_available * 10, other_available)
                else:
                    attendance_score = (senior_available * 3 + other_available,)

                pi_time_score = calculate_pi_time_score(
                    day, block_times, current_day_blocks, days_with_meetings
                )

                start_time = (day, block_times[0])
                is_contiguous = start_time in meeting_end_times

                sharing_bonus = 0
                shares_with = None
                if is_biweekly_meeting:
                    for t in block_index:
                        if t in biweekly_times:
                            sharing_bonus = 10
                            shares_with = biweekly_times[t]
                            break

                final_score = attendance_score + (pi_time_score, is_contiguous, sharing_bonus)

                candidates.append({
                    "score": final_score,
                    "day": day,
                    "times": block_times,
                    "pi_available": len(PI),
                    "senior_available": senior_available,
                    "other_available": other_available,
                    "total_group_size": len(seniors_in_group) + len(other_members),
                    "is_contiguous": is_contiguous,
                    "target_blocks": target_blocks,
                    "is_biweekly": is_biweekly_meeting,
                    "shares_slot": sharing_bonus > 0,
                    "shares_with": shares_with,
                })

        return candidates

    def schedule_meeting(meeting_name, group_members, is_biweekly_meeting):
        candidates = process_meeting(meeting_name, group_members, is_biweekly_meeting)

        if not candidates:
            logger.warning(f"No available time block found for '{meeting_name}'")
            return

        best = max(candidates, key=lambda x: x["score"])
        scheduled[meeting_name] = best

        if is_biweekly_meeting:
            for t in best["times"]:
                biweekly_times[(best["day"], t)] = meeting_name
        else:
            for t in best["times"]:
                used_times.add((best["day"], t))

        days_with_meetings.add(best["day"])

        start_minutes = time_to_minutes(best["times"][0])
        end_minutes = time_to_minutes(best["times"][-1]) + 15
        day_meeting_blocks[best["day"]].append((start_minutes, end_minutes))

        # Record end time for contiguity detection
        day_df = availability.loc[best["day"]]
        time_slots = list(day_df.index)
        end_idx = time_slots.index(best["times"][-1])
        if end_idx + 1 < len(time_slots):
            next_time = (best["day"], time_slots[end_idx + 1])
            meeting_end_times[next_time] = meeting_name

        # Log result
        last_time = best["times"][-1]
        if isinstance(last_time, pd.Timestamp):
            actual_end = last_time + pd.Timedelta(minutes=15)
        else:
            time_obj = pd.to_datetime(str(last_time), format="%H:%M:%S").time()
            end_dt = datetime.combine(datetime.today(), time_obj) + timedelta(minutes=15)
            actual_end = end_dt.time()

        unavailable = best["total_group_size"] - (best["senior_available"] + best["other_available"])
        logger.info(
            f"  {meeting_name}: {best['day']} {best['times'][0]}-{actual_end} "
            f"({best['senior_available']}sr + {best['other_available']}jr available, "
            f"{unavailable} unavailable)"
        )

    # Schedule weekly first, then biweekly
    for meeting_name, group_members in weekly_meetings:
        schedule_meeting(meeting_name, group_members, False)

    for meeting_name, group_members in biweekly_meetings:
        schedule_meeting(meeting_name, group_members, True)

    # Build summary DataFrame
    schedule_df = _build_schedule_df(scheduled, groups, PI, senior, external, preferred_durations)

    return scheduled, schedule_df


def _build_schedule_df(scheduled, groups, PI, senior, external, preferred_durations):
    """Build a summary DataFrame from scheduled meetings."""
    schedule_data = []

    for meeting_name, details in scheduled.items():
        last_time = details["times"][-1]
        if isinstance(last_time, pd.Timestamp):
            actual_end = last_time + pd.Timedelta(minutes=15)
        else:
            time_obj = pd.to_datetime(str(last_time), format="%H:%M:%S").time()
            end_dt = datetime.combine(datetime.today(), time_obj) + timedelta(minutes=15)
            actual_end = end_dt.time()

        if meeting_name == "Lab Meeting":
            group_for_count = [m for m in groups[meeting_name] if m not in external]
        else:
            group_for_count = groups[meeting_name]

        total_senior = details["pi_available"] + details["senior_available"]
        total_possible_senior = len(PI) + sum(1 for m in group_for_count if m in senior)

        frequency = "Biweekly" if details.get("is_biweekly") else "Weekly"
        if details.get("shares_slot"):
            frequency += " *"

        schedule_data.append({
            "Meeting": meeting_name,
            "Day": details["day"],
            "Start Time": str(details["times"][0]),
            "End Time": str(actual_end),
            "Duration (min)": len(details["times"]) * 15,
            "Frequency": frequency,
            "Senior Availability": f"{total_senior}/{total_possible_senior}",
            "Total Available": f"{details['pi_available'] + details['senior_available'] + details['other_available']}/{len(PI) + details['total_group_size']}",
        })

    schedule_df = pd.DataFrame(schedule_data)

    if not schedule_df.empty:
        day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        schedule_df["Day_num"] = schedule_df["Day"].map({d: i for i, d in enumerate(day_order)})
        schedule_df = schedule_df.sort_values(["Day_num", "Start Time"])
        schedule_df = schedule_df.drop("Day_num", axis=1)
        schedule_df = schedule_df.set_index("Meeting")

    return schedule_df


def format_schedule_for_slack(scheduled: dict, schedule_df: pd.DataFrame,
                              project_emojis: dict = None) -> str:
    """
    Format the schedule as a Slack message (mrkdwn).

    Returns a string suitable for posting to Slack.
    """
    if schedule_df is None or schedule_df.empty:
        return "No meetings were scheduled."

    lines = ["*Proposed Meeting Schedule*\n"]

    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    for day in day_order:
        day_meetings = schedule_df[schedule_df["Day"] == day]
        if day_meetings.empty:
            continue

        lines.append(f"*{day}:*")
        for meeting_name, row in day_meetings.iterrows():
            emoji = (project_emojis or {}).get(meeting_name, "")
            emoji_str = f"{emoji} " if emoji else ""
            freq = f" _{row['Frequency']}_" if "Biweekly" in row["Frequency"] else ""
            lines.append(
                f"  {emoji_str}`{row['Start Time'][:5]} - {row['End Time'][:5]}` "
                f"*{meeting_name}*{freq} ({row['Total Available']} available)"
            )
        lines.append("")

    # Note about shared slots
    shared = [name for name, details in scheduled.items() if details.get("shares_slot")]
    if shared:
        lines.append("_* Meetings marked share a time slot (alternating weeks)_")

    return "\n".join(lines)


def format_announcement(scheduled: dict, schedule_df: pd.DataFrame,
                        groups: dict, project_emojis: dict = None,
                        term: str = "") -> str:
    """
    Format the final announcement message for #general.

    Groups meetings by day with indented time entries:
        Monday:
          11:30 - 12:00: StockProphet (Weekly)
          15:00 - 16:00: Lab Meeting (Weekly)
    """
    if schedule_df is None or schedule_df.empty:
        return "No meetings were scheduled."

    lines = []
    if term:
        lines.append(f"Hi @channel! Here's the schedule for *{term}* meetings:\n")

    day_order = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
    for day in day_order:
        day_meetings = schedule_df[schedule_df["Day"] == day]
        if day_meetings.empty:
            continue

        lines.append(f"*{day}:*")

        for meeting_name, row in day_meetings.iterrows():
            freq = "Biweekly" if "Biweekly" in row["Frequency"] else "Weekly"
            start = row["Start Time"][:5]
            end = row["End Time"][:5]

            # Format one-on-one meetings as "Individual Meeting (Name)"
            display_name = meeting_name
            if meeting_name.endswith(" one-on-one"):
                person = meeting_name.replace(" one-on-one", "")
                display_name = f"Individual Meeting ({person})"

            lines.append(f"  {start} - {end}: {display_name} ({freq})")

        lines.append("")  # blank line between days

    return "\n".join(lines)
