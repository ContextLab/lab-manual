"""
Website approval handlers for Slack.

Handles the admin preview and approval flow for website changes:
- Onboarding: Preview member profile before PR creation
- Offboarding: Preview alumni transition before PR creation
"""

import logging
import re
from datetime import datetime
from typing import Optional

from slack_bolt import App
from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

from ..config import Config
from ..models.onboarding_request import OnboardingRequest, OnboardingStatus
from ..services.website_service import (
    WebsiteService,
    WebsiteContent,
    AlumniContent,
    MemberRole,
    GradType,
    build_cv_entry,
    build_cv_update_for_offboarding,
)
from ..storage import get_request, save_request

logger = logging.getLogger(__name__)

# Storage for pending website operations
_pending_website_ops: dict[str, dict] = {}


def register_website_approval_handlers(app: App, config: Config):
    """Register website approval handlers."""

    website_service = None
    if config.github:
        website_service = WebsiteService(config.github.token)

    # ========== Onboarding Website Handlers ==========

    @app.action("preview_website_changes")
    def handle_preview(ack, body, client: WebClient, action):
        """Show admin preview of website changes before PR creation."""
        ack()

        user_id = action["value"]
        admin_id = body["user"]["id"]

        if admin_id != config.slack.admin_user_id:
            return

        request = get_request(user_id)
        if not request:
            logger.error(f"No request found for user {user_id}")
            return

        _open_website_preview_modal(client, body["trigger_id"], request)

    @app.view(re.compile(r"website_preview_modal_.*"))
    def handle_preview_submission(ack, body, client: WebClient, view):
        """Handle submission of the website preview modal."""
        ack()

        user_id = view["private_metadata"]
        request = get_request(user_id)
        if not request:
            return

        values = view["state"]["values"]

        # Extract edited values
        edited_name = values.get("name_block", {}).get("name_input", {}).get("value", request.name)
        edited_role = values.get("role_block", {}).get("role_select", {}).get("selected_option", {}).get("value", request.role)
        edited_grad_type = values.get("grad_type_block", {}).get("grad_type_select", {}).get("selected_option", {}).get("value", request.grad_type) if "grad_type_block" in values else request.grad_type
        edited_grad_field = values.get("grad_field_block", {}).get("grad_field_input", {}).get("value", request.grad_field) if "grad_field_block" in values else request.grad_field
        edited_bio = values.get("bio_block", {}).get("bio_input", {}).get("value", request.bio_edited or request.bio_raw)
        edited_website = values.get("website_block", {}).get("website_input", {}).get("value", request.website_url)

        # Store edited content
        _pending_website_ops[user_id] = {
            "name": edited_name,
            "role": edited_role,
            "grad_type": edited_grad_type,
            "grad_field": edited_grad_field,
            "bio": edited_bio,
            "website_url": edited_website,
            "request": request,
        }

        # Send confirmation message
        _send_website_confirmation(client, config, user_id, edited_name, edited_role,
                                   edited_grad_type, edited_grad_field, edited_bio, edited_website)

    @app.action("create_website_pr")
    def handle_create_pr(ack, body, client: WebClient, action):
        """Create the website PR after admin confirmation."""
        ack()

        user_id = action["value"]
        admin_id = body["user"]["id"]

        if admin_id != config.slack.admin_user_id:
            return

        pending = _pending_website_ops.get(user_id)
        if not pending or not website_service:
            client.chat_postMessage(
                channel=config.slack.admin_user_id,
                text=":x: Error: No pending website operation found or website service not configured.",
            )
            return

        request = pending["request"]

        # Build website content
        content = WebsiteContent(
            name=pending["name"],
            name_url=pending["website_url"] if pending["website_url"] else None,
            role=pending["role"],
            bio=pending["bio"],
            image_filename=website_service.generate_image_filename(pending["name"]),
        )

        # Read image data if available
        if request.photo_processed_path and request.photo_processed_path.exists():
            with open(request.photo_processed_path, "rb") as f:
                content.image_data = f.read()

        # Build CV entry if applicable
        cv_entry = None
        cv_section = None
        try:
            role_enum = MemberRole(pending["role"])
            grad_type_enum = GradType(pending["grad_type"]) if pending["grad_type"] else None
            year = request.start_year if request.start_year else datetime.now().year

            cv_entry, cv_section = build_cv_entry(
                name=pending["name"],
                role=role_enum,
                grad_type=grad_type_enum,
                grad_field=pending["grad_field"],
                year=year,
            )
        except ValueError:
            logger.warning(f"Could not map role '{pending['role']}' to CV section")

        # Create the PR
        success, result, branch = website_service.create_onboarding_pr(
            content=content,
            cv_entry=cv_entry,
            cv_section=cv_section,
            slack_user_id=user_id,
        )

        if success:
            # Update message
            try:
                client.chat_update(
                    channel=body["channel"]["id"],
                    ts=body["message"]["ts"],
                    text=f":white_check_mark: Website PR created for {pending['name']}",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f":white_check_mark: *Website PR Created*\n\n"
                                        f"<{result}|View Pull Request>\n\n"
                                        f"The PR will add {pending['name']} to the lab website"
                                        + (f" and CV" if cv_entry else "") +
                                        ". Review and merge to publish.",
                            },
                        },
                    ],
                )
            except SlackApiError as e:
                logger.error(f"Error updating message: {e}")

            # Update request
            request.website_pr_url = result
            request.website_branch = branch
            request.update_status(OnboardingStatus.WEBSITE_PR_CREATED)
            save_request(request)

            # Notify member
            try:
                client.chat_postMessage(
                    channel=request.slack_channel_id,
                    text=":globe_with_meridians: Your profile is being added to the lab website! "
                         "It will appear shortly after admin review.",
                )
            except SlackApiError:
                pass
        else:
            client.chat_postMessage(
                channel=config.slack.admin_user_id,
                text=f":x: Error creating website PR: {result}",
            )

        # Cleanup
        _pending_website_ops.pop(user_id, None)

    @app.action("edit_website_content")
    def handle_edit_content(ack, body, client: WebClient, action):
        """Re-open the preview modal for editing."""
        ack()

        user_id = action["value"]
        request = get_request(user_id)
        if request:
            _open_website_preview_modal(client, body["trigger_id"], request)

    @app.action("request_member_changes")
    def handle_request_member_changes(ack, body, client: WebClient, action):
        """Request the member to update their information."""
        ack()

        user_id = action["value"]
        admin_id = body["user"]["id"]

        if admin_id != config.slack.admin_user_id:
            return

        try:
            client.views_open(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "callback_id": f"website_member_changes_modal_{user_id}",
                    "private_metadata": user_id,
                    "title": {"type": "plain_text", "text": "Request Changes"},
                    "submit": {"type": "plain_text", "text": "Send to Member"},
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
                                    "text": "What changes should the member make? (e.g., need different photo, bio too long, etc.)",
                                },
                            },
                            "label": {"type": "plain_text", "text": "Changes Needed"},
                        },
                    ],
                },
            )
        except SlackApiError as e:
            logger.error(f"Error opening changes modal: {e}")

    @app.view(re.compile(r"website_member_changes_modal_.*"))
    def handle_member_changes_submission(ack, body, client: WebClient, view):
        """Send change request to member."""
        ack()

        user_id = view["private_metadata"]
        request = get_request(user_id)
        if not request:
            return

        changes_text = view["state"]["values"]["changes_block"]["changes_input"]["value"]

        try:
            client.chat_postMessage(
                channel=request.slack_channel_id,
                text="The admin has requested changes to your website profile.",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": ":memo: *Changes Requested for Website Profile*",
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
                            "text": "Please reply with the updated information or upload a new photo.",
                        },
                    },
                ],
            )
        except SlackApiError as e:
            logger.error(f"Error sending change request: {e}")

        request.update_status(OnboardingStatus.PENDING_INFO)
        save_request(request)

    # ========== Offboarding Website Handlers ==========

    @app.action("collect_alumni_info")
    def handle_collect_alumni_info(ack, body, client: WebClient, action):
        """Initiate alumni info collection from admin."""
        ack()

        user_id = action["value"]

        try:
            client.views_open(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "callback_id": f"initiate_alumni_collection_{user_id}",
                    "private_metadata": user_id,
                    "title": {"type": "plain_text", "text": "Alumni Info"},
                    "submit": {"type": "plain_text", "text": "Send to Member"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": [
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f"This will send a form to <@{user_id}> to collect their alumni information.",
                            },
                        },
                        {
                            "type": "input",
                            "block_id": "years_block",
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "years_input",
                                "placeholder": {"type": "plain_text", "text": "e.g., 2020-2024"},
                            },
                            "label": {"type": "plain_text", "text": "Years Active"},
                        },
                        {
                            "type": "input",
                            "block_id": "alumni_sheet_block",
                            "element": {
                                "type": "static_select",
                                "action_id": "alumni_sheet_select",
                                "options": [
                                    {"text": {"type": "plain_text", "text": "Graduate Alumni"}, "value": "alumni_grads"},
                                    {"text": {"type": "plain_text", "text": "Undergraduate Alumni"}, "value": "alumni_undergrads"},
                                    {"text": {"type": "plain_text", "text": "Postdoc Alumni"}, "value": "alumni_postdocs"},
                                    {"text": {"type": "plain_text", "text": "Lab Manager Alumni"}, "value": "alumni_managers"},
                                ],
                            },
                            "label": {"type": "plain_text", "text": "Alumni Category"},
                        },
                    ],
                },
            )
        except SlackApiError as e:
            logger.error(f"Error opening alumni collection modal: {e}")

    @app.view(re.compile(r"initiate_alumni_collection_.*"))
    def handle_alumni_collection_initiation(ack, body, client: WebClient, view):
        """Send alumni info request to departing member."""
        ack()

        user_id = view["private_metadata"]
        values = view["state"]["values"]

        years = values.get("years_block", {}).get("years_input", {}).get("value", "")
        alumni_sheet = values.get("alumni_sheet_block", {}).get("alumni_sheet_select", {}).get("selected_option", {}).get("value", "alumni_grads")

        _pending_website_ops[f"offboard_{user_id}"] = {
            "years": years,
            "alumni_sheet": alumni_sheet,
        }

        try:
            dm_response = client.conversations_open(users=[user_id])
            dm_channel = dm_response["channel"]["id"]

            client.chat_postMessage(
                channel=dm_channel,
                text="Please provide your alumni information.",
                blocks=[
                    {
                        "type": "section",
                        "text": {
                            "type": "mrkdwn",
                            "text": ":wave: *Alumni Information Request*\n\n"
                                    "As you transition from the lab, we'd like to update the website with your alumni information.",
                        },
                    },
                    {
                        "type": "actions",
                        "elements": [
                            {
                                "type": "button",
                                "text": {"type": "plain_text", "text": "Provide Alumni Info"},
                                "style": "primary",
                                "action_id": "open_alumni_form",
                                "value": user_id,
                            },
                        ],
                    },
                ],
            )
        except SlackApiError as e:
            logger.error(f"Error sending alumni form request: {e}")

    @app.action("open_alumni_form")
    def handle_open_alumni_form(ack, body, client: WebClient, action):
        """Open the alumni information form."""
        ack()

        user_id = body["user"]["id"]

        try:
            client.views_open(
                trigger_id=body["trigger_id"],
                view={
                    "type": "modal",
                    "callback_id": f"alumni_form_{user_id}",
                    "private_metadata": user_id,
                    "title": {"type": "plain_text", "text": "Alumni Information"},
                    "submit": {"type": "plain_text", "text": "Submit"},
                    "close": {"type": "plain_text", "text": "Cancel"},
                    "blocks": [
                        {
                            "type": "input",
                            "block_id": "position_block",
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "position_input",
                                "placeholder": {"type": "plain_text", "text": "e.g., Postdoc at MIT"},
                            },
                            "label": {"type": "plain_text", "text": "Current Position"},
                        },
                        {
                            "type": "input",
                            "block_id": "position_url_block",
                            "optional": True,
                            "element": {
                                "type": "plain_text_input",
                                "action_id": "position_url_input",
                                "placeholder": {"type": "plain_text", "text": "https://..."},
                            },
                            "label": {"type": "plain_text", "text": "Position URL (optional)"},
                        },
                    ],
                },
            )
        except SlackApiError as e:
            logger.error(f"Error opening alumni form: {e}")

    @app.view(re.compile(r"alumni_form_.*"))
    def handle_alumni_form_submission(ack, body, client: WebClient, view):
        """Handle alumni form submission."""
        ack()

        user_id = view["private_metadata"]
        values = view["state"]["values"]

        position = values.get("position_block", {}).get("position_input", {}).get("value", "")
        position_url = values.get("position_url_block", {}).get("position_url_input", {}).get("value", "")

        pending = _pending_website_ops.get(f"offboard_{user_id}", {})

        try:
            user_info = client.users_info(user=user_id)
            name = user_info["user"]["real_name"] or user_info["user"]["name"]
        except SlackApiError:
            name = "Unknown"

        _pending_website_ops[f"offboard_{user_id}"] = {
            **pending,
            "name": name,
            "current_position": position,
            "current_position_url": position_url,
        }

        _send_alumni_preview(client, config, user_id, name, pending.get("years", ""),
                            position, position_url, pending.get("alumni_sheet", "alumni_grads"))

    @app.action("create_offboarding_pr")
    def handle_create_offboarding_pr(ack, body, client: WebClient, action):
        """Create the offboarding PR."""
        ack()

        user_id = action["value"]
        admin_id = body["user"]["id"]

        if admin_id != config.slack.admin_user_id:
            return

        pending = _pending_website_ops.get(f"offboard_{user_id}")
        if not pending or not website_service:
            client.chat_postMessage(
                channel=config.slack.admin_user_id,
                text=":x: Error: No pending offboarding operation found.",
            )
            return

        alumni_content = AlumniContent(
            name=pending["name"],
            years=pending.get("years", ""),
            current_position=pending.get("current_position", ""),
            current_position_url=pending.get("current_position_url"),
        )

        # Build CV update if we have role info
        cv_update = None
        # Note: CV update would require knowing the member's original role and start year
        # This would need to be stored or looked up

        success, result, branch = website_service.create_offboarding_pr(
            member_name=pending["name"],
            alumni_content=alumni_content,
            alumni_sheet=pending.get("alumni_sheet", "alumni_grads"),
            cv_update=cv_update,
            slack_user_id=user_id,
        )

        if success:
            try:
                client.chat_update(
                    channel=body["channel"]["id"],
                    ts=body["message"]["ts"],
                    text=f":white_check_mark: Offboarding PR created for {pending['name']}",
                    blocks=[
                        {
                            "type": "section",
                            "text": {
                                "type": "mrkdwn",
                                "text": f":white_check_mark: *Website Offboarding PR Created*\n\n"
                                        f"<{result}|View Pull Request>\n\n"
                                        f"This PR moves {pending['name']} to alumni. Review and merge to publish.",
                            },
                        },
                    ],
                )
            except SlackApiError as e:
                logger.error(f"Error updating message: {e}")
        else:
            client.chat_postMessage(
                channel=config.slack.admin_user_id,
                text=f":x: Error creating offboarding PR: {result}",
            )

        _pending_website_ops.pop(f"offboard_{user_id}", None)


