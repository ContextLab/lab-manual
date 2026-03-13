# CDL Scheduling Bot Implementation Plan

## Status: Implementation in progress

## Source Material
- Notebook: `scripts/onboarding/CDL_scheduler.ipynb`
- Key functions: `parse_when2meet(url)` and `find_best_meeting_times()`
- Example Slack messages below

## Algorithm Summary
- `parse_when2meet(url)` → DataFrame with (Day, Time) MultiIndex, 1 col per respondent (0/1), 15-min slots
- `find_best_meeting_times(availability, PI, senior, external, groups, preferred_durations)`:
  - Attendance maximization with priority weighting (seniors 3x, PI required)
  - Biweekly support (duration ending in .5 means biweekly)
  - Optimizes for: PI unencumbered time, day concentration, meeting contiguity
  - Schedules larger groups first, lab meeting first
  - Returns (scheduled_dict, schedule_df)

## Architecture Decisions
- **Availability**: Use when2meet for everyone (create surveys programmatically via POST)
- **Name matching**: Fuzzy match when2meet names to Slack users, director confirms via dropdown
- **Calendar events**: Google Apps Script web app (serverless, free)
- **Credentials**: GitHub Secrets preferred, or gitignored local file
- **Scheduling algorithm**: Extract from notebook into `scripts/onboarding/services/scheduling_service.py`

## Flow
1. `/cdl-schedule` → project config modal (names, durations, emojis — NO members)
2. Create When2Meet survey → post to #general with emoji reaction instructions
3. "Collect Responses" → scrape When2Meet respondent names
4. Assignment modal: director assigns respondents to projects + marks senior/external
5. Algorithm runs → director reviews proposed schedule
6. Approve → post announcement to #general

## Default Projects & Emojis
Pre-populated from previous terms. Baseline defaults:

| Project | Duration (blocks) | Emoji |
|-|-|-|
| Lab Meeting | 4 | :raising_hand: |
| Kraken | 4 | :octopus: |
| Efficient Learning | 2 | :teacher: |
| StockProphet | 2 | :chart_with_upwards_trend: |
| Brain Dynamics | 2 | :spider_web: |
| Memory Dynamics | 2 | :brain: |
| Asymmetries | 2 | :scales: |
| Jeremy Office Hours | 6 | |

## Key Data Structures
- `groups`: dict of meeting_name -> list of member names
- `preferred_durations`: dict of meeting_name -> 15-min blocks (e.g., 4=60min, 2.5=biweekly 30min)
- `PI`, `senior`, `external`: lists for priority weighting
- Project emojis: dict of meeting_name -> emoji

## Calendar Event Details
- Project meetings: Moore 416, Zoom: https://dartmouth.zoom.us/my/contextlab
- 1:1 meetings: Moore 349, same Zoom link
- Recurring events with term start/end dates

## Example: Survey Request Message (posted to #general)

Hey @channel, happy first day of winter, happy holidays, and happy new year!! :snowflake: :snowman: :new-year: :holidayspirit:

I'd like to nail down our meeting times for the upcoming (Winter 2026) term!  To that end, please fill out your availability for weekly meetings (lab meetings + project meetings). Here's a when2meet survey: https://www.when2meet.com/?34050837-QFUDh

Regular meetings will start up again on Monday, January 5 (i.e., on the first day of the term).

Here's the list of our weekly meetings for this term:

Lab meeting (full group): :raising_hand:
Optimizing learning meeting (#mooc-learning + #khan-academy-eeg + #course-shapes + #exploratorium-dartmouth-collab + #efficientlearning): :teacher:
StockProphet/Network Dynamics meeting (#stockprophet + #timecorr): :chart_with_upwards_trend:
Brain dynamics (#directedforgetting + #timecorr + #giblet-decoding + #niinja): :spider_web:
LLMs (#kraken): :octopus:
Memory Dynamics (#memory-dynamics): :brain:
Temporal asymmetries in memory (#prediction-retrodiction + #memory-cueing + #memory-entropy): :scales:

If you would like to have additional individual meetings with me, we can schedule those on an as-needed basis, or you can respond here (or via DM) if you'd like to have a recurring meeting with me (and please specify your desired frequency + duration of those meetings, in addition to filling out your availability on the when2meet survey so that I can match it up with my schedule).

In addition to filling out your availability, please tag this message with the appropriate emoji(s) corresponding to meetings you want to attend (note: everyone should be at the full-lab meetings if at all possible; adding your reaction helps me to gauge who is active in the lab this term). I'll use your responses to figure out times for each of these that accommodate as many people as possible.  Everything will go on the CDL calendar, and anyone is welcome at any of the project meetings if you're interested in learning more, even if it's not for your primary project(s).  Also feel free to join any channels you're interested in; lurking and/or chiming in sporadically is totally fine/encouraged, as is more regular participation by anyone who is interested.  The lab's policy is: anyone can work on any project they are interested in. So if you're new to the group, feel free to explore!

All group meetings will be hybrid: they will occur both in person (either our main lab space/Moore 416, or in the 4th floor library if we can reserve it), and via zoom (https://dartmouth.zoom.us/my/contextlab) if you are feeling sick and/or if you're off campus, etc..  All individual meetings will happen in my office (Moore 349) or via zoom (same link) if one of us is sick and/or off campus.

Please respond by filling out your availability for the winter term in when2meet + adding your emoji reactions to this message by the end of the work day (5pm) on January 4. 2026. That evening, I'll run my script for scheduling our meeting times, and then I'll post the times here. (If you don't fill out your availability by then, your needs/preferences won't be taken into account for scheduling.)

## Example: Schedule Announcement (posted to #general)

Hi @channel!  Here's the schedule for this term's meetings:

Monday:
  11:30 - 12:00: StockProphet (Weekly)
  13:00 - 13:30: Asymmetries (Weekly)
  13:30 - 14:00: Individual Meeting (Claudia) (Weekly)
  15:00 - 16:00: Kraken (Weekly)
  16:00 - 17:00: Lab Meeting (Weekly)

Friday:
  11:30 - 12:00: Memory Dynamics (Weekly)
  13:30 - 15:00: Jeremy Office Hours (Weekly)
  15:00 - 15:30: Efficient Learning (Weekly)
