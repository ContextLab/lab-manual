"""
Offboarding workflow handlers for Slack.

Handles the process of removing lab members:
- Prompts admin for what access to revoke
- Does NOT automatically remove anyone (per requirements)
- Provides guidance for manual steps (website removal)
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from slack_bolt import App
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..config import Config

logger = logging.getLogger(__name__)


@dataclass
class OffboardingRequest:
    """Tracks an offboarding request."""
    slack_user_id: str
    name: str
    initiated_by: str
    github_username: str = ""
    email: str = ""
    remove_github: bool = False
    remove_calendars: bool = False
    created_at: datetime = field(default_factory=datetime.now)


# In-memory storage for offboarding requests
_offboarding_requests: dict[str, OffboardingRequest] = {}


def register_offboard_handlers(app: App, config: Config):
    """Register all offboarding-related handlers with the Slack app."""

    @app.command("/cdl-offboard")
    def handle_offboard_command(ack, command, client: WebClient, respond):
        """Handle the /cdl-offboard slash command."""
        ack()

        user_id = command["user_id"]
        text = command.get("text", "").strip()

        # This command can be initiated by the member themselves or the admin
        is_admin = user_id == config.slack.admin_user_id

        # If text contains a user mention and caller is admin, offboard that user
        target_user_id = user_id  # Default to self
        if text.startswith("<@") and ">" in text and is_admin:
            target_user_id = text.split("<@")[1].split("|")[0].split(">")[0]

        # Get user info
        try:
            user_info = client.users_info(user=target_user_id)
            user_name = user_info["user"]["real_name"] or user_info["user"]["name"]
            user_email = user_info["user"].get("profile", {}).get("email", "")
        except SlackApiError as e:
            respond(f"Error getting user info: {e}")
            return

        # If self-initiated, send request to admin
        if target_user_id == user_id and not is_admin:
            _send_offboarding_request_to_admin(client, config, target_user_id, user_name, user_email)
            respond(
                "Your offboarding request has been sent to the lab admin. "
                "They will confirm what access should be revoked or retained."
            )
            return

        # If admin-initiated, show the confirmation dialog
        _send_offboarding_confirmation(client, config, target_user_id, user_name, user_email, command["trigger_id"])
        respond(f"Opening offboarding options for {user_name}...")

    @app.action("confirm_offboarding")
    def handle_confirm_offboarding(ack, body, client: WebClient, action):
        """Handle confirmation of offboarding actions."""
        ack()

        admin_id = body["user"]["id"]
        if admin_id != config.slack.admin_user_id:
            return

        user_id = action["value"]
        request = _offboarding_requests.get(user_id)

        if not request:
            logger.error(f"No offboarding request found for {user_id}")
            return

        # Get checkbox selections from the state
        state = body.get("state", {}).get("values", {})

        remove_github = False
        remove_calendars = False

        for block_id, block_data in state.items():
            if "offboard_options" in block_data:
                selected = block_data["offboard_options"].get("selected_options", [])
                for opt in selected:
                    if opt["value"] == "github":
                        remove_github = True
                    elif opt["value"] == "calendars":
                        remove_calendars = True

        request.remove_github = remove_github
        request.remove_calendars = remove_calendars

        # Process the offboarding
        _process_offboarding(client, config, request)

        # Update the message
        _update_offboarding_message(client, body, request)

    @app.action("cancel_offboarding")
    def handle_cancel_offboarding(ack, body, client: WebClient, action):
        """Handle cancellation of offboarding."""
        ack()

        user_id = action["value"]
        _offboarding_requests.pop(user_id, None)

        # Update the message
        channel = body["channel"]["id"]
        ts = body["message"]["ts"]

        try:
            client.chat_update(
                channel=channel,
                ts=ts,
                text="Offboarding cancelled",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": ":x: Offboarding cancelled. No changes were made.",
                        },
                    },
                ],
            )
        except SlackApiError as e:
            logger.error(f"Error updating message: {e}")

    @app.action("offboard_options")
    def handle_offboard_options(ack):
        """Handle checkbox selection (just acknowledge)."""
        ack()


def _send_offboarding_request_to_admin(
    client: WebClient,
    config: Config,
    user_id: str,
    user_name: str,
    user_email: str,
):
    """Send an offboarding request to the admin for confirmation."""
    request = OffboardingRequest(
        slack_user_id=user_id,
        name=user_name,
        initiated_by=user_id,
        email=user_email,
    )
    _offboarding_requests[user_id] = request

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":wave: Offboarding Request",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{user_name}* (<@{user_id}>) has initiated the offboarding process.",
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Select what access to revoke:*\n\n"
                "_Note: Some lab members may continue to collaborate on projects after leaving. "
                "Only revoke access that is no longer needed._",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Select options:",
            },
            "accessory": {
                "type": "checkboxes",
                "action_id": "offboard_options",
                "options": [
                    {
                        "text": {"type": "plain_text", "text": "Remove from GitHub organization"},
                        "value": "github",
                        "description": {"type": "plain_text", "text": "Revoke access to ContextLab repos"},
                    },
                    {
                        "text": {"type": "plain_text", "text": "Remove calendar access"},
                        "value": "calendars",
                        "description": {"type": "plain_text", "text": "Revoke access to lab calendars"},
                    },
                ],
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": ":information_source: Website profile removal must be done manually in Squarespace "
                    "(or will be automated once the GitHub Pages migration is complete).",
                },
            ],
        },
        {"type": "divider"},
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Confirm Offboarding"},
                    "style": "danger",
                    "action_id": "confirm_offboarding",
                    "value": user_id,
                    "confirm": {
                        "title": {"type": "plain_text", "text": "Confirm Offboarding"},
                        "text": {
                            "type": "mrkdwn",
                            "text": f"Are you sure you want to proceed with offboarding {user_name}?",
                        },
                        "confirm": {"type": "plain_text", "text": "Yes, proceed"},
                        "deny": {"type": "plain_text", "text": "Cancel"},
                    },
                },
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Cancel"},
                    "action_id": "cancel_offboarding",
                    "value": user_id,
                },
            ],
        },
    ]

    try:
        client.chat_postMessage(
            channel=config.slack.admin_user_id,
            text=f"Offboarding request from {user_name}",
            blocks=blocks,
        )
    except SlackApiError as e:
        logger.error(f"Error sending offboarding request: {e}")


def _send_offboarding_confirmation(
    client: WebClient,
    config: Config,
    user_id: str,
    user_name: str,
    user_email: str,
    trigger_id: str,
):
    """Show offboarding confirmation dialog (admin-initiated)."""
    # This is the same as the member-initiated flow but sent directly
    _send_offboarding_request_to_admin(client, config, user_id, user_name, user_email)


def _process_offboarding(client: WebClient, config: Config, request: OffboardingRequest):
    """Process the offboarding actions."""
    results = []
    errors = []

    # Note: We intentionally do NOT automatically remove users
    # We just prepare instructions for the admin

    if request.remove_github:
        results.append(
            f":octocat: *GitHub:* Please manually remove `{request.github_username or request.name}` "
            f"from the ContextLab organization at:\n"
            f"https://github.com/orgs/ContextLab/people"
        )

    if request.remove_calendars:
        results.append(
            f":calendar: *Calendars:* Please remove `{request.email}` from the following calendars:\n"
            f"• Contextual Dynamics Lab\n"
            f"• Out of lab\n"
            f"• CDL Resources"
        )

    # Always include website instructions
    results.append(
        f":globe_with_meridians: *Website:* Please remove {request.name}'s profile from:\n"
        f"https://www.context-lab.com/people\n"
        f"(Or from the GitHub Pages people-site repo once migrated)"
    )

    # Send summary to admin
    summary_blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Offboarding Checklist: {request.name}",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "Please complete the following manual steps:",
            },
        },
    ]

    for item in results:
        summary_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": item,
            },
        })

    try:
        client.chat_postMessage(
            channel=config.slack.admin_user_id,
            text=f"Offboarding checklist for {request.name}",
            blocks=summary_blocks,
        )
    except SlackApiError as e:
        logger.error(f"Error sending offboarding checklist: {e}")

    # Notify the departing member
    try:
        dm_response = client.conversations_open(users=[request.slack_user_id])
        dm_channel = dm_response["channel"]["id"]

        client.chat_postMessage(
            channel=dm_channel,
            text="Your offboarding has been processed",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": ":wave: *Offboarding Confirmed*\n\n"
                        "The lab admin has been notified and will process your offboarding. "
                        "Thank you for your contributions to the CDL!",
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": "If you have any questions or need continued access for ongoing collaborations, "
                            "please contact the lab admin.",
                        },
                    ],
                },
            ],
        )
    except SlackApiError as e:
        logger.error(f"Error notifying departing member: {e}")


def _update_offboarding_message(client: WebClient, body: dict, request: OffboardingRequest):
    """Update the offboarding message to show completion."""
    channel = body["channel"]["id"]
    ts = body["message"]["ts"]

    actions = []
    if request.remove_github:
        actions.append("GitHub access")
    if request.remove_calendars:
        actions.append("Calendar access")

    actions_text = ", ".join(actions) if actions else "No access revoked"

    try:
        client.chat_update(
            channel=channel,
            ts=ts,
            text=f"Offboarding processed for {request.name}",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":white_check_mark: *Offboarding Processed: {request.name}*\n\n"
                        f"Actions to take: {actions_text}\n"
                        f"A checklist has been sent with manual steps to complete.",
                    },
                },
            ],
        )
    except SlackApiError as e:
        logger.error(f"Error updating offboarding message: {e}")
