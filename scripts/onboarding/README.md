# CDL Onboarding Bot

A Slack bot for automating the onboarding and offboarding process for CDL lab members.

## Features

### Onboarding (Two Methods)

**Method 1: Workflow Builder Integration (Recommended)**

Integrates with existing "Join the lab!" and "Leave the lab" Slack Workflow Builder workflows. New members initiate their own onboarding by clicking a workflow link.

- New member runs the "Join the lab!" workflow
- Bot receives form data and validates GitHub username
- Admin gets an interactive approval message
- On approval: GitHub invite sent, calendars shared, photo processed

**Method 2: Admin-Initiated (`/cdl-onboard @user`)**

Admin can also start onboarding manually:
- Sends welcome message to new member
- Collects: GitHub username, bio, photo, website URL
- Validates GitHub username via API
- Edits bio to CDL style (third person, 3-4 sentences) using Claude
- Adds hand-drawn green border to profile photo
- Sends GitHub organization invitation
- Shares Google Calendar access
- All actions require admin approval

### Offboarding (`/cdl-offboard` or Workflow)
- Can be initiated by member or admin
- Works with "Leave the lab" Workflow Builder workflow
- Admin selects what access to revoke (GitHub, calendars)
- Does NOT automatically remove anyone
- Generates checklist for manual steps

### Term Scheduling (`/cdl-schedule`)
- Director runs `/cdl-schedule` to start the scheduling flow
- Configures projects, members, durations, emojis via modal
- Auto-derives term from current month (Winter/Spring/Summer/Fall)
- Creates When2Meet survey and posts to #general
- Scrapes responses and fuzzy-matches names to expected members
- Runs attendance-maximizing algorithm with PI-required, senior priority weighting
- Director reviews proposed schedule and approves
- Posts formatted announcement to #general
- Persists project emoji mappings across terms

## Setup

### 1. Create Slack App

