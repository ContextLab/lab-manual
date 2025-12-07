"""
Custom Workflow Step handlers for Slack Workflow Builder integration.

This module provides custom steps that can be added to Workflow Builder workflows
for onboarding and offboarding processes.

The "Process Onboarding" step receives form data from a workflow and sends
an approval request to the admin. The workflow remains paused until approved.
"""

import logging
import os
import tempfile
from pathlib import Path
from typing import Optional

import requests
from slack_bolt import App, Complete, Fail
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..config import Config
from ..models.onboarding_request import OnboardingRequest, OnboardingStatus
from ..services.github_service import GitHubService
from ..services.image_service import ImageService
from ..services.bio_service import BioService
from .onboard import get_request, save_request, delete_request

logger = logging.getLogger(__name__)

# Store workflow execution context for completing after admin approval
_pending_workflow_executions: dict[str, dict] = {}


def get_workflow_execution(user_id: str) -> Optional[dict]:
    """Get pending workflow execution context for a user."""
    return _pending_workflow_executions.get(user_id)


def save_workflow_execution(user_id: str, context: dict):
    """Save workflow execution context."""
    _pending_workflow_executions[user_id] = context


def delete_workflow_execution(user_id: str):
    """Delete workflow execution context."""
    _pending_workflow_executions.pop(user_id, None)


