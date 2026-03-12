"""
Bio editing service using Claude API.

Edits member bios to follow CDL style guidelines:
- Third person voice
- Uses first names only
- 3-4 sentences maximum
- Clear, engaging, fun style
- No inappropriate or private information
"""

import logging
import re
from typing import Optional

import anthropic

logger = logging.getLogger(__name__)


class BioService:
    """Service for editing member bios using Claude API."""

    # Style guidelines for bio editing
    STYLE_GUIDELINES = """
Style guidelines for CDL lab member bios:
1. Use third person voice (e.g., "Jane studies..." not "I study...")
2. Use first names only after the first mention
3. Keep it to 3-4 sentences maximum
4. Write in a clear, engaging, and fun style
5. Focus on research interests and personality
6. Remove any private information (addresses, phone numbers, personal emails)
7. Remove any inappropriate content
8. Match the tone of existing CDL bios - professional but personable
"""

    # Example bios for few-shot learning
    EXAMPLE_BIOS = """
Example edited bios from the CDL website:

Example 1:
"Jeremy is an Associate Professor of Psychological and Brain Sciences at Dartmouth and directs the Contextual Dynamics Lab. He enjoys thinking about brains, computers, and cats."

Example 2:
"Paxton graduated from Dartmouth in 2019 with a BA in neuroscience and is continuing his research in the lab. He's interested in how we represent and understand narratives and how those processes relate to memory."

Example 3:
"Lucy joined the lab as a research assistant after graduating from Dartmouth. She's excited to explore computational approaches to understanding memory and cognition."
"""

    def __init__(self, api_key: str, model: str = "claude-sonnet-4-20250514"):
        """
        Initialize the bio service.

        Args:
            api_key: Anthropic API key
            model: Claude model to use
        """
        self.client = anthropic.Anthropic(api_key=api_key)
        self.model = model

    def edit_bio(self, raw_bio: str, name: str) -> tuple[str, Optional[str]]:
        """
        Edit a bio to match CDL style guidelines.

        Args:
            raw_bio: The original bio text from the user
            name: The member's full name

        Returns:
            Tuple of (edited_bio, error_message)
        """
        if not raw_bio.strip():
            return "", "No bio text provided"

        # Extract first name for the prompt
        first_name = name.split()[0] if name else "the member"

        prompt = f"""Please edit the following bio to match our lab's style guidelines.

{self.STYLE_GUIDELINES}

{self.EXAMPLE_BIOS}

Member's name: {name}
First name to use: {first_name}

Original bio:
{raw_bio}

Please provide ONLY the edited bio text, with no additional commentary, explanations, or quotation marks. The bio should be ready to publish as-is."""

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )

            edited_bio = message.content[0].text.strip()

            # Clean up any stray quotation marks
            edited_bio = edited_bio.strip('"\'')

            # Validate the output
            is_valid, validation_error = self._validate_bio(edited_bio, first_name)
            if not is_valid:
                logger.warning(f"Bio validation warning: {validation_error}")

            logger.info(f"Edited bio for {name}: {len(raw_bio)} -> {len(edited_bio)} chars")
            return edited_bio, None

        except anthropic.APIError as e:
            error_msg = f"Claude API error: {e}"
            logger.error(error_msg)
            return "", error_msg
        except Exception as e:
            error_msg = f"Error editing bio: {e}"
            logger.error(error_msg)
            return "", error_msg

    def _validate_bio(self, bio: str, first_name: str) -> tuple[bool, Optional[str]]:
        """
        Validate that an edited bio meets our guidelines.

        Args:
            bio: The edited bio text
            first_name: The member's first name

        Returns:
            Tuple of (is_valid, warning_message)
        """
        warnings = []

        # Check length (rough sentence count)
        sentences = [s.strip() for s in re.split(r'[.!?]+', bio) if s.strip()]
        if len(sentences) > 5:
            warnings.append(f"Bio has {len(sentences)} sentences (recommended: 3-4)")

        # Check for first-person pronouns
        first_person_pattern = r'\b(I|me|my|myself|we|us|our|ourselves)\b'
        if re.search(first_person_pattern, bio, re.IGNORECASE):
            warnings.append("Bio contains first-person pronouns")

        # Check that the first name is used
        if first_name.lower() not in bio.lower():
            warnings.append(f"Bio doesn't mention '{first_name}'")

        # Check for potential private info patterns
        phone_pattern = r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b'
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'

        if re.search(phone_pattern, bio):
            warnings.append("Bio may contain a phone number")
        if re.search(email_pattern, bio):
            warnings.append("Bio may contain an email address")

        if warnings:
            return False, "; ".join(warnings)
        return True, None

    def suggest_improvements(self, bio: str, name: str) -> tuple[str, Optional[str]]:
        """
        Get suggestions for improving a bio without fully rewriting it.

        Args:
            bio: The current bio text
            name: The member's full name

        Returns:
            Tuple of (suggestions, error_message)
        """
        prompt = f"""Review this lab member bio and suggest specific improvements.

{self.STYLE_GUIDELINES}

Member's name: {name}

Current bio:
{bio}

Please provide a brief list of specific suggestions for improvement. Focus on:
1. Tone and voice
2. Length appropriateness
3. Content that should be added or removed
4. Any style issues

Keep your response concise and actionable."""

        try:
            message = self.client.messages.create(
                model=self.model,
                max_tokens=500,
                messages=[{"role": "user", "content": prompt}],
            )

            suggestions = message.content[0].text.strip()
            return suggestions, None

        except Exception as e:
            error_msg = f"Error getting suggestions: {e}"
            logger.error(error_msg)
            return "", error_msg

    def check_for_private_info(self, text: str) -> list[str]:
        """
        Check text for potential private or inappropriate information.

        Args:
            text: Text to check

        Returns:
            List of warnings about potential private info
        """
        warnings = []

        # Phone numbers
        phone_pattern = r'\b\d{3}[-.\s]?\d{3}[-.\s]?\d{4}\b'
        if re.search(phone_pattern, text):
            warnings.append("Possible phone number detected")

        # Email addresses
        email_pattern = r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'
        if re.search(email_pattern, text):
            warnings.append("Possible email address detected")

        # Street addresses (basic pattern)
        address_pattern = r'\b\d+\s+[A-Za-z]+\s+(Street|St|Avenue|Ave|Road|Rd|Drive|Dr|Lane|Ln|Court|Ct|Boulevard|Blvd)\b'
        if re.search(address_pattern, text, re.IGNORECASE):
            warnings.append("Possible street address detected")

        # Social security numbers
        ssn_pattern = r'\b\d{3}[-.\s]?\d{2}[-.\s]?\d{4}\b'
        if re.search(ssn_pattern, text):
            warnings.append("Possible SSN detected")

        return warnings
