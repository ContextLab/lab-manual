"""
Workflow Builder message listener.

Listens for messages from the existing "Join the lab!" Workflow Builder workflow
and processes them to create onboarding requests.

The workflow sends two messages to the admin:
1. Step 4: GitHub username and Gmail address
2. Step 7: Name, bio, and personal website

This handler listens for both messages, combines the data, and sends
an interactive approval form to the admin.
"""

import logging
import re
from typing import Optional

from slack_bolt import App
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..config import Config
from ..models.onboarding_request import OnboardingRequest, OnboardingStatus
from ..services.github_service import GitHubService
from ..services.bio_service import BioService
from .onboard import get_request, save_request, delete_request

logger = logging.getLogger(__name__)

# Temporary storage for partial onboarding data (keyed by Slack user ID)
# This holds the first form submission until we receive the second
_partial_requests: dict[str, dict] = {}


def get_partial_request(user_id: str) -> Optional[dict]:
    """Get partial onboarding data for a user."""
    return _partial_requests.get(user_id)


def save_partial_request(user_id: str, data: dict):
    """Save partial onboarding data."""
    _partial_requests[user_id] = data


def delete_partial_request(user_id: str):
    """Delete partial onboarding data."""
    _partial_requests.pop(user_id, None)


def register_workflow_listener_handlers(app: App, config: Config):
    """Register handlers that listen for Workflow Builder output messages."""

    github_service = GitHubService(config.github.token, config.github.org_name)
    bio_service = None
    if config.anthropic:
        bio_service = BioService(config.anthropic.api_key, config.anthropic.model)

    @app.event("message")
    def handle_workflow_message(event, client: WebClient, say):
        """
        Listen for messages from Workflow Builder.

        We're looking for messages sent to the admin that match the pattern:
        "CDL Onboarding submission from [Person]"
        """
        # Only process messages sent to the admin
        channel = event.get("channel")
        channel_type = event.get("channel_type")

        # Check if this is a DM to the admin (im = direct message)
        if channel_type != "im":
            return

        # Get the message text
        text = event.get("text", "")

        # Check if this is a workflow message
        if "CDL Onboarding" not in text and "submission from" not in text:
            return

        # Check for bot_id to identify workflow messages (workflows post as bots)
        bot_id = event.get("bot_id")
        if not bot_id:
            return

        logger.info(f"Detected potential workflow message: {text[:100]}...")

        # Try to parse the message
        # The workflow sends messages with the person who submitted as a link
        # Format: "CDL Onboarding submission from <@U12345|username>"

        # Extract the user ID from the message
        user_match = re.search(r"submission from\s+<@([A-Z0-9]+)", text)
        if not user_match:
            logger.debug("Could not extract user ID from workflow message")
            return

        submitter_id = user_match.group(1)
        logger.info(f"Workflow submission from user: {submitter_id}")

        # Parse the form fields from the message
        # Messages have format like:
        # "What's your GitHub username?\nAnswer to: What's your GitHub username?"
        # or similar patterns

        parsed_data = _parse_workflow_message(text)

        if not parsed_data:
            logger.warning(f"Could not parse workflow message fields")
            return

        logger.info(f"Parsed workflow data: {parsed_data}")

        # Determine which form this is (first or second)
        has_github = "github_username" in parsed_data
        has_bio = "bio" in parsed_data or "name" in parsed_data

        if has_github and not has_bio:
            # This is the first form (Step 4) - GitHub and email
            logger.info(f"Received first workflow form for {submitter_id}")

            # Store partial data
            partial = get_partial_request(submitter_id) or {}
            partial.update(parsed_data)
            partial["submitter_id"] = submitter_id
            save_partial_request(submitter_id, partial)

            # Acknowledge receipt but wait for second form
            try:
                client.chat_postMessage(
                    channel=channel,
                    text=f":white_check_mark: Received GitHub info for <@{submitter_id}>. Waiting for website info...",
                    thread_ts=event.get("ts"),  # Reply in thread
                )
            except SlackApiError as e:
                logger.error(f"Error sending acknowledgment: {e}")

        elif has_bio:
            # This is the second form (Step 7) - name, bio, website
            logger.info(f"Received second workflow form for {submitter_id}")

            # Get partial data from first form
            partial = get_partial_request(submitter_id) or {}
            partial.update(parsed_data)
            partial["submitter_id"] = submitter_id

            # Now we have all the data - process it
            _process_complete_workflow_submission(
                client, config, submitter_id, partial,
                github_service, bio_service, channel
            )

            # Clean up partial data
            delete_partial_request(submitter_id)

        else:
            # Unknown form type - store what we have
            logger.warning(f"Unknown workflow form type, storing data")
            partial = get_partial_request(submitter_id) or {}
            partial.update(parsed_data)
            partial["submitter_id"] = submitter_id
            save_partial_request(submitter_id, partial)


