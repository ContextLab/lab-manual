"""
Approval workflow handlers for Slack.

Handles admin approval/rejection of onboarding requests.
"""

import logging
import re
from typing import Optional

from slack_bolt import App
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..config import Config, GoogleCalendarConfig
from ..models.onboarding_request import OnboardingRequest, OnboardingStatus
from ..services.github_service import GitHubService
from ..services.calendar_service import CalendarService
from .onboard import get_request, save_request, delete_request

logger = logging.getLogger(__name__)


def register_approval_handlers(app: App, config: Config):
    """Register all approval-related handlers with the Slack app."""

    github_service = GitHubService(config.github.token, config.github.org_name)

    calendar_service = None
    if config.google_calendar:
        calendar_service = CalendarService(
            config.google_calendar.credentials_file,
            config.google_calendar.calendars,
        )

    @app.action("approve_onboarding")
    def handle_approve(ack, body, client: WebClient, action):
        """Handle approval of an onboarding request."""
        ack()

        user_id = action["value"]
        admin_id = body["user"]["id"]

        # Verify admin
        if admin_id != config.slack.admin_user_id:
            return

        request = get_request(user_id)
        if not request:
            logger.error(f"No request found for user {user_id}")
            return

        # Get selected teams from the message
        # Parse from the state or use defaults
        selected_team_ids = _get_selected_teams(body)

        request.github_teams = selected_team_ids
        request.approved_by = admin_id
        request.update_status(OnboardingStatus.GITHUB_PENDING)
        save_request(request)

        # Update the approval message
        _update_approval_message(client, body, "Approved", request)

        # Process the approval
        _process_approval(client, config, request, github_service, calendar_service)

    @app.action("reject_onboarding")
    def handle_reject(ack, body, client: WebClient, action):
        """Handle rejection of an onboarding request."""
        ack()

        user_id = action["value"]
        admin_id = body["user"]["id"]

        # Verify admin
        if admin_id != config.slack.admin_user_id:
            return

        request = get_request(user_id)
        if not request:
            return

        request.update_status(OnboardingStatus.REJECTED)
        save_request(request)

        # Update the approval message
        _update_approval_message(client, body, "Rejected", request)

        # Notify the user
        try:
            client.chat_postMessage(
                channel=request.slack_channel_id,
                text="Your onboarding request was not approved. Please contact the lab admin for more information.",
            )
        except SlackApiError as e:
            logger.error(f"Error notifying user of rejection: {e}")

        # Clean up
        delete_request(user_id)

    @app.action("request_changes_onboarding")
    def handle_request_changes(ack, body, client: WebClient, action):
        """Handle request for changes to an onboarding request."""
        ack()

        user_id = action["value"]
        admin_id = body["user"]["id"]

        # Verify admin
        if admin_id != config.slack.admin_user_id:
            return

        request = get_request(user_id)
        if not request:
            return

        # Open a modal for the admin to specify what changes are needed
        try:
            client.views_open(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "callback_id": f"request_changes_modal_{user_id}",
                    "private_metadata": user_id,
                    "title": {"type": "plain_text", "text": "Request Changes"},
                    "submit": {"type": "plain_text", "text": "Send"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": [
                        {
                            "type": "input",
                            "block_id": "changes_block",
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "changes_input",
                                "multiline": True,
                                "placeholder": {
                                    "type": "plain_text",
                                    "text": "Describe the changes needed...",
                                },
                            },
                            "label": {
                                "type": "plain_text",
                                "text": "What changes are needed?",
                            },
                        },
                    ],
                },
            )
        except SlackApiError as e:
            logger.error(f"Error opening changes modal: {e}")

    @app.view(re.compile(r"request_changes_modal_.*"))
    def handle_changes_modal(ack, body, client: WebClient, view):
        """Handle submission of the request changes modal."""
        ack()

        user_id = view["private_metadata"]
        request = get_request(user_id)
        if not request:
            return

        changes_text = view["state"]["values"]["changes_block"]["changes_input"]["value"]

        request.update_status(OnboardingStatus.PENDING_INFO)
        save_request(request)

        # Notify the user
        try:
            client.chat_postMessage(
                channel=request.slack_channel_id,
                text="The admin has requested some changes to your onboarding information.",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": ":memo: *Changes Requested*\n\nThe lab admin has requested the following changes:",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f">{changes_text}",
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Update Information"},
                                "action_id": "open_onboarding_form",
                            },
                        ],
                    },
                ],
            )
        except SlackApiError as e:
            logger.error(f"Error notifying user of changes: {e}")

    @app.action("github_teams_select")
    def handle_teams_select(ack, body):
        """Handle GitHub team selection (just acknowledge, we'll read the value on approval)."""
        ack()

    # ========== Workflow-initiated onboarding approval handlers ==========
    # These handlers are for approving onboarding requests that came through
    # Workflow Builder workflows (member-initiated) rather than slash commands.

    @app.action("approve_workflow_onboarding")
    def handle_workflow_approve(ack, body, client: WebClient, action, complete, fail):
        """Handle approval of a workflow-initiated onboarding request."""
        ack()

        user_id = action["value"]
        admin_id = body["user"]["id"]

        # Verify admin
        if admin_id != config.slack.admin_user_id:
            return

        request = get_request(user_id)
        if not request:
            logger.error(f"No request found for user {user_id}")
            return

        # Get selected teams from the message
        selected_team_ids = _get_selected_teams(body)

        request.github_teams = selected_team_ids
        request.approved_by = admin_id
        request.update_status(OnboardingStatus.GITHUB_PENDING)
        save_request(request)

        # Update the approval message
        _update_approval_message(client, body, "Approved", request)

        # Process the approval
        _process_approval(client, config, request, github_service, calendar_service)

        # Complete the workflow step (if it came from a workflow)
        try:
            from .workflow_step import get_workflow_execution, delete_workflow_execution
            execution = get_workflow_execution(user_id)
            if execution and complete:
                complete({
                    "status": "approved",
                    "github_username": request.github_username,
                    "name": request.name,
                })
                delete_workflow_execution(user_id)
        except Exception as e:
            logger.warning(f"Could not complete workflow step: {e}")

    @app.action("reject_workflow_onboarding")
    def handle_workflow_reject(ack, body, client: WebClient, action, complete, fail):
        """Handle rejection of a workflow-initiated onboarding request."""
        ack()

        user_id = action["value"]
        admin_id = body["user"]["id"]

        # Verify admin
        if admin_id != config.slack.admin_user_id:
            return

        request = get_request(user_id)
        if not request:
            return

        request.update_status(OnboardingStatus.REJECTED)
        save_request(request)

        # Update the approval message
        _update_approval_message(client, body, "Rejected", request)

        # Notify the user
        try:
            client.chat_postMessage(
                channel=request.slack_channel_id,
                text="Your onboarding request was not approved. Please contact the lab admin for more information.",
            )
        except SlackApiError as e:
            logger.error(f"Error notifying user of rejection: {e}")

        # Fail the workflow step (if it came from a workflow)
        try:
            from .workflow_step import get_workflow_execution, delete_workflow_execution
            execution = get_workflow_execution(user_id)
            if execution and fail:
                fail("Onboarding request was rejected by admin")
                delete_workflow_execution(user_id)
        except Exception as e:
            logger.warning(f"Could not fail workflow step: {e}")

        # Clean up
        delete_request(user_id)

    @app.action("request_changes_workflow_onboarding")
    def handle_workflow_request_changes(ack, body, client: WebClient, action):
        """Handle request for changes to a workflow-initiated onboarding request."""
        ack()

        user_id = action["value"]
        admin_id = body["user"]["id"]

        # Verify admin
        if admin_id != config.slack.admin_user_id:
            return

        request = get_request(user_id)
        if not request:
            return

        # Open a modal for the admin to specify what changes are needed
        try:
            client.views_open(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "callback_id": f"workflow_changes_modal_{user_id}",
                    "private_metadata": user_id,
                    "title": {"type": "plain_text", "text": "Request Changes"},
                    "submit": {"type": "plain_text", "text": "Send"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": [
                        {
                            "type": "input",
                            "block_id": "changes_block",
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "changes_input",
                                "multiline": True,
                                "placeholder": {
                                    "type": "plain_text",
                                    "text": "Describe the changes needed...",
                                },
                            },
                            "label": {
                                "type": "plain_text",
                                "text": "What changes are needed?",
                            },
                        },
                    ],
                },
            )
        except SlackApiError as e:
            logger.error(f"Error opening changes modal: {e}")

    @app.view(re.compile(r"workflow_changes_modal_.*"))
    def handle_workflow_changes_modal(ack, body, client: WebClient, view):
        """Handle submission of the workflow changes request modal."""
        ack()

        user_id = view["private_metadata"]
        request = get_request(user_id)
        if not request:
            return

        changes_text = view["state"]["values"]["changes_block"]["changes_input"]["value"]

        request.update_status(OnboardingStatus.PENDING_INFO)
        save_request(request)

        # Notify the user
        try:
            client.chat_postMessage(
                channel=request.slack_channel_id,
                text="The admin has requested some changes to your onboarding information.",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": ":memo: *Changes Requested*\n\nThe lab admin has requested the following changes:",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f">{changes_text}",
                        },
                    },
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": "Please reply to this message with the updated information, or re-run the \"Join the lab\" workflow.",
                        },
                    },
                ],
            )
        except SlackApiError as e:
            logger.error(f"Error notifying user of changes: {e}")

    @app.action("start_offboarding_workflow")
    def handle_start_offboarding(ack, body, client: WebClient, action):
        """Handle the start offboarding button from workflow notification."""
        ack()

        user_id = action["value"]
        admin_id = body["user"]["id"]

        # Verify admin
        if admin_id != config.slack.admin_user_id:
            return

        # Open modal to select what to revoke
        try:
            client.views_open(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "callback_id": f"offboarding_modal_{user_id}",
                    "private_metadata": user_id,
                    "title": {"type": "plain_text", "text": "Offboarding"},
                    "submit": {"type": "plain_text", "text": "Process"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"Select what access to revoke for <@{user_id}>:",
                            },
                        },
                        {
                            "type": "input",
                            "block_id": "revoke_options",
                            "element": {
                                "type": "checkboxes",
                                "action_id": "revoke_checkboxes",
                                "options": [
                                    {
                                        "text": {"type": "plain_text", "text": "Remove from GitHub org"},
                                        "value": "github",
                                    },
                                    {
                                        "text": {"type": "plain_text", "text": "Remove calendar access"},
                                        "value": "calendar",
                                    },
                                    {
                                        "text": {"type": "plain_text", "text": "Move to alumni on website"},
                                        "value": "website_alumni",
                                    },
                                ],
                                "initial_options": [
                                    {
                                        "text": {"type": "plain_text", "text": "Remove from GitHub org"},
                                        "value": "github",
                                    },
                                    {
                                        "text": {"type": "plain_text", "text": "Remove calendar access"},
                                        "value": "calendar",
                                    },
                                    {
                                        "text": {"type": "plain_text", "text": "Move to alumni on website"},
                                        "value": "website_alumni",
                                    },
                                ],
                            },
                            "label": {"type": "plain_text", "text": "Access to revoke"},
                        },
                    ],
                },
            )
        except SlackApiError as e:
            logger.error(f"Error opening offboarding modal: {e}")