def _open_website_preview_modal(client: WebClient, trigger_id: str, request: OnboardingRequest):
    """Open modal showing preview of website content."""

    role_options = [
        {"text": {"type": "plain_text", "text": role.value}, "value": role.value}
        for role in MemberRole
    ]

    # Find initial role option
    initial_role = None
    for opt in role_options:
        if opt["value"] == request.role:
            initial_role = opt
            break

    blocks = [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": ":globe_with_meridians: *Preview Website Profile*\n\n"
                        "Review and edit the content below before creating the PR.",
            },
        },
        {
            "type": "input",
            "block_id": "name_block",
            "element": {
                "type": "plain_text_input",
                "action_id": "name_input",
                "initial_value": request.name,
            },
            "label": {"type": "plain_text", "text": "Name (as displayed on website)"},
        },
        {
            "type": "input",
            "block_id": "role_block",
            "element": {
                "type": "static_select",
                "action_id": "role_select",
                "options": role_options,
                **({"initial_option": initial_role} if initial_role else {}),
            },
            "label": {"type": "plain_text", "text": "Role"},
        },
    ]

    # Add grad type if role is Graduate Student
    if request.role == MemberRole.GRAD_STUDENT.value:
        grad_type_options = [
            {"text": {"type": "plain_text", "text": "Doctoral"}, "value": "Doctoral"},
            {"text": {"type": "plain_text", "text": "Masters"}, "value": "Masters"},
        ]
        initial_grad = None
        for opt in grad_type_options:
            if opt["value"] == request.grad_type:
                initial_grad = opt
                break

        blocks.append({
            "type": "input",
            "block_id": "grad_type_block",
            "element": {
                "type": "static_select",
                "action_id": "grad_type_select",
                "options": grad_type_options,
                **({"initial_option": initial_grad} if initial_grad else {}),
            },
            "label": {"type": "plain_text", "text": "Graduate Type"},
        })

        if request.grad_type == "Masters":
            blocks.append({
                "type": "input",
                "block_id": "grad_field_block",
                "optional": True,
                "element": {
                    "type": "plain_text_input",
                    "action_id": "grad_field_input",
                    "initial_value": request.grad_field or "",
                    "placeholder": {"type": "plain_text", "text": "e.g., Quantitative Biomedical Sciences"},
                },
                "label": {"type": "plain_text", "text": "Field of Study"},
            })

    blocks.extend([
        {
            "type": "input",
            "block_id": "bio_block",
            "element": {
                "type": "plain_text_input",
                "action_id": "bio_input",
                "multiline": True,
                "initial_value": request.bio_edited or request.bio_raw,
            },
            "label": {"type": "plain_text", "text": "Bio"},
        },
        {
            "type": "input",
            "block_id": "website_block",
            "optional": True,
            "element": {
                "type": "plain_text_input",
                "action_id": "website_input",
                "initial_value": request.website_url or "",
            },
            "label": {"type": "plain_text", "text": "Personal Website URL"},
        },
    ])

    # Photo status
    if request.photo_processed_path and request.photo_processed_path.exists():
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": f":camera: Photo ready: `{request.photo_processed_path.name}`"}],
        })
    else:
        blocks.append({
            "type": "context",
            "elements": [{"type": "mrkdwn", "text": ":warning: No photo processed yet"}],
        })

    try:
        client.views_open(
            trigger_id=trigger_id,
            view={
                "type": "modal",
                "callback_id": f"website_preview_modal_{request.slack_user_id}",
                "private_metadata": request.slack_user_id,
                "title": {"type": "plain_text", "text": "Website Preview"},
                "submit": {"type": "plain_text", "text": "Continue"},
                "close": {"type": "plain_text", "text": "Cancel"},
                "blocks": blocks,
            },
        )
    except SlackApiError as e:
        logger.error(f"Error opening preview modal: {e}")


