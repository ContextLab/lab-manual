"""
Onboarding workflow handlers for Slack.

Manages the multi-step onboarding process:
1. Collect member information
2. Validate GitHub username
3. Request admin approval
4. Process invitations and bio/photo
"""

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

from slack_bolt import App
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..config import Config
from ..models.onboarding_request import OnboardingRequest, OnboardingStatus
from ..services.github_service import GitHubService
from ..services.image_service import ImageService
from ..services.bio_service import BioService

logger = logging.getLogger(__name__)

# In-memory storage for active onboarding requests
# In production, this should be persisted to a database
_active_requests: dict[str, OnboardingRequest] = {}


def get_request(user_id: str) -> Optional[OnboardingRequest]:
    """Get an active onboarding request for a user."""
    return _active_requests.get(user_id)


def save_request(request: OnboardingRequest):
    """Save an onboarding request."""
    _active_requests[request.slack_user_id] = request


def delete_request(user_id: str):
    """Delete an onboarding request."""
    _active_requests.pop(user_id, None)


def register_onboard_handlers(app: App, config: Config):
    """Register all onboarding-related handlers with the Slack app."""

    github_service = GitHubService(config.github.token, config.github.org_name)
    image_service = ImageService(config.border_color, config.border_width)
    bio_service = None
    if config.anthropic:
        bio_service = BioService(config.anthropic.api_key, config.anthropic.model)

    # Slash command to start onboarding
    @app.command("/cdl-onboard")
    def handle_onboard_command(ack, command, client: WebClient, respond):
        """Handle the /cdl-onboard slash command."""
        ack()

        user_id = command["user_id"]
        text = command.get("text", "").strip()

        # Check if user is admin
        if user_id != config.slack.admin_user_id:
            respond("Only the lab admin can initiate onboarding.")
            return

        # Parse mentioned user if provided
        target_user_id = None
        if text.startswith("<@") and ">" in text:
            # Extract user ID from mention like <@U12345|username>
            target_user_id = text.split("<@")[1].split("|")[0].split(">")[0]

        if not target_user_id:
            respond(
                "Please specify the Slack user to onboard. "
                "Usage: `/cdl-onboard @username`"
            )
            return

        # Get user info
        try:
            user_info = client.users_info(user=target_user_id)
            user_name = user_info["user"]["real_name"] or user_info["user"]["name"]
            user_email = user_info["user"].get("profile", {}).get("email", "")
        except SlackApiError as e:
            respond(f"Error getting user info: {e}")
            return

        # Check if user already has an active request
        existing_request = get_request(target_user_id)
        if existing_request:
            respond(
                f"User <@{target_user_id}> already has an active onboarding request "
                f"(status: {existing_request.status.value})"
            )
            return

        # Open DM with the new member
        try:
            dm_response = client.conversations_open(users=[target_user_id])
            dm_channel = dm_response["channel"]["id"]
        except SlackApiError as e:
            respond(f"Error opening DM with user: {e}")
            return

        # Create onboarding request
        request = OnboardingRequest(
            slack_user_id=target_user_id,
            slack_channel_id=dm_channel,
            name=user_name,
            email=user_email,
        )
        save_request(request)

        # Send welcome message to the new member
        welcome_blocks = _build_welcome_message(user_name)
        try:
            client.chat_postMessage(
                channel=dm_channel,
                text=f"Welcome to the CDL, {user_name}!",
                blocks=welcome_blocks,
            )
        except SlackApiError as e:
            logger.error(f"Error sending welcome message: {e}")

        respond(f"Started onboarding for <@{target_user_id}>. They've been sent the welcome message.")

    # Handle the onboarding form submission
    @app.view("onboarding_form")
    def handle_onboarding_form(ack, body, client: WebClient, view):
        """Handle submission of the onboarding information form."""
        ack()

        user_id = body["user"]["id"]
        request = get_request(user_id)

        if not request:
            logger.error(f"No onboarding request found for user {user_id}")
            return

        # Extract form values
        values = view["state"]["values"]

        # Get GitHub username
        github_username = values.get("github_block", {}).get("github_input", {}).get("value", "")

        # Get bio
        bio_raw = values.get("bio_block", {}).get("bio_input", {}).get("value", "")

        # Get website URL (optional)
        website_url = values.get("website_block", {}).get("website_input", {}).get("value", "")

        # Validate GitHub username
        is_valid, error_msg = github_service.validate_username(github_username)

        if not is_valid:
            # Send error message and re-prompt
            try:
                client.chat_postMessage(
                    channel=request.slack_channel_id,
                    text=f"The GitHub username '{github_username}' was not found. Please check and try again.",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f":warning: *GitHub username not found*\n\nThe username `{github_username}` doesn't exist on GitHub. Please double-check the spelling and try again.",
                            },
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "Try Again"},
                                    "action_id": "retry_github_username",
                                }
                            ],
                        },
                    ],
                )
            except SlackApiError as e:
                logger.error(f"Error sending validation error: {e}")
            return

        # Update the request
        request.github_username = github_username
        request.bio_raw = bio_raw
        request.website_url = website_url
        request.update_status(OnboardingStatus.PENDING_APPROVAL)
        save_request(request)

        # Process the bio if we have the service
        if bio_service and bio_raw:
            edited_bio, bio_error = bio_service.edit_bio(bio_raw, request.name)
            if edited_bio:
                request.bio_edited = edited_bio
                save_request(request)

        # Send confirmation to the new member
        try:
            client.chat_postMessage(
                channel=request.slack_channel_id,
                text="Thanks! Your information has been submitted for approval.",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": ":white_check_mark: *Information Received*\n\nYour onboarding information has been submitted. The lab admin will review it shortly.",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f"*What's next:*\n• GitHub: Invitation to ContextLab organization\n• Calendar: Access to lab calendars\n• Website: Your photo and bio will be added",
                        },
                    },
                ],
            )
        except SlackApiError as e:
            logger.error(f"Error sending confirmation: {e}")

        # Send approval request to admin
        _send_approval_request(client, config, request, github_service)

    # Handle retry GitHub username button
    @app.action("retry_github_username")
    def handle_retry_github(ack, body, client: WebClient):
        """Handle the retry GitHub username button."""
        ack()
        user_id = body["user"]["id"]
        request = get_request(user_id)

        if request:
            # Re-open the form modal
            _open_onboarding_form(client, body["trigger_id"], request)

    # Handle file uploads (for photo)
    @app.event("file_shared")
    def handle_file_shared(event, client: WebClient, say):
        """Handle when a file is shared in a DM with the bot."""
        file_id = event.get("file_id")
        channel_id = event.get("channel_id")
        user_id = event.get("user_id")

        request = get_request(user_id)
        if not request or request.slack_channel_id != channel_id:
            return  # Not an onboarding conversation

        # Get file info
        try:
            file_info = client.files_info(file=file_id)
            file_data = file_info["file"]
        except SlackApiError as e:
            logger.error(f"Error getting file info: {e}")
            return

        # Check if it's an image
        mimetype = file_data.get("mimetype", "")
        if not mimetype.startswith("image/"):
            say(
                channel=channel_id,
                text="Please upload an image file (JPEG, PNG, etc.) for your profile photo.",
            )
            return

        # Download the file
        file_url = file_data.get("url_private_download")
        if not file_url:
            logger.error("No download URL for file")
            return

        try:
            # Download using Slack token
            import requests

            headers = {"Authorization": f"Bearer {config.slack.bot_token}"}
            response = requests.get(file_url, headers=headers)
            response.raise_for_status()

            # Save to temp file
            suffix = Path(file_data.get("name", "photo.jpg")).suffix
            with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                tmp.write(response.content)
                tmp_path = Path(tmp.name)

            # Validate the image
            is_valid, error_msg = image_service.validate_image(tmp_path)
            if not is_valid:
                say(channel=channel_id, text=f"Image validation failed: {error_msg}")
                tmp_path.unlink()
                return

            # Save original path
            request.photo_original_path = tmp_path
            save_request(request)

            # Process the photo
            output_path = config.output_dir / f"{request.slack_user_id}_photo.png"
            processed_path = image_service.add_hand_drawn_border(
                tmp_path, output_path, seed=hash(request.slack_user_id)
            )
            request.photo_processed_path = processed_path
            save_request(request)

            # Upload the processed photo back to show the user
            try:
                client.files_upload_v2(
                    channel=channel_id,
                    file=str(processed_path),
                    title="Your processed profile photo",
                    initial_comment=":camera: Here's how your photo will look on the website with the CDL border!",
                )
            except SlackApiError as e:
                logger.error(f"Error uploading processed photo: {e}")

            say(
                channel=channel_id,
                text="Photo received and processed! If you're happy with it, we'll use this for the website.",
            )

        except Exception as e:
            logger.error(f"Error processing photo: {e}")
            say(channel=channel_id, text=f"Error processing photo: {e}")

    # Button to open the onboarding form
    @app.action("open_onboarding_form")
    def handle_open_form(ack, body, client: WebClient):
        """Handle the button to open the onboarding form."""
        ack()
        user_id = body["user"]["id"]
        request = get_request(user_id)

        if request:
            _open_onboarding_form(client, body["trigger_id"], request)