def _get_selected_teams(body: dict) -> list[int]:
    """Extract selected team IDs from the message state."""
    try:
        state = body.get("state", {}).get("values", {})
        for block_id, block_data in state.items():
            if "github_teams_select" in block_data:
                selected = block_data["github_teams_select"].get("selected_options", [])
                return [int(opt["value"]) for opt in selected]
    except (KeyError, ValueError) as e:
        logger.warning(f"Error parsing team selection: {e}")

    return []


def _update_approval_message(client: WebClient, body: dict, status: str, request: OnboardingRequest):
    """Update the approval message to show the result."""
    channel = body["channel"]["id"]
    ts = body["message"]["ts"]

    try:
        client.chat_update(
            channel=channel,
            ts=ts,
            text=f"Onboarding request from {request.name} - {status}",
            blocks=[
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f":white_check_mark: *Onboarding Request - {status}*\n\n"
                        f"*Member:* {request.name} (<@{request.slack_user_id}>)\n"
                        f"*GitHub:* `{request.github_username}`",
                    },
                },
                {
                    "type": "context",
                    "elements": [
                        {
                            "type": "mrkdwn",
                            "text": f"Status: {request.status.value}",
                        },
                    ],
                },
            ],
        )
    except SlackApiError as e:
        logger.error(f"Error updating approval message: {e}")