1. Go to [api.slack.com/apps](https://api.slack.com/apps)
2. Create new app from manifest (use `manifest.json` in this directory)
   - Or create from scratch and configure manually:
3. Enable Socket Mode in "Socket Mode" settings
4. Create an app-level token with `connections:write` scope
5. Add Bot Token Scopes in "OAuth & Permissions":
   - `chat:write`
   - `commands`
   - `users:read`
   - `users:read.email`
   - `im:write`
   - `im:history`
   - `files:read`
   - `files:write`
   - `workflow.steps:execute`
6. Add Event Subscriptions:
   - `file_shared`
   - `function_executed`
7. Create slash commands in "Slash Commands":
   - `/cdl-onboard` - Start onboarding a new member
   - `/cdl-offboard` - Start offboarding process
   - `/cdl-schedule` - Start term meeting scheduling
   - `/cdl-ping` - Health check
   - `/cdl-help` - Show help
8. Enable "Interactivity & Shortcuts"
9. Enable "Org Level Apps" (for Workflow Builder custom steps)
10. Install app to workspace

### 2. Create GitHub Token

1. Go to [github.com/settings/tokens](https://github.com/settings/tokens)
2. Create new token (classic) with scopes:
   - `admin:org` (for team and invitation management)
   - `repo` (for team repository access)

### 3. Set Up Google Calendar (Optional)

1. Create a Google Cloud project
2. Enable the Google Calendar API
3. Create a service account
4. Download the credentials JSON file
5. Share each calendar with the service account email

### 4. Get Anthropic API Key (Optional)

1. Go to [console.anthropic.com](https://console.anthropic.com)
2. Create an API key for bio editing

### 5. Configure Environment

```bash
# Copy the example config
cp .env.example .env

# Edit with your credentials
nano .env
```

Required variables:
- `SLACK_BOT_TOKEN` - Bot OAuth token (xoxb-...)
- `SLACK_APP_TOKEN` - App-level token (xapp-...)
- `SLACK_ADMIN_USER_ID` - Your Slack user ID
- `GITHUB_TOKEN` - GitHub PAT with admin:org scope

Optional variables:
- `GOOGLE_CREDENTIALS_FILE` - Path to service account JSON
- `GOOGLE_CALENDAR_*` - Calendar IDs
- `ANTHROPIC_API_KEY` - For bio editing

### 6. Install Dependencies

```bash
pip install -r requirements.txt
```

### 7. Run the Bot

The bot runs locally on your machine using Slack's Socket Mode (no public URL needed).
Run it from the **repository root** directory:

```bash
# Create and activate a virtual environment (first time only)
python -m venv venv
source venv/bin/activate  # macOS/Linux
pip install -r scripts/onboarding/requirements.txt

# Load environment variables and start the bot
set -a && source scripts/onboarding/.env && set +a
python -m scripts.onboarding.bot
```

The bot will connect to Slack and log `⚡️ Bolt app is running!` when ready.
Press `Ctrl+C` to stop. The bot must be running for slash commands and
Workflow Builder integrations to respond.

## Usage

### Starting Onboarding

As admin:
```
/cdl-onboard @newmember
```

This will:
1. Open a form for the new member to fill out
2. Send approval request to admin
3. Admin reviews and approves/rejects
4. On approval: GitHub invite sent, calendars shared, photo processed

### Starting Offboarding

As member leaving:
```
/cdl-offboard
```

As admin for specific member:
```
/cdl-offboard @member
```

Admin selects what to revoke and receives checklist.

### Term Scheduling

As director:
```
/cdl-schedule
```

This will:
1. Open a configuration modal for projects, members, durations, and emojis
2. Create a When2Meet survey and post it to #general
3. When ready, click "Collect Responses" to scrape and run the algorithm
4. Review the proposed schedule (attendance-optimized)
5. Approve to post the announcement to #general

Project format in the modal (one per line):
```
Project Name: member1, member2 | duration_blocks | :emoji:
Lab Meeting: everyone | 4 | :microscope:
Kraken: Paxton, Jacob, MJ | 4 | :octopus:
Office Hours: | 6 |
```

Duration is in 15-minute blocks (4 = 60min). Append `.5` for biweekly (2.5 = biweekly 30min).

## Testing

Run tests (model and image tests don't require API keys):
```bash
# Models only
pytest tests/test_onboarding/test_models.py -v

# Image processing only
pytest tests/test_onboarding/test_image_service.py -v

# GitHub service (requires GITHUB_TOKEN)
pytest tests/test_onboarding/test_github_service.py -v

# Scheduling (scrapes live When2Meet, no API keys needed)
pytest tests/test_onboarding/test_scheduling.py -v

# Bio service (requires ANTHROPIC_API_KEY)
pytest tests/test_onboarding/test_bio_service.py -v

# All tests
pytest tests/test_onboarding/ -v
```

## Workflow Builder Integration

The bot provides custom steps that can be added to Slack Workflow Builder workflows.

### Adding Custom Steps to Workflows

1. Ensure the Slack app has `workflow.steps:execute` scope and `function_executed` event
2. In Workflow Builder, create or edit a workflow
3. Add a step and search for "CDL Onboarding" to find the custom steps:
   - **Process CDL Onboarding**: Receives form data, validates GitHub, sends approval to admin
   - **Process CDL Offboarding**: Notifies admin and generates checklist

### Connecting to Existing "Join the Lab!" Workflow

1. Edit your existing "Join the lab!" workflow in Workflow Builder
2. After the form collection step, add the "Process CDL Onboarding" step
3. Map the form fields to the step inputs:
   - `submitter_id` -> Person who started the workflow
   - `github_username` -> GitHub username field from form
   - `bio` -> Bio field from form
   - `website_url` -> Website field from form
   - `photo_url` -> Photo URL if collected

### Connecting to "Leave the Lab" Workflow

1. Create a new "Leave the lab" workflow or add to existing
2. Add the "Process CDL Offboarding" step
3. Map:
   - `submitter_id` -> Person leaving the lab

## Architecture

```
scripts/onboarding/
├── bot.py              # Main entry point
├── config.py           # Configuration management
├── manifest.json       # Slack app manifest (with functions)
├── handlers/
│   ├── onboard.py      # /cdl-onboard command handling
│   ├── approval.py     # Admin approval workflow
│   ├── offboard.py     # /cdl-offboard command handling
│   ├── schedule.py     # /cdl-schedule term scheduling flow
│   └── workflow_step.py # Workflow Builder custom steps
├── models/
│   ├── onboarding_request.py  # Onboarding data models
│   └── scheduling_session.py  # Scheduling session state
├── scheduling_storage.py  # Scheduling session persistence
└── services/
    ├── github_service.py      # GitHub API integration
    ├── calendar_service.py    # Google Calendar API
    ├── image_service.py       # Photo border processing
    ├── bio_service.py         # Claude API bio editing
    ├── when2meet_service.py   # When2Meet create + scrape
    └── scheduling_service.py  # Meeting scheduling algorithm
```

## Security Notes

- Credentials stored in `.env` (gitignored)
- All onboarding actions require admin approval
- Offboarding does NOT auto-remove (generates checklist)
- Private info detection in bios (phone, email, SSN)
