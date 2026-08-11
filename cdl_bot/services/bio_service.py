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

from .dartmouth_chat import DartmouthChatError, DEFAULT_MODEL
from .dartmouth_chat import chat as dartmouth_chat

logger = logging.getLogger(__name__)


PRONOUN_GROUPS = {
    "he": {"he", "him", "his"},
    "she": {"she", "her", "hers"},
}


def stated_pronouns(text: str) -> frozenset:
    """Which gendered pronoun groups a bio uses, if any.

    Compares groups rather than exact words: rewriting "His research
    interests lie in X" as "He is passionate about X" keeps the person's
    pronouns and is a fine edit, while turning it into "Their research
    interests" or "with research interests in X" does not.
    """
    words = set(re.findall(r"[a-z]+", (text or "").lower()))
    return frozenset(
        group for group, forms in PRONOUN_GROUPS.items() if words & forms
    )


class BioService:
    """Service for editing member bios using Claude API."""

    # How many times to re-ask when an edit changes the bio's pronouns.
    PRONOUN_ATTEMPTS = 3

    # Style guidelines for bio editing
    STYLE_GUIDELINES = """
Style guidelines for CDL lab member bios:
1. Use third person voice (e.g., "Jane studies..." not "I study...")
2. PRONOUNS: if the original bio already uses gendered pronouns (he/him/his,
   she/her/hers), keep exactly those -- the person wrote them about
   themselves, so never swap them for "they/them" and never reword them away.
   ONLY when the original contains no pronouns at all may you use "they/them"
   or reword to avoid pronouns. Never infer pronouns from the name.
3. Use first names only after the first mention
4. Keep it to 3-4 sentences maximum
5. Write in a clear, engaging, and fun style
6. Focus on research interests and personality
7. Remove any private information (addresses, phone numbers, personal emails)
8. Remove any inappropriate content
9. Match the tone of existing CDL bios - professional but personable
"""

    # Example bios for few-shot learning.
    #
    # Every one of these happens to use a gendered pronoun, which on its own
    # reads as an instruction to pick one. The note keeps them as tone
    # examples without letting them override rule 2 above.
    EXAMPLE_BIOS = """
Example edited bios from the CDL website. Note that these people's pronouns
come from the bios they submitted -- match the TONE of these examples, not
their pronoun choices:

Example 1:
"Jeremy is an Associate Professor of Psychological and Brain Sciences at Dartmouth and directs the Contextual Dynamics Lab. He enjoys thinking about brains, computers, and cats."

Example 2:
"Paxton graduated from Dartmouth in 2019 with a BA in neuroscience and is continuing his research in the lab. He's interested in how we represent and understand narratives and how those processes relate to memory."

Example 3:
"Lucy joined the lab as a research assistant after graduating from Dartmouth. She's excited to explore computational approaches to understanding memory and cognition."
"""

    def __init__(self, api_key: str = None, model: str = DEFAULT_MODEL):
        """
        Initialize the bio service.

        Bios go through Dartmouth's own chat.dartmouth.edu rather than a paid
        third-party API: it costs the lab nothing, and it does not stop working
        when a personal account runs out of credit -- which is exactly what had
        happened, with every bio call returning "Your credit balance is too low
        to access the Anthropic API."

        Args:
            api_key: Dartmouth Chat API key. Falls back to
                DARTMOUTH_CHAT_API_KEY in the environment or cdl_bot/.env.
            model: model id; see dartmouth_chat.list_models().
        """
        self.api_key = api_key
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

        base_prompt = f"""Please edit the following bio to match our lab's style guidelines.

{self.STYLE_GUIDELINES}

{self.EXAMPLE_BIOS}

Member's name: {name}
First name to use: {first_name}

Original bio:
{raw_bio}

Please provide ONLY the edited bio text, with no additional commentary, explanations, or quotation marks. The bio should be ready to publish as-is."""
        prompt = base_prompt

        # Checked in code, not trusted to the prompt. Even with the pronoun
        # rule spelled out in the style guidelines, the model would rewrite
        # "His research interests lie in causal inference" as "...with
        # research interests in causal inference" -- pronoun gone, rule
        # technically unbroken -- and invent "she" for a member named Jamie.
        # These bios go straight onto a public page about real people.
        wanted = stated_pronouns(raw_bio)

        try:
            for attempt in range(self.PRONOUN_ATTEMPTS):
                edited_bio = dartmouth_chat(
                    prompt,
                    model=self.model,
                    max_tokens=500,
                    api_key=self.api_key,
                ).strip()

                # Clean up any stray quotation marks
                edited_bio = edited_bio.strip('"\'')

                got = stated_pronouns(edited_bio)
                if got == wanted:
                    break

                if wanted:
                    insist = (
                        "Your previous attempt REMOVED the pronouns the "
                        f"person used about themselves "
                        f"({', '.join(sorted(wanted))}). Keep them. Do not "
                        "rephrase to avoid them."
                    )
                else:
                    insist = (
                        "Your previous attempt ADDED gendered pronouns "
                        f"({', '.join(sorted(got))}) that the original did "
                        "not use. Do not guess someone's pronouns. Use "
                        '"they/them" or reword to avoid pronouns.'
                    )
                logger.warning(
                    "Bio edit for %s changed the bio's pronouns (%s -> %s); "
                    "retrying (%d/%d)",
                    name,
                    sorted(wanted) or "none",
                    sorted(got) or "none",
                    attempt + 1,
                    self.PRONOUN_ATTEMPTS,
                )
                prompt = f"{base_prompt}\n\n{insist}"
            else:
                logger.error(
                    "Bio edit for %s kept changing the bio's pronouns; "
                    "keeping the submitted text.",
                    name,
                )
                return raw_bio.strip(), None

            # Validate the output
            is_valid, validation_error = self._validate_bio(edited_bio, first_name)
            if not is_valid:
                logger.warning(f"Bio validation warning: {validation_error}")

            logger.info(f"Edited bio for {name}: {len(raw_bio)} -> {len(edited_bio)} chars")
            return edited_bio, None

        except DartmouthChatError as e:
            error_msg = f"Dartmouth Chat API error: {e}"
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
            suggestions = dartmouth_chat(
                prompt,
                model=self.model,
                max_tokens=500,
                api_key=self.api_key,
            ).strip()
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