def _parse_workflow_message(text: str) -> dict:
    """
    Parse form fields from a Workflow Builder message.

    The message format is typically:
    "CDL Onboarding submission from <@U123|name>

    What's your GitHub username?
    Answer to: What's your GitHub username?

    What's your GMail address (include @gmail.com or @dartmouth.edu)?
    Answer to: What's your GMail address..."

    Returns a dict with parsed field names and values.
    """
    result = {}

    # Split by lines and look for question/answer pairs
    lines = text.split("\n")

    current_question = None
    for i, line in enumerate(lines):
        line = line.strip()

        # Look for GitHub username
        if "github username" in line.lower():
            # Next line starting with "Answer to:" contains the value
            for j in range(i + 1, min(i + 3, len(lines))):
                next_line = lines[j].strip()
                if next_line.startswith("Answer to:"):
                    # The answer is after "Answer to: [question]?"
                    # But actually the answer IS the repeated text - extract it
                    # Format: "Answer to: What's your GitHub username?"
                    # The actual answer comes after this in the Slack message rendering
                    pass
                elif next_line and not next_line.startswith("What") and "?" not in next_line:
                    # This might be the actual answer
                    result["github_username"] = next_line
                    break

        # Look for email/Gmail
        if "gmail" in line.lower() or "email" in line.lower():
            for j in range(i + 1, min(i + 3, len(lines))):
                next_line = lines[j].strip()
                if next_line and "@" in next_line and not next_line.startswith("Answer"):
                    result["email"] = next_line
                    break
                elif next_line.startswith("Answer to:") and "@" in next_line:
                    # Extract email from answer line
                    email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', next_line)
                    if email_match:
                        result["email"] = email_match.group(0)
                        break

        # Look for name
        if "name listed on the lab website" in line.lower() or "how do you want your name" in line.lower():
            for j in range(i + 1, min(i + 3, len(lines))):
                next_line = lines[j].strip()
                if next_line and not next_line.startswith("Answer") and not next_line.startswith("What") and not next_line.startswith("Please") and not next_line.startswith("Do you"):
                    result["name"] = next_line
                    break

        # Look for bio
        if "bio" in line.lower() and "sentence" in line.lower():
            for j in range(i + 1, min(i + 3, len(lines))):
                next_line = lines[j].strip()
                if next_line and not next_line.startswith("Answer") and not next_line.startswith("What") and not next_line.startswith("Do you") and len(next_line) > 20:
                    result["bio"] = next_line
                    break

        # Look for website
        if "personal website" in line.lower():
            for j in range(i + 1, min(i + 3, len(lines))):
                next_line = lines[j].strip()
                if next_line and ("http" in next_line or "www" in next_line or next_line == "blank" or not next_line.startswith("Answer")):
                    if next_line.lower() != "blank" and next_line:
                        result["website_url"] = next_line
                    break

    # Alternative parsing: look for the cyan/blue "Answer to:" formatted text
    # In Slack's rendering, the answers appear as linked text
    # Pattern: field label followed by cyan text with the answer

    # Try regex patterns for common formats
    github_patterns = [
        r"GitHub username[?\s]*\n*(?:Answer to:[^\n]*)?\n*([A-Za-z0-9_-]+)",
        r"GitHub username[?\s]*:?\s*([A-Za-z0-9_-]+)",
    ]
    for pattern in github_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match and "github_username" not in result:
            result["github_username"] = match.group(1).strip()
            break

    email_patterns = [
        r"(?:gmail|email)[^@\n]*?(?:Answer to:[^\n]*)?\n*([\w\.-]+@[\w\.-]+\.\w+)",
        r"([\w\.-]+@(?:gmail\.com|dartmouth\.edu))",
    ]
    for pattern in email_patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match and "email" not in result:
            result["email"] = match.group(1).strip()
            break

    return result


