"""
Startup queue processor for missed workflow submissions.

When the bot starts, this module scans recent messages in the admin's DM
for workflow submissions that may have arrived while the bot was offline.
It compares against stored requests to find and process any missed submissions.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

from slack_bolt import App
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from .config import Config
from .storage import get_request, get_storage

logger = logging.getLogger(__name__)

# File to track processed message timestamps
PROCESSED_MESSAGES_FILE = Path(__file__).parent / "data" / "processed_messages.json"

# File to store pending reprocess data (message content for button handler)
PENDING_REPROCESS_FILE = Path(__file__).parent / "data" / "pending_reprocess.json"

# How far back to scan (in days)
DEFAULT_LOOKBACK_DAYS = 7


def _load_pending_reprocess() -> dict:
    """Load pending reprocess data."""
    PENDING_REPROCESS_FILE.parent.mkdir(parents=True, exist_ok=True)
    if PENDING_REPROCESS_FILE.exists():
        try:
            return json.loads(PENDING_REPROCESS_FILE.read_text())
        except Exception:
            pass
    return {}


def _save_pending_reprocess(data: dict):
    """Save pending reprocess data."""
    try:
        PENDING_REPROCESS_FILE.write_text(json.dumps(data, indent=2))
    except Exception as e:
        logger.error(f"Error saving pending reprocess data: {e}")


class StartupQueueProcessor:
    """Processes missed workflow submissions on bot startup."""

    def __init__(self, client: WebClient, config: Config):
        self.client = client
        self.config = config
        self._processed_timestamps: set[str] = set()
        self._load_processed_timestamps()

    def _load_processed_timestamps(self):
        """Load set of already-processed message timestamps."""
        PROCESSED_MESSAGES_FILE.parent.mkdir(parents=True, exist_ok=True)
        if PROCESSED_MESSAGES_FILE.exists():
            try:
                data = json.loads(PROCESSED_MESSAGES_FILE.read_text())
                self._processed_timestamps = set(data.get("timestamps", []))
                logger.info(f"Loaded {len(self._processed_timestamps)} processed message timestamps")
            except Exception as e:
                logger.error(f"Error loading processed timestamps: {e}")
                self._processed_timestamps = set()

    def _save_processed_timestamps(self):
        """Save processed message timestamps to disk."""
        try:
            # Keep only recent timestamps (last 30 days worth) to prevent unbounded growth
            data = {"timestamps": list(self._processed_timestamps)[-10000:]}
            PROCESSED_MESSAGES_FILE.write_text(json.dumps(data, indent=2))
        except Exception as e:
            logger.error(f"Error saving processed timestamps: {e}")

    def mark_processed(self, message_ts: str):
        """Mark a message as processed."""
        self._processed_timestamps.add(message_ts)
        self._save_processed_timestamps()

    def is_processed(self, message_ts: str) -> bool:
        """Check if a message has already been processed."""
        return message_ts in self._processed_timestamps

    def scan_for_missed_submissions(
        self,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    ) -> list[dict]:
        """
        Scan admin's DMs for missed workflow submissions.

        Returns list of unprocessed workflow messages.
        """
        missed = []

        # Calculate the oldest timestamp to scan
        oldest = datetime.now() - timedelta(days=lookback_days)
        oldest_ts = str(oldest.timestamp())

        try:
            # Open DM with admin to get channel ID
            dm_response = self.client.conversations_open(
                users=[self.config.slack.admin_user_id]
            )
            admin_dm_channel = dm_response["channel"]["id"]

            # Fetch conversation history
            result = self.client.conversations_history(
                channel=admin_dm_channel,
                oldest=oldest_ts,
                limit=200,  # Reasonable limit for startup scan
            )

            messages = result.get("messages", [])
            logger.info(f"Scanning {len(messages)} messages from last {lookback_days} days")

            for msg in messages:
                # Skip if already processed
                msg_ts = msg.get("ts", "")
                if self.is_processed(msg_ts):
                    continue

                # Check if this is a workflow submission message
                text = msg.get("text", "")
                bot_id = msg.get("bot_id")

                # Workflow messages come from bots and contain specific text
                if not bot_id:
                    continue

                if "CDL Onboarding" not in text or "submission from" not in text:
                    continue

                # Extract submitter user ID
                user_match = re.search(r"submission from\s+<@([A-Z0-9]+)", text)
                if not user_match:
                    continue

                submitter_id = user_match.group(1)

                # Check if we already have a request for this user
                existing_request = get_request(submitter_id)
                if existing_request:
                    # Already have a request - mark as processed and skip
                    self.mark_processed(msg_ts)
                    continue

                # This is a missed submission!
                logger.info(f"Found missed workflow submission from {submitter_id} at {msg_ts}")
                missed.append({
                    "message": msg,
                    "submitter_id": submitter_id,
                    "channel": admin_dm_channel,
                })

        except SlackApiError as e:
            logger.error(f"Error scanning for missed submissions: {e}")

        return missed


def process_startup_queue(client: WebClient, config: Config) -> int:
    """
    Process any missed workflow submissions on startup.

    This is the main entry point called from bot.py.

    Returns:
        Number of missed submissions found and queued for processing.
    """
    processor = StartupQueueProcessor(client, config)
    missed = processor.scan_for_missed_submissions()

    if not missed:
        logger.info("No missed workflow submissions found")
        return 0

    logger.info(f"Found {len(missed)} missed workflow submissions")

    # For each missed submission, we need to trigger the workflow listener logic
    # But we can't directly call the handler - instead, we'll notify the admin
    # and let them re-trigger or we process inline

    # Load pending reprocess data to store message info for button handler
    pending = _load_pending_reprocess()

    for item in missed:
        submitter_id = item["submitter_id"]
        msg = item["message"]
        channel = item["channel"]
        msg_ts = msg.get("ts", "")

        # Store message data for the reprocess button handler
        reprocess_key = f"{submitter_id}_{msg_ts}"
        pending[reprocess_key] = {
            "submitter_id": submitter_id,
            "message_text": msg.get("text", ""),
            "channel": channel,
            "message_ts": msg_ts,
        }

        try:
            # Notify admin about the missed submission with reprocess button
            client.chat_postMessage(
                channel=channel,
                text=f"Missed workflow submission from <@{submitter_id}>",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f":warning: *Missed Submission Detected*\n\n"
                                    f"A workflow submission from <@{submitter_id}> was received "
                                    f"while the bot was offline.",
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Reprocess Now"},
                                "style": "primary",
                                "action_id": "reprocess_missed_submission",
                                "value": reprocess_key,
                            },
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Dismiss"},
                                "action_id": "dismiss_missed_submission",
                                "value": reprocess_key,
                            },
                        ],
                    },
                ],
                thread_ts=msg_ts,
            )

            # Mark as processed so we don't notify again on next restart
            processor.mark_processed(msg_ts)

        except SlackApiError as e:
            logger.error(f"Error notifying about missed submission: {e}")

    # Save pending reprocess data
    _save_pending_reprocess(pending)

    return len(missed)


def register_startup_queue_handlers(app: App, config: Config):
    """Register handlers for missed submission reprocessing buttons."""

    from .services.github_service import GitHubService
    from .services.bio_service import BioService
    from .models.onboarding_request import OnboardingRequest, OnboardingStatus
    from .storage import save_request
    from .handlers.workflow_listener import _parse_workflow_message

    github_service = GitHubService(config.github.token, config.github.org_name)
    bio_service = None
    if config.anthropic:
        bio_service = BioService(config.anthropic.api_key, config.anthropic.model)

    @app.action("reprocess_missed_submission")
    def handle_reprocess(ack, body, client: WebClient, action):
        """Handle the reprocess button click."""
        ack()

        reprocess_key = action["value"]
        pending = _load_pending_reprocess()

        if reprocess_key not in pending:
            client.chat_postMessage(
                channel=body["channel"]["id"],
                text=":x: Could not find submission data. It may have already been processed.",
                thread_ts=body["message"]["ts"],
            )
            return

        data = pending[reprocess_key]
        submitter_id = data["submitter_id"]
        message_text = data["message_text"]
        channel = data["channel"]

        # Parse the workflow message
        parsed_data = _parse_workflow_message(message_text)

        if not parsed_data:
            client.chat_postMessage(
                channel=channel,
                text=f":x: Could not parse workflow message for <@{submitter_id}>. Manual processing required.",
                thread_ts=body["message"]["ts"],
            )
            return

        # Get user info from Slack
        try:
            user_info = client.users_info(user=submitter_id)
            name = parsed_data.get("name") or user_info["user"]["real_name"] or user_info["user"]["name"]
            email = parsed_data.get("email") or user_info["user"].get("profile", {}).get("email", "")
        except SlackApiError:
            name = parsed_data.get("name", "Unknown")
            email = parsed_data.get("email", "")

        # Validate GitHub username if provided
        github_username = parsed_data.get("github_username", "")
        if github_username:
            is_valid, _ = github_service.validate_username(github_username)
            if not is_valid:
                client.chat_postMessage(
                    channel=channel,
                    text=f":warning: GitHub username `{github_username}` is invalid, but continuing anyway.",
                    thread_ts=body["message"]["ts"],
                )

        # Open DM channel with the new member
        try:
            dm_response = client.conversations_open(users=[submitter_id])
            dm_channel = dm_response["channel"]["id"]
        except SlackApiError:
            dm_channel = channel

        # Create onboarding request
        request = OnboardingRequest(
            slack_user_id=submitter_id,
            slack_channel_id=dm_channel,
            name=name,
            email=email,
            github_username=github_username,
            bio_raw=parsed_data.get("bio", ""),
            website_url=parsed_data.get("website_url", ""),
        )

        # Process bio if service available
        if bio_service and request.bio_raw:
            edited_bio, _ = bio_service.edit_bio(request.bio_raw, name)
            if edited_bio:
                request.bio_edited = edited_bio

        request.update_status(OnboardingStatus.PENDING_APPROVAL)
        save_request(request)

        # Remove from pending
        del pending[reprocess_key]
        _save_pending_reprocess(pending)

        # Send approval request (reuse workflow listener logic)
        from .handlers.workflow_listener import _send_workflow_approval_request
        _send_workflow_approval_request(client, config, request, github_service, channel)

        # Update the original message to show it was processed
        try:
            client.chat_update(
                channel=body["channel"]["id"],
                ts=body["message"]["ts"],
                text=f":white_check_mark: Reprocessed submission from <@{submitter_id}>",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": f":white_check_mark: *Submission Reprocessed*\n\n"
                                    f"Successfully created onboarding request for <@{submitter_id}>.\n"
                                    f"Approval request sent above.",
                        },
                    },
                ],
            )
        except SlackApiError as e:
            logger.error(f"Error updating message: {e}")

    @app.action("dismiss_missed_submission")
    def handle_dismiss(ack, body, client: WebClient, action):
        """Handle the dismiss button click."""
        ack()

        reprocess_key = action["value"]
        pending = _load_pending_reprocess()

        # Remove from pending
        if reprocess_key in pending:
            del pending[reprocess_key]
            _save_pending_reprocess(pending)

        # Update message to show dismissed
        try:
            client.chat_update(
                channel=body["channel"]["id"],
                ts=body["message"]["ts"],
                text="Dismissed missed submission",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": ":no_entry_sign: *Dismissed* - This missed submission has been ignored.",
                        },
                    },
                ],
            )
        except SlackApiError as e:
            logger.error(f"Error updating message: {e}")
