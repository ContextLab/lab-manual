# CDL Bot

Slack bot for the Contextual Dynamics Laboratory. Automates onboarding, offboarding, and term meeting scheduling.

## Quick Start

```bash
# First time: create venv and install
python -m venv venv
source venv/bin/activate
pip install -e .

# Configure credentials
cp cdl_bot/.env.example cdl_bot/.env
nano cdl_bot/.env

# Start the bot
cdl-bot start
```

The `cdl-bot` CLI manages the bot process:

```bash
cdl-bot start    # Start (no-op if already running)
cdl-bot stop     # Stop (no-op if not running)
cdl-bot restart  # Stop + start
cdl-bot status   # Show PID or "not running"
cdl-bot logs     # Tail the log file
```

## Features

### Onboarding (`/cdl-onboard` or Workflow Builder)

Two entry points for new members:

1. **Workflow Builder** (recommended): New member runs "Join the lab!" workflow. Bot validates GitHub username, sends admin approval, then invites to GitHub org, shares calendars, processes profile photo.

2. **Admin-initiated**: `/cdl-onboard @user` opens a form for the new member. Collects GitHub username, bio, photo, website. Bio edited to CDL style via Claude. Photo gets hand-drawn green border.

### Offboarding (`/cdl-offboard` or Workflow Builder)

Admin selects access to revoke (GitHub, calendars). Generates a checklist — does NOT auto-remove anyone.

### Term Scheduling (`/cdl-schedule`)

1. Director runs `/cdl-schedule` — config modal auto-populates projects from database
2. Creates When2Meet survey (Mon-Fri weekly) and posts to #general with project list and emoji reactions
3. "Collect Responses" scrapes respondent names, checks for :zoom: individual meeting requests
4. Assignment modal: director assigns respondents to projects, marks senior/external
5. Algorithm maximizes attendance with PI-required, senior 3x weighting, day concentration
6. Director reviews proposed schedule, approves to post announcement

**Project database** (`data/projects.json`): Stores emoji, Slack channels, description, and default duration per project. Auto-populated from previous terms. New projects added during scheduling are saved for future use.

Project format in the config modal (one per line):
```
Project Name | duration_blocks | :emoji:
Lab Meeting | 4 | :raising_hand:
Kraken | 4 | :octopus:
```

Duration is in 15-minute blocks (4 = 60min). Append `.5` for biweekly (2.5 = biweekly 30min).

## Setup

### Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Create new app from manifest (`cdl_bot/manifest.json`) or manually:
   - Enable Socket Mode, create app-level token with `connections:write`
   - Bot scopes: `chat:write`, `commands`, `users:read`, `users:read.email`, `im:write`, `im:history`, `files:read`, `files:write`, `workflow.steps:execute`, `reactions:read`
   - Events: `file_shared`, `function_executed`
   - Slash commands: `/cdl-onboard`, `/cdl-offboard`, `/cdl-schedule`, `/cdl-ping`, `/cdl-help`
   - Enable Interactivity

### Credentials (`.env`)

Required:
- `SLACK_BOT_TOKEN` — Bot OAuth token (xoxb-...)
- `SLACK_APP_TOKEN` — App-level token (xapp-...)
- `SLACK_ADMIN_USER_ID` — Director's Slack user ID
- `GITHUB_TOKEN` — PAT with `admin:org` scope

Optional:
- `GOOGLE_CREDENTIALS_FILE` — Service account JSON for calendar sharing
- `GOOGLE_CALENDAR_*` — Calendar IDs
- `ANTHROPIC_API_KEY` — For Claude bio editing

## Testing

```bash
pytest tests/test_onboarding/ -v          # All tests
pytest tests/test_onboarding/test_scheduling.py -v  # Scheduling only (no API keys needed)
```

Tests use real API calls (no mocks). Tests skip automatically when credentials are missing.

## Architecture

```
cdl_bot/
├── bot.py                # Entry point, Slack app setup
├── cli.py                # cdl-bot CLI (start/stop/restart/status/logs)
├── config.py             # Environment/config management
├── project_store.py      # Project database CRUD
├── scheduling_storage.py # Scheduling session persistence
├── manifest.json         # Slack app manifest
├── data/
│   └── projects.json     # Project database (emojis, channels, descriptions)
├── handlers/
│   ├── onboard.py        # /cdl-onboard
│   ├── approval.py       # Admin approval workflow
│   ├── offboard.py       # /cdl-offboard
│   ├── schedule.py       # /cdl-schedule term scheduling flow
│   └── workflow_step.py  # Workflow Builder custom steps
├── models/
│   ├── onboarding_request.py
│   └── scheduling_session.py
└── services/
    ├── github_service.py      # GitHub org invitations
    ├── calendar_service.py    # Google Calendar sharing
    ├── image_service.py       # Photo border processing (Pillow)
    ├── bio_service.py         # Bio editing via Claude API
    ├── when2meet_service.py   # When2Meet create + scrape
    └── scheduling_service.py  # Meeting scheduling algorithm
```