def _process_complete_workflow_submission(
    client: WebClient,
    config: Config,
    submitter_id: str,
    data: dict,
    github_service: GitHubService,
    bio_service: Optional[BioService],
    admin_channel: str,
):
    """Process a complete workflow submission and send approval request."""

    github_username = data.get("github_username", "")
    email = data.get("email", "")
    name = data.get("name", "")
    bio_raw = data.get("bio", "")
    website_url = data.get("website_url", "")

    logger.info(f"Processing complete workflow submission for {submitter_id}")
    logger.info(f"  GitHub: {github_username}, Email: {email}")
    logger.info(f"  Name: {name}, Bio: {bio_raw[:50] if bio_raw else 'N/A'}...")

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

    # Validate GitHub username
    if github_username:
        is_valid, error_msg = github_service.validate_username(github_username)
        if not is_valid:
            try:
                client.chat_postMessage(
                    channel=admin_channel,
                    text=f":warning: GitHub username `{github_username}` for <@{submitter_id}> is invalid: {error_msg}",
                )
            except SlackApiError:
                pass
            # Continue anyway - admin can handle it

    # Open DM channel with the new member
    try:
        dm_response = client.conversations_open(users=[submitter_id])
        dm_channel = dm_response["channel"]["id"]
    except SlackApiError as e:
        logger.error(f"Error opening DM with user: {e}")
        dm_channel = None

    # Create onboarding request
    request = OnboardingRequest(
        slack_user_id=submitter_id,
        slack_channel_id=dm_channel or admin_channel,
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

    request.update_status(OnboardingStatus.PENDING_APPROVAL)
    save_request(request)

    # Send approval request to admin
    _send_workflow_approval_request(client, config, request, github_service, admin_channel)


def _send_workflow_approval_request(
    client: WebClient,
    config: Config,
    request: OnboardingRequest,
    github_service: GitHubService,
    channel: str,
):
    """Send an approval request to the admin channel."""

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
                "text": ":clipboard: New Member - Workflow Submission",
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*{request.name}* (<@{request.slack_user_id}>) submitted the \"Join the lab\" workflow.",
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*GitHub Username:* `{request.github_username or 'Not provided'}`\n"
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
                "text": f"*Edited Bio (CDL style):*\n>{request.bio_edited}",
            },
        })

    # Photo status
    blocks.append({
        "type": "section",
        "text": {
            "type": "mrkdwn",
            "text": ":camera: *Photo:* Waiting for DM (workflow asks them to send it to you)",
        },
    })

    blocks.append({"type": "divider"})

    # GitHub team selection
    if team_options:
        blocks.append({
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": "*Select GitHub teams:*",
            },
            "accessory": {
                "type": "checkboxes",
                "action_id": "github_teams_select",
                "options": team_options[:10],
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

    # Action buttons
    blocks.append({
        "type": "actions",
        "block_id": f"workflow_approval_actions_{request.slack_user_id}",
        "elements": [
            {
                "type": "button",
                "text": {"type": "plain_text", "text": "Approve & Send Invites"},
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
            channel=channel,
            text=f"New member request from {request.name}",
            blocks=blocks,
        )
        request.admin_approval_message_ts = result["ts"]
        save_request(request)
        logger.info(f"Sent approval request for {request.name}")
    except SlackApiError as e:
        logger.error(f"Error sending approval request: {e}")