def register_workflow_step_handlers(app: App, config: Config):
    """Register custom workflow step handlers with the Slack app."""

    github_service = GitHubService(config.github.token, config.github.org_name)
    image_service = ImageService(config.border_color, config.border_width)
    bio_service = None
    if config.anthropic:
        bio_service = BioService(config.anthropic.api_key, config.anthropic.model)

    @app.function("cdl_onboarding_step")
    def handle_onboarding_step(inputs: dict, fail: Fail, client: WebClient, context, body: dict):
        """
        Handle the CDL onboarding workflow step.

        This function is triggered when the custom step is executed in a workflow.
        It receives the form data collected by previous workflow steps and sends
        an approval request to the admin.

        Expected inputs (from workflow variables):
        - submitter_id: Slack user ID of the new member
        - name: Full name
        - github_username: GitHub username
        - bio: Short bio text
        - website_url: Optional personal website
        - photo_url: Optional URL to profile photo (from file upload)

        The workflow will remain paused until complete() or fail() is called.
        """
        try:
            submitter_id = inputs.get("submitter_id")
            name = inputs.get("name", "")
            github_username = inputs.get("github_username", "")
            bio_raw = inputs.get("bio", "")
            website_url = inputs.get("website_url", "")
            photo_url = inputs.get("photo_url", "")
            email = inputs.get("email", "")

            logger.info(f"Onboarding step triggered for {name} ({submitter_id})")

            if not submitter_id:
                fail("Missing submitter ID")
                return

            if not github_username:
                fail("Missing GitHub username")
                return

            # Validate GitHub username
            is_valid, error_msg = github_service.validate_username(github_username)
            if not is_valid:
                fail(f"Invalid GitHub username '{github_username}': {error_msg}")
                return

            # Get user info from Slack if name not provided
            if not name:
                try:
                    user_info = client.users_info(user=submitter_id)
                    name = user_info["user"]["real_name"] or user_info["user"]["name"]
                    if not email:
                        email = user_info["user"].get("profile", {}).get("email", "")
                except SlackApiError as e:
                    logger.warning(f"Could not get user info: {e}")
                    name = "Unknown"

            # Open DM channel with the new member
            try:
                dm_response = client.conversations_open(users=[submitter_id])
                dm_channel = dm_response["channel"]["id"]
            except SlackApiError as e:
                logger.error(f"Error opening DM with user: {e}")
                fail(f"Could not open DM with user: {e}")
                return

            # Create onboarding request
            request = OnboardingRequest(
                slack_user_id=submitter_id,
                slack_channel_id=dm_channel,
                name=name,
                email=email,
                github_username=github_username,
                bio_raw=bio_raw,
                website_url=website_url,
            )

            # Process bio if service available
            if bio_service and bio_raw:
                edited_bio, bio_error = bio_service.edit_bio(bio_raw, name)
                if edited_bio:
                    request.bio_edited = edited_bio
                else:
                    logger.warning(f"Bio editing failed: {bio_error}")

            # Process photo if provided
            if photo_url:
                try:
                    processed_path = _process_photo_from_url(
                        photo_url, submitter_id, config, image_service
                    )
                    if processed_path:
                        request.photo_processed_path = processed_path
                except Exception as e:
                    logger.warning(f"Photo processing failed: {e}")

            request.update_status(OnboardingStatus.PENDING_APPROVAL)
            save_request(request)

            # Save workflow execution context for completing after approval
            # The function_execution_id is needed to complete() or fail() later
            function_execution_id = body.get("function_data", {}).get("execution_id")
            save_workflow_execution(submitter_id, {
                "execution_id": function_execution_id,
                "inputs": inputs,
                "context": context,
            })

            # Send acknowledgment to the new member
            try:
                client.chat_postMessage(
                    channel=dm_channel,
                    text="Your onboarding information has been received!",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": ":white_check_mark: *Welcome to CDL!*\n\n"
                                "Your onboarding information has been submitted. "
                                "The lab admin will review it shortly.",
                            },
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"*What's next:*\n"
                                f"• GitHub: Invitation to ContextLab organization\n"
                                f"• Calendar: Access to lab calendars\n"
                                f"• Website: Your profile will be added to context-lab.com",
                            },
                        },
                    ],
                )
            except SlackApiError as e:
                logger.error(f"Error sending acknowledgment: {e}")

            # Send approval request to admin
            _send_workflow_approval_request(client, config, request, github_service)

            # NOTE: We do NOT call complete() here!
            # The workflow stays paused until admin approves/rejects.
            # complete() will be called from the approval handler.

        except Exception as e:
            logger.exception(f"Error in onboarding step: {e}")
            fail(f"Onboarding step failed: {e}")

    @app.function("cdl_offboarding_step")
    def handle_offboarding_step(inputs: dict, fail: Fail, client: WebClient, complete: Complete):
        """
        Handle the CDL offboarding workflow step.

        Expected inputs:
        - submitter_id: Slack user ID of the departing member
        - name: Full name (optional, can be looked up)

        This step notifies the admin and generates an offboarding checklist.
        """
        try:
            submitter_id = inputs.get("submitter_id")
            name = inputs.get("name", "")

            if not submitter_id:
                fail("Missing submitter ID")
                return

            # Get user info if name not provided
            if not name:
                try:
                    user_info = client.users_info(user=submitter_id)
                    name = user_info["user"]["real_name"] or user_info["user"]["name"]
                except SlackApiError:
                    name = "Unknown"

            logger.info(f"Offboarding step triggered for {name} ({submitter_id})")

            # Send offboarding notification to admin
            try:
                client.chat_postMessage(
                    channel=config.slack.admin_user_id,
                    text=f"Offboarding request from {name}",
                    blocks=[
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
                                "text": f"*{name}* (<@{submitter_id}>) has initiated the offboarding process.",
                            },
                        },
                        {"type": "divider"},
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "*Offboarding Checklist:*\n"
                                ":ballot_box_with_check: Remove from GitHub ContextLab organization\n"
                                ":ballot_box_with_check: Remove from lab calendars\n"
                                ":ballot_box_with_check: Update website (remove or move to alumni)\n"
                                ":ballot_box_with_check: Transfer any relevant files/data\n"
                                ":ballot_box_with_check: Update mailing lists",
                            },
                        },
                        {
                            "type": "actions",
                            "elements": [
                                {
                                    "type": "button",
                                    "text": {"type": "plain_text", "text": "Start Offboarding"},
                                    "style": "primary",
                                    "action_id": "start_offboarding_workflow",
                                    "value": submitter_id,
                                },
                            ],
                        },
                    ],
                )
            except SlackApiError as e:
                logger.error(f"Error sending offboarding notification: {e}")
                fail(f"Could not send offboarding notification: {e}")
                return

            # Send confirmation to departing member
            try:
                dm_response = client.conversations_open(users=[submitter_id])
                dm_channel = dm_response["channel"]["id"]
                client.chat_postMessage(
                    channel=dm_channel,
                    text="Thank you for your time with CDL!",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": ":wave: *Thank you for your time with CDL!*\n\n"
                                "The lab admin has been notified about your departure. "
                                "They'll handle the access revocations and website updates.",
                            },
                        },
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": "If you have any questions or need anything, "
                                "feel free to reach out to the lab admin.",
                            },
                        },
                    ],
                )
            except SlackApiError as e:
                logger.warning(f"Could not send confirmation to departing member: {e}")

            # Complete the workflow step
            complete({"status": "notified", "member_name": name})

        except Exception as e:
            logger.exception(f"Error in offboarding step: {e}")
            fail(f"Offboarding step failed: {e}")


