# CDL Scheduling Bot Implementation Plan

## Status: Complete

All spec items implemented. See `cdl_bot/README.md` for full documentation.

## Implemented Features

### Core Flow
1. `/cdl-schedule` → project config modal (names, durations, emojis)
2. Create When2Meet survey → post to #general with emoji reaction instructions
3. "Collect Responses" → scrape When2Meet, auto-populate assignments from emoji reactions
4. Assignment modal: director assigns respondents to projects + marks senior/external
5. Algorithm runs → director reviews proposed schedule
6. Approve → post announcement to #general + create Google Calendar events

### Auto-population
- Project assignments from emoji reactions on survey message
- Senior members from #senior-lab-stuff channel membership
- Term dates scraped from Dartmouth registrar (fallback to approximations)
- Project database pre-populates config modal each term

### Individual Meetings
- :zoom: reaction on survey → director reviews accept/deny + duration
- Accepted meetings join the scheduling algorithm as one-on-one events
- Calendar events created in Moore 349 (vs Moore 416 for group meetings)

### Calendar Integration
- Recurring Google Calendar events with RRULE (weekly/biweekly)
- Term start/end dates define recurrence bounds
- Gracefully skips if GOOGLE_CREDENTIALS_FILE not configured

### Survey Message
- Conversational tone matching spec example
- Deadline computed as Friday before term start (actual date)
- Hybrid meeting details (Moore 416 / Moore 349, Zoom link)
- Lab policy paragraph encouraging exploration

### Announcement Format
- Grouped by day with headers (Monday:, Tuesday:, etc.)
- Indented time entries with (Weekly)/(Biweekly)
- One-on-one meetings displayed as "Individual Meeting (Name)"

## Key Data Structures
- `groups`: dict of meeting_name -> list of member names
- `preferred_durations`: dict of meeting_name -> 15-min blocks (e.g., 4=60min, 2.5=biweekly 30min)
- `PI`, `senior`, `external`: lists for priority weighting
- Project emojis: dict of meeting_name -> emoji
- `zoom_requests`: list of {user_id, name, accepted, duration_blocks}

## Calendar Event Details
- Project meetings: Moore 416, Zoom: https://dartmouth.zoom.us/my/contextlab
- 1:1 meetings: Moore 349, same Zoom link
- Recurring events with term start/end dates