def _build_welcome_message(user_name: str) -> list:
    """Build the welcome message blocks."""
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f":wave: *Welcome to the Contextual Dynamics Lab, {user_name}!*",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "I'm the CDL onboarding bot. I'll help you get set up with:\n\n"
                "• *GitHub:* Access to the ContextLab organization\n"
                "• *Calendars:* Access to lab calendars\n"
                "• *Website:* Adding your profile to context-lab.com",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "To get started, I'll need some information from you. Click the button below to fill out the form.",
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "text": {"type": "plain_text", "text": "Start Onboarding"},
                    "style": "primary",
                    "action_id": "open_onboarding_form",
                }
            ],
        },
    ]


def _open_onboarding_form(client: WebClient, trigger_id: str, request: OnboardingRequest):
    """Open the onboarding information form modal."""
    try:
        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": "onboarding_form",
                "title": {"type": "plain_text", "text": "CDL Onboarding"},
                "submit": {"type": "plain_text", "text": "Submit"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "blocks": [
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "Please provide the following information for your CDL profile.",
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "github_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "github_input",
                            "placeholder": {
                                "type": "plain_text",
                                "text": "e.g., octocat",
                            },
                        },
                        "label": {
                            "type": "plain_text",
                            "text": "GitHub Username",
                        },
                        "hint": {
                            "type": "plain_text",
                            "text": "Your GitHub username (not email). We'll invite you to the ContextLab organization.",
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "bio_block",
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "bio_input",
                            "multiline": True,
                            "placeholder": {
                                "type": "plain_text",
                                "text": "Tell us about yourself and your research interests...",
                            },
                        },
                        "label": {
                            "type": "plain_text",
                            "text": "Short Bio",
                        },
                        "hint": {
                            "type": "plain_text",
                            "text": "3-4 sentences about you. We'll edit it for style consistency.",
                        },
                    },
                    {
                        "type": "input",
                        "block_id": "website_block",
                        "optional": True,
                        "element": {
                            "type": "plain_text_input",
                            "action_id": "website_input",
                            "placeholder": {
                                "type": "plain_text",
                                "text": "https://your-website.com",
                            },
                        },
                        "label": {
                            "type": "plain_text",
                            "text": "Personal Website (optional)",
                        },
                        "hint": {
                            "type": "plain_text",
                            "text": "If you have a personal website, we'll link to it from your profile.",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": ":camera: *Photo:* After submitting this form, please upload a profile photo by sending it as a message in this conversation.",
                        },
                    },
                ],
            },
        )
    except SlackApiError as e:
        logger.error(f"Error opening modal: {e}")


