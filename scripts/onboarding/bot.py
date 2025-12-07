#!/usr/bin/env python3
"""
CDL Onboarding Bot - Main Entry Point

A Slack bot for automating the onboarding process for new CDL lab members.

Features:
- /cdl-onboard @user - Start onboarding a new member (admin only)
- /cdl-offboard - Start offboarding process (self or admin)
- Interactive forms for collecting member information
- Admin approval workflow for all actions
- GitHub organization invitations
- Google Calendar sharing
- Photo processing (hand-drawn green borders)
- Bio editing via Claude API

Usage:
    python -m scripts.onboarding.bot

Environment Variables Required:
    SLACK_BOT_TOKEN - Slack bot OAuth token (xoxb-...)
    SLACK_APP_TOKEN - Slack app-level token (xapp-...)
    SLACK_ADMIN_USER_ID - Slack user ID of the admin
    GITHUB_TOKEN - GitHub personal access token with admin:org scope

Optional Environment Variables:
    GOOGLE_CREDENTIALS_FILE - Path to Google service account JSON
    GOOGLE_CALENDAR_* - Calendar IDs for each calendar
    ANTHROPIC_API_KEY - For bio editing feature
"""

import logging
import sys
from pathlib import Path

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from .config import get_config, Config
from .handlers.onboard import register_onboard_handlers
from .handlers.approval import register_approval_handlers
from .handlers.offboard import register_offboard_handlers
from .handlers.workflow_step import register_workflow_step_handlers
from .handlers.workflow_listener import register_workflow_listener_handlers

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger(__name__)


def create_app(config: Config) -> App:
    """
    Create and configure the Slack Bolt app.

    Args:
        config: Application configuration

    Returns:
        Configured Slack Bolt App instance
    """
    app = App(token=config.slack.bot_token)

    # Register all handlers
    register_onboard_handlers(app, config)
    register_approval_handlers(app, config)
    register_offboard_handlers(app, config)
    register_workflow_step_handlers(app, config)
    register_workflow_listener_handlers(app, config)

    # Add a health check command
    @app.command("/cdl-ping")
    def handle_ping(ack, respond):
        """Simple health check command."""
        ack()
        respond("Pong! CDL Onboarding Bot is running.")

    # Add help command
    @app.command("/cdl-help")
    def handle_help(ack, respond, command):
        """Show help information."""
        ack()

        user_id = command["user_id"]
        is_admin = user_id == config.slack.admin_user_id

        help_text = """*CDL Onboarding Bot Help*

*Available Commands:*

`/cdl-onboard @user` - Start onboarding a new lab member
  • Opens a form for the member to fill out their info
  • Collects GitHub username, bio, photo, and website URL
  • Sends request to admin for approval

`/cdl-offboard` - Start the offboarding process
  • Can be initiated by the member leaving or by admin
  • Admin selects which access to revoke
  • Generates a checklist for manual steps

`/cdl-ping` - Check if the bot is running

`/cdl-help` - Show this help message
"""

        if is_admin:
            help_text += """
*Admin-Only Features:*
• Approve/reject onboarding requests
• Select GitHub teams for new members
• Initiate offboarding for any member
• Request changes to submitted information
"""

        respond(help_text)

    # Log errors
    @app.error
    def handle_error(error, body, logger):
        """Handle errors in Slack event processing."""
        logger.exception(f"Error processing Slack event: {error}")
        logger.debug(f"Request body: {body}")

    logger.info("Slack app created and handlers registered")
    return app


def main():
    """Main entry point for the bot."""
    logger.info("Starting CDL Onboarding Bot...")

    try:
        config = get_config()
        logger.info("Configuration loaded successfully")

        # Log which optional services are available
        if config.google_calendar:
            logger.info("Google Calendar integration enabled")
        else:
            logger.warning("Google Calendar integration not configured")

        if config.anthropic:
            logger.info("Anthropic bio editing enabled")
        else:
            logger.warning("Anthropic bio editing not configured")

        # Create the app
        app = create_app(config)

        # Start the bot in Socket Mode
        handler = SocketModeHandler(app, config.slack.app_token)
        logger.info("Bot started in Socket Mode. Press Ctrl+C to stop.")
        handler.start()

    except ValueError as e:
        logger.error(f"Configuration error: {e}")
        logger.error("Please check your environment variables and .env file")
        sys.exit(1)
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
        sys.exit(0)
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