def _process_approval(
    client: WebClient,
    config: Config,
    request: OnboardingRequest,
    github_service: GitHubService,
    calendar_service: Optional[CalendarService],
):
    """Process an approved onboarding request."""
    results = []
    errors = []

    # 1. Send GitHub invitation
    success, error = github_service.invite_user(
        request.github_username,
        request.github_teams,
    )
    if success:
        results.append(f":white_check_mark: GitHub invitation sent to `{request.github_username}`")
        request.github_invitation_sent = True
    else:
        errors.append(f":x: GitHub invitation failed: {error}")
    save_request(request)

    # 2. Send calendar invitations
    if calendar_service and request.email:
        request.update_status(OnboardingStatus.CALENDAR_PENDING)
        save_request(request)

        # Use default permissions
        permissions = GoogleCalendarConfig.DEFAULT_PERMISSIONS.copy()
        request.calendar_permissions = permissions

        calendar_results = calendar_service.share_multiple_calendars(
            email=request.email,
            calendar_permissions=permissions,
        )

        for calendar_name, (cal_success, cal_error) in calendar_results.items():
            if cal_success:
                results.append(f":white_check_mark: Calendar '{calendar_name}' shared")
            else:
                errors.append(f":x: Calendar '{calendar_name}' failed: {cal_error}")

        request.calendar_invites_sent = True
        save_request(request)
    else:
        if not calendar_service:
            errors.append(":warning: Calendar service not configured")
        if not request.email:
            errors.append(":warning: No email address for calendar invitations")

    # 3. Prepare website content
    request.update_status(OnboardingStatus.READY_FOR_WEBSITE)
    save_request(request)

    website_ready = bool(request.bio_edited and request.photo_processed_path)
    if website_ready:
        results.append(":white_check_mark: Photo and bio ready for website")
    else:
        missing = []
        if not request.bio_edited:
            missing.append("edited bio")
        if not request.photo_processed_path:
            missing.append("processed photo")
        errors.append(f":warning: Website content incomplete: missing {', '.join(missing)}")

    # Notify admin of results
    summary_blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": f"Onboarding Progress: {request.name}",
            },
        },
    ]

    if results:
        summary_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Completed:*\n" + "\n".join(results),
            },
        })

    if errors:
        summary_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Issues:*\n" + "\n".join(errors),
            },
        })

    # Add website update instructions if ready
    if website_ready:
        summary_blocks.append({"type": "divider"})
        summary_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Website Update:*\n"
                f"The processed photo has been saved to: `{request.photo_processed_path}`\n\n"
                f"*Edited bio:*\n>{request.bio_edited}",
            },
        })

    try:
        client.chat_postMessage(
            channel=config.slack.admin_user_id,
            text=f"Onboarding progress for {request.name}",
            blocks=summary_blocks,
        )
    except SlackApiError as e:
        logger.error(f"Error sending progress update: {e}")

    # Notify the new member
    member_blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":tada: *Your onboarding has been approved!*",
            },
        },
    ]

    if request.github_invitation_sent:
        member_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":octocat: *GitHub:* Check your email for an invitation to join the ContextLab organization. "
                "Once you accept, you'll have access to our repositories.",
            },
        })

    if request.calendar_invites_sent:
        member_blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":calendar: *Calendars:* You should receive invitations to the lab calendars shortly. "
                "Add them to your Google Calendar to stay up to date.",
            },
        })

    member_blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": ":globe_with_meridians: *Website:* Your profile will be added to context-lab.com soon!",
        },
    })

    try:
        client.chat_postMessage(
            channel=request.slack_channel_id,
            text="Your onboarding has been approved!",
            blocks=member_blocks,
        )
    except SlackApiError as e:
        logger.error(f"Error notifying member: {e}")

    # Mark as completed
    if not errors:
        request.update_status(OnboardingStatus.COMPLETED)
    save_request(request)