def _send_approval_request(
    client: WebClient,
    config: Config,
    request: OnboardingRequest,
    github_service: GitHubService,
):
    """Send an approval request to the admin."""
    # Get GitHub teams for the checkboxes
    teams = github_service.get_teams()

    # Build team options
    team_options = []
    initial_options = []
    for team in teams:
        option = {
            "text": {"type": "plain_text", "text": team["name"]},
            "value": str(team["id"]),
        }
        team_options.append(option)
        if team["name"] == config.github.default_team:
            initial_options.append(option)

    # Build the approval message
    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":clipboard: New Onboarding Request",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{request.name}* (<@{request.slack_user_id}>) has submitted their onboarding information.",
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*GitHub Username:* `{request.github_username}`\n"
                f"*Email:* {request.email or 'Not provided'}\n"
                f"*Website:* {request.website_url or 'None'}",
            },
        },
    ]

    # Add bio section
    if request.bio_raw:
        bio_preview = request.bio_raw[:300] + "..." if len(request.bio_raw) > 300 else request.bio_raw
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Original Bio:*\n>{bio_preview}",
            },
        })

    if request.bio_edited:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Edited Bio (for website):*\n>{request.bio_edited}",
            },
        })

    blocks.append({"type": "divider"})

    # GitHub team selection
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "*Select GitHub teams to add this member to:*",
        },
        "accessory": {
            "type": "checkboxes",
            "action_id": "github_teams_select",
            "options": team_options[:10],  # Slack limits to 10 options
            "initial_options": initial_options,
        },
    })

    # Calendar permissions (using default values)
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": "*Calendar Permissions (defaults):*\n"
            "• Contextual Dynamics Lab: Read-only\n"
            "• Out of lab: Edit\n"
            "• CDL Resources: Edit",
        },
    })

    blocks.append({"type": "divider"})

    # Action buttons
    blocks.append({
        "type": "actions",
        "block_id": f"approval_actions_{request.slack_user_id}",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Approve"},
                "style": "primary",
                "action_id": "approve_onboarding",
                "value": request.slack_user_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Reject"},
                "style": "danger",
                "action_id": "reject_onboarding",
                "value": request.slack_user_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Request Changes"},
                "action_id": "request_changes_onboarding",
                "value": request.slack_user_id,
            },
        ],
    })

    # Send to admin
    try:
        result = client.chat_postMessage(
            channel=config.slack.admin_user_id,
            text=f"New onboarding request from {request.name}",
            blocks=blocks,
        )
        request.admin_approval_message_ts = result["ts"]
        save_request(request)
    except SlackApiError as e:
        logger.error(f"Error sending approval request: {e}")
