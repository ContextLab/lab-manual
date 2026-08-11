"""Client for Dartmouth's LLM service at chat.dartmouth.edu.

chat.dartmouth.edu runs Open WebUI. Bios are rewritten through it rather than
a paid third-party API, so onboarding costs the lab nothing and works for any
member with a Dartmouth account -- including Windows and Linux members, who
could not run the previous local mlx-lm path at all.

Endpoint choice is not arbitrary. Open WebUI publishes two surfaces, and on
this deployment only one of them is open to us:

    GET /api/models        -> 200
    GET /openai/v1/models  -> 403

So this uses Open WebUI's native /api/chat/completions rather than the
OpenAI-compatible /openai/v1 path, and takes no dependency on the `openai`
package. The request and response bodies are OpenAI-shaped regardless.

Get a key from chat.dartmouth.edu -> Settings -> Account -> API Keys, then put
it in the environment or a .env file as DARTMOUTH_CHAT_API_KEY.
"""
import json
import os
import re
from pathlib import Path
from typing import List, Optional

import requests

BASE_URL = os.environ.get("DARTMOUTH_CHAT_BASE_URL", "https://chat.dartmouth.edu")

# Confirmed present on the deployment. `list_models()` shows the other 42.
DEFAULT_MODEL = "qwen.qwen3.5-122b"

DEFAULT_TIMEOUT = 180

API_KEY_VAR = "DARTMOUTH_CHAT_API_KEY"


class DartmouthChatError(RuntimeError):
    """The service could not be reached, or refused the request."""


def _load_dotenv_value(name: str) -> Optional[str]:
    """Read `name` from a .env file next to the repo, without a dependency.

    python-dotenv is not part of this repo's build requirements, and pulling
    one in for a handful of KEY=value lines is not worth it. Searched in order:
    the repo root, then the user's ~/.cdl/.env.
    """
    candidates = [
        # cdl_bot/.env, then the repo root -- the same two places
        # cdl_bot/config.py already looks in.
        Path(__file__).resolve().parent.parent / ".env",
        Path(__file__).resolve().parent.parent.parent / ".env",
        Path.home() / ".cdl" / ".env",
    ]
    for path in candidates:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() != name:
                continue
            value = value.strip()
            # Tolerate KEY="value" and KEY='value'.
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            if value:
                return value
    return None


def get_api_key() -> Optional[str]:
    """The API key from the environment, falling back to a .env file."""
    return os.environ.get(API_KEY_VAR) or _load_dotenv_value(API_KEY_VAR)


def is_configured() -> bool:
    return bool(get_api_key())


def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def list_models(api_key: Optional[str] = None, timeout: int = 60) -> List[str]:
    """Every model id this account can reach, sorted."""
    api_key = api_key or get_api_key()
    if not api_key:
        raise DartmouthChatError(
            f"{API_KEY_VAR} is not set. Create a key at "
            f"{BASE_URL} -> Settings -> Account -> API Keys."
        )

    response = requests.get(
        f"{BASE_URL}/api/models", headers=_headers(api_key), timeout=timeout
    )
    if response.status_code != 200:
        raise DartmouthChatError(
            f"GET /api/models returned {response.status_code}: {response.text[:200]}"
        )
    return sorted(m.get("id", "") for m in response.json().get("data", []))


def strip_reasoning(text: str) -> str:
    """Remove an inline chain of thought, if a model emits one.

    qwen3.5-122b keeps its scratchpad out of `content` entirely -- see the
    note on `reasoning` in chat() -- but other models on this deployment wrap
    it in <think>...</think> inline. Left in, that monologue would be written
    into people.xlsx as somebody's bio.
    """
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL | re.IGNORECASE)
    # An unterminated block means the model was cut off mid-thought; keep
    # whatever preceded the opening tag rather than returning the monologue.
    if "<think>" in text.lower():
        text = re.split(r"<think>", text, flags=re.IGNORECASE)[0]
    return text.strip()


def chat(
    prompt: str,
    model: str = DEFAULT_MODEL,
    system: Optional[str] = None,
    max_tokens: int = 300,
    temperature: float = 0.7,
    timeout: int = DEFAULT_TIMEOUT,
    api_key: Optional[str] = None,
    reasoning: bool = False,
) -> str:
    """Send one prompt and return the assistant's reply as text.

    `reasoning=False` matters more than it looks. qwen3.5-122b is a reasoning
    model: it writes its chain of thought into a separate `reasoning_content`
    field and leaves `content` null until it is finished thinking. Asked for a
    one-sentence bio it will happily spend the entire token budget deliberating
    -- measured here at 2000 tokens and 6883 characters of reasoning, still
    returning `finish_reason: "length"` and a null `content`. Turning thinking
    off answers the same prompt in about 20 tokens and 2 seconds.

    Two switches were tested against the live service. `reasoning_effort:
    "none"` and `chat_template_kwargs: {"enable_thinking": false}` each work;
    `reasoning_effort: "low"` and `think: false` are ignored and still burn the
    budget. Both working switches are sent, since the first is the OpenAI
    spelling and the second is Qwen's, and whichever model this points at
    later will recognise one of them.

    Raises DartmouthChatError rather than returning a sentinel, so a caller
    that wants to fall back has to say so explicitly.
    """
    api_key = api_key or get_api_key()
    if not api_key:
        raise DartmouthChatError(
            f"{API_KEY_VAR} is not set. Create a key at "
            f"{BASE_URL} -> Settings -> Account -> API Keys, then export it or "
            f"add it to a .env file in the repo root."
        )

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
        "stream": False,
    }
    if not reasoning:
        payload["reasoning_effort"] = "none"
        payload["chat_template_kwargs"] = {"enable_thinking": False}

    try:
        response = requests.post(
            f"{BASE_URL}/api/chat/completions",
            headers=_headers(api_key),
            data=json.dumps(payload),
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise DartmouthChatError(f"Could not reach {BASE_URL}: {exc}") from exc

    if response.status_code != 200:
        raise DartmouthChatError(
            f"chat/completions returned {response.status_code}: "
            f"{response.text[:300]}"
        )

    try:
        body = response.json()
        choice = body["choices"][0]
        content = choice["message"].get("content")
    except (ValueError, KeyError, IndexError) as exc:
        raise DartmouthChatError(
            f"Unexpected response shape: {response.text[:300]}"
        ) from exc

    finish_reason = choice.get("finish_reason")
    text = strip_reasoning(content or "")

    if not text:
        # `content` comes back null when a reasoning model runs out of budget
        # mid-thought. Returning "" here would quietly write an empty bio into
        # people.xlsx, so this is an error rather than a silent blank.
        reasoning_chars = len(choice["message"].get("reasoning_content") or "")
        detail = f"finish_reason={finish_reason!r}"
        if reasoning_chars:
            detail += (
                f", {reasoning_chars} characters of reasoning and no answer"
                " -- raise max_tokens or keep reasoning off"
            )
        raise DartmouthChatError(f"{model} returned no content ({detail})")

    return text


def main():
    """`python dartmouth_chat.py [prompt]` -- a quick manual check."""
    import sys

    if not is_configured():
        print(f"{API_KEY_VAR} is not set.")
        raise SystemExit(1)

    if len(sys.argv) > 1 and sys.argv[1] == "--models":
        for model in list_models():
            print(model)
        return

    prompt = " ".join(sys.argv[1:]) or "Say hello in exactly four words."
    print(chat(prompt, max_tokens=100))


if __name__ == "__main__":
    main()