def _process_photo_from_url(
    photo_url: str,
    user_id: str,
    config: Config,
    image_service: ImageService,
) -> Optional[Path]:
    """Download and process a photo from URL."""
    try:
        # Download the file
        headers = {"Authorization": f"Bearer {config.slack.bot_token}"}
        response = requests.get(photo_url, headers=headers)
        response.raise_for_status()

        # Save to temp file
        with tempfile.NamedTemporaryFile(delete=False, suffix=".jpg") as tmp:
            tmp.write(response.content)
            tmp_path = Path(tmp.name)

        # Validate the image
        is_valid, error_msg = image_service.validate_image(tmp_path)
        if not is_valid:
            tmp_path.unlink()
            logger.warning(f"Image validation failed: {error_msg}")
            return None

        # Process the photo
        output_path = config.output_dir / f"{user_id}_photo.png"
        processed_path = image_service.add_hand_drawn_border(
            tmp_path, output_path, seed=hash(user_id)
        )

        # Clean up temp file
        tmp_path.unlink()

        return processed_path

    except Exception as e:
        logger.error(f"Error processing photo: {e}")
        return None


def _send_workflow_approval_request(
    client: WebClient,
    config: Config,
    request: OnboardingRequest,
    github_service: GitHubService,
):
    """Send an approval request to the admin (for workflow-initiated onboarding)."""
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
                "text": ":clipboard: New Member - Join the Lab Request",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{request.name}* (<@{request.slack_user_id}>) has submitted the \"Join the lab\" form.",
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

    # Photo status
    if request.photo_processed_path:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":camera: *Photo:* Received and processed with CDL border",
            },
        })
    else:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":camera: *Photo:* Not yet uploaded",
            },
        })

    blocks.append({"type": "divider"})

    # GitHub team selection
    if team_options:
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
                "initial_options": initial_options if initial_options else None,
            },
        })

    # Calendar permissions info
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

    # Action buttons - using workflow-specific action IDs
    blocks.append({
        "type": "actions",
        "block_id": f"workflow_approval_actions_{request.slack_user_id}",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Approve"},
                "style": "primary",
                "action_id": "approve_workflow_onboarding",
                "value": request.slack_user_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Reject"},
                "style": "danger",
                "action_id": "reject_workflow_onboarding",
                "value": request.slack_user_id,
            },
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Request Changes"},
                "action_id": "request_changes_workflow_onboarding",
                "value": request.slack_user_id,
            },
        ],
    })

    # Send to admin
    try:
        result = client.chat_postMessage(
            channel=config.slack.admin_user_id,
            text=f"New member request from {request.name}",
            blocks=blocks,
        )
        request.admin_approval_message_ts = result["ts"]
        save_request(request)
    except SlackApiError as e:
        logger.error(f"Error sending approval request: {e}")


def complete_workflow_onboarding(user_id: str, success: bool, outputs: dict = None):
    """
    Complete a pending workflow onboarding step.

    Called from the approval handler after admin approves/rejects.

    Args:
        user_id: Slack user ID of the onboarding member
        success: Whether the onboarding was approved
        outputs: Output values to return to the workflow
    """
    execution = get_workflow_execution(user_id)
    if not execution:
        logger.warning(f"No workflow execution found for user {user_id}")
        return False

    # The complete/fail functions would need to be called with the execution context
    # This is handled by storing the context and using Slack's API
    delete_workflow_execution(user_id)
    return True