def _send_website_confirmation(
    client: WebClient,
    config: Config,
    user_id: str,
    name: str,
    role: str,
    grad_type: str,
    grad_field: str,
    bio: str,
    website: str,
):
    """Send confirmation message with Create PR button."""

    role_display = role
    if grad_type:
        role_display += f" ({grad_type})"
        if grad_field:
            role_display += f" - {grad_field}"

    try:
        client.chat_postMessage(
            channel=config.slack.admin_user_id,
            text=f"Ready to create website PR for {name}",
            blocks=[
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": ":globe_with_meridians: Website PR Ready"},
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{name}* ({role_display})\n\n"
                                f"*Bio:*\n>{bio}\n\n"
                                f"*Website:* {website or 'None'}",
                    },
                },
                {"type": "divider"},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Create PR"},
                            "style": "primary",
                            "action_id": "create_website_pr",
                            "value": user_id,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Edit Content"},
                            "action_id": "edit_website_content",
                            "value": user_id,
                        },
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Request Member Changes"},
                            "action_id": "request_member_changes",
                            "value": user_id,
                        },
                    ],
                },
            ],
        )
    except SlackApiError as e:
        logger.error(f"Error sending confirmation: {e}")


def _send_alumni_preview(
    client: WebClient,
    config: Config,
    user_id: str,
    name: str,
    years: str,
    position: str,
    position_url: str,
    alumni_sheet: str,
):
    """Send alumni preview to admin for confirmation."""
    try:
        client.chat_postMessage(
            channel=config.slack.admin_user_id,
            text=f"Alumni transition ready for {name}",
            blocks=[
                {
                    "type": "header",
                    "text": {"type": "plain_text", "text": ":wave: Alumni Transition Ready"},
                },
                {
                    "type": "section",
                    "text": {
                        "type": "mrkdwn",
                        "text": f"*{name}*\n\n"
                                f"*Years:* {years}\n"
                                f"*Current Position:* {position}\n"
                                f"*Position URL:* {position_url or 'None'}\n"
                                f"*Alumni Sheet:* {alumni_sheet}",
                    },
                },
                {"type": "divider"},
                {
                    "type": "actions",
                    "elements": [
                        {
                            "type": "button",
                            "text": {"type": "plain_text", "text": "Create Offboarding PR"},
                            "style": "primary",
                            "action_id": "create_offboarding_pr",
                            "value": user_id,
                        },
                    ],
                },
            ],
        )
    except SlackApiError as e:
        logger.error(f"Error sending alumni preview: {e}")
