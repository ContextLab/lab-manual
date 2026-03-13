#!/bin/bash
# CDL Bot Setup — idempotent installer
# Usage: ./setup-cdl-bot.sh
set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SCRIPT_DIR"

VENV="$SCRIPT_DIR/venv"
ENV_FILE="$SCRIPT_DIR/cdl_bot/.env"
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

step() { echo -e "\n${GREEN}▸ $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠ $1${NC}"; }
fail() { echo -e "${RED}  ✗ $1${NC}"; }

print_slack_setup() {
    echo -e "${YELLOW}Slack App Setup Instructions:${NC}"
    echo ""
    echo "  1. Go to https://api.slack.com/apps"
    echo "  2. Create a new app (or import cdl_bot/manifest.json)"
    echo "  3. Enable Socket Mode → create app-level token (connections:write)"
    echo "  4. Add Bot Token Scopes: chat:write, commands, users:read,"
    echo "     users:read.email, im:write, im:history, files:read,"
    echo "     files:write, workflow.steps:execute, reactions:read"
    echo "  5. Add Event Subscriptions: file_shared, function_executed"
    echo "  6. Create Slash Commands:"
    echo "     /cdl-onboard, /cdl-offboard, /cdl-schedule, /cdl-ping, /cdl-help"
    echo "  7. Enable Interactivity"
    echo "  8. Install app to workspace"
    echo "  9. Copy tokens to cdl_bot/.env"
    echo ""
}

# ── 1. Python virtual environment ────────────────────────────────────────────
step "Checking Python virtual environment..."
if [ -d "$VENV" ]; then
    echo "  Virtual environment exists."
else
    echo "  Creating virtual environment..."
    python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"

# ── 2. Install package ──────────────────────────────────────────────────────
step "Installing cdl-bot package..."
pip install -e . --quiet 2>&1 | grep -v "already satisfied" || true
echo "  cdl-bot CLI installed."

# Verify CLI is available
if ! command -v cdl-bot &>/dev/null; then
    fail "cdl-bot CLI not found in PATH after install."
    echo "  Try: source venv/bin/activate && pip install -e ."
    exit 1
fi

# ── 3. Check .env file ─────────────────────────────────────────────────────
step "Checking credentials..."
if [ ! -f "$ENV_FILE" ]; then
    warn ".env file not found at $ENV_FILE"
    if [ -f "${ENV_FILE}.example" ]; then
        echo "  Creating from template..."
        cp "${ENV_FILE}.example" "$ENV_FILE"
    fi
    warn "Edit $ENV_FILE with your credentials:"
    echo ""
    echo "    nano $ENV_FILE"
    echo ""
    echo "  Required:"
    echo "    SLACK_BOT_TOKEN=xoxb-..."
    echo "    SLACK_APP_TOKEN=xapp-..."
    echo "    SLACK_ADMIN_USER_ID=U..."
    echo "    GITHUB_TOKEN=ghp_..."
    echo ""
    print_slack_setup
    exit 1
fi

# Check required vars are set (not empty)
MISSING=""
for var in SLACK_BOT_TOKEN SLACK_APP_TOKEN SLACK_ADMIN_USER_ID GITHUB_TOKEN; do
    val=$(grep "^${var}=" "$ENV_FILE" 2>/dev/null | cut -d= -f2-)
    if [ -z "$val" ]; then
        MISSING="$MISSING $var"
    fi
done
if [ -n "$MISSING" ]; then
    warn "Missing required variables in .env:$MISSING"
    echo "  Edit: nano $ENV_FILE"
    exit 1
fi
echo "  Credentials configured."

# ── 4. Test run ─────────────────────────────────────────────────────────────
step "Testing bot connection..."
cdl-bot stop 2>/dev/null || true
cdl-bot start

sleep 3

if cdl-bot status | grep -q "running"; then
    echo "  Bot process is running."

    # Test Slack authentication
    set -a && source "$ENV_FILE" && set +a
    PING_RESULT=$(python3 -c "
from slack_sdk import WebClient
client = WebClient(token='${SLACK_BOT_TOKEN}')
try:
    result = client.auth_test()
    print('OK:' + result.get('user', 'unknown'))
except Exception as e:
    print('FAIL:' + str(e))
" 2>&1)

    if echo "$PING_RESULT" | grep -q "^OK:"; then
        BOT_USER="${PING_RESULT#OK:}"
        echo -e "  ${GREEN}Slack auth OK (bot user: ${BOT_USER})${NC}"
        echo ""
        echo -e "${GREEN}Setup complete!${NC}"
        echo ""
        echo "  Commands:"
        echo "    cdl-bot start    # Start bot (idempotent)"
        echo "    cdl-bot stop     # Stop bot"
        echo "    cdl-bot restart  # Restart"
        echo "    cdl-bot status   # Check status"
        echo "    cdl-bot logs     # Tail logs"
        echo ""
        echo "  Only the lab director (SLACK_ADMIN_USER_ID) can run /cdl-* commands."
        echo "  The bot must be running for slash commands to respond."
    else
        cdl-bot stop 2>/dev/null || true
        fail "Bot started but Slack authentication failed."
        echo "  $PING_RESULT"
        echo ""
        print_slack_setup
        exit 1
    fi
else
    fail "Bot failed to start. Check logs: cdl-bot logs"
    echo ""
    print_slack_setup
    exit 1
fi
