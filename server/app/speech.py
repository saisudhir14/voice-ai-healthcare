"""Turn clinic clock times into spoken words and keep machine text (JSON, parentheses) out of TTS."""

from __future__ import annotations

import re

_UNDER_TWENTY = (
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
)
_TENS = ("", "", "twenty", "thirty", "forty", "fifty")

# 10:00 AM, 10:00am, 6:00 a.m., 14:30
_CLOCK = re.compile(
    r"\b(\d{1,2}):(\d{2})(?:\s*(a\.?\s*m\.?|p\.?\s*m\.?))?\b",
    re.IGNORECASE,
)
_PAREN = re.compile(r"\([^)]*\)")
_OPEN_PAREN = re.compile(r"\([^)]*$")
_DASH = re.compile(r"[—–…]")
_HUM = re.compile(r"\b(mm+|hmm+|uh+|um+)\b[,.!]?", re.IGNORECASE)
_SPACE = re.compile(r"\s+")
# Local models sometimes print a tool call as JSON. That must never be spoken.
_JSON_KEY = re.compile(
    r"""["'](?:name|parameters|prompt|arguments|tool_calls|function)["']\s*:""",
    re.IGNORECASE,
)
_JSON_TAIL = re.compile(r"""["']\s*\}+\s*$""")
_FAKE_TOOL = re.compile(r"prompt_user_for_response", re.IGNORECASE)
_STAGE = re.compile(
    r"\b(not emergent|keep booking|stop booking|continue makeup booking|continue booking)\b[.,!]?",
    re.IGNORECASE,
)


def _drop_machine_text(text: str) -> str:
    """Keep words a person would say. Drop tool JSON and leftover braces."""
    if _FAKE_TOOL.search(text) or _JSON_KEY.search(text):
        brace = text.find("{")
        return text[:brace].strip() if brace > 0 else ""
    brace = text.find("{")
    if brace != -1:
        text = text[:brace]
    if _JSON_TAIL.search(text):
        return ""
    text = _JSON_TAIL.sub("", text)
    text = text.replace("{", " ").replace("}", " ")
    return text


def _words(n: int) -> str:
    if n < 20:
        return _UNDER_TWENTY[n]
    tens, ones = divmod(n, 10)
    if ones == 0:
        return _TENS[tens]
    return f"{_TENS[tens]} {_UNDER_TWENTY[ones]}"


def _clock(match: re.Match[str]) -> str:
    hour = int(match.group(1))
    minute = int(match.group(2))
    meridian = match.group(3)
    if hour > 23 or minute > 59:
        return match.group(0)
    spoken_hour = _words(hour % 12 or 12)
    if minute == 0:
        spoken = spoken_hour
    elif minute < 10:
        spoken = f"{spoken_hour} oh {_words(minute)}"
    else:
        spoken = f"{spoken_hour} {_words(minute)}"
    if meridian:
        spoken += " AM" if meridian.lower().startswith("a") else " PM"
    return spoken


_RANGE_DASH = re.compile(r"(?<=\d)\s*[—–]\s*(?=\d)")
_TEXT_DASH = re.compile(r"\s*(?:—|–|--)\s*")
_DOUBLE_PUNCT = re.compile(r",\s*([,.!?;:])")


def _no_dashes(text: str) -> str:
    """Patients never get em/en dashes: "9–10" -> "9-10", "rough — sorry" -> "rough, sorry"."""
    text = _RANGE_DASH.sub("-", text)
    text = _TEXT_DASH.sub(", ", text)
    text = _DOUBLE_PUNCT.sub(r"\1", text)
    return re.sub(r"^\s*,\s*|\s*,\s*$", "", text)


def clean_for_text(text: str) -> str:
    """SMS keeps digits and times as written; drop tool JSON, markdown emphasis and dashes."""
    cleaned = _no_dashes(_drop_machine_text(text).replace("*", ""))
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    return re.sub(r"\n{3,}", "\n\n", cleaned).strip()


def normalize_for_speech(text: str) -> str:
    """Say 10:00 AM as 'ten AM', and drop JSON, parentheses, and stage directions."""
    cleaned = _drop_machine_text(text)
    if not cleaned.strip():
        return ""
    cleaned = _PAREN.sub(" ", cleaned)
    cleaned = _OPEN_PAREN.sub(" ", cleaned)
    cleaned = _DASH.sub(", ", cleaned)
    cleaned = _HUM.sub(" ", cleaned)
    cleaned = _STAGE.sub(" ", cleaned)
    cleaned = _CLOCK.sub(_clock, cleaned)
    cleaned = cleaned.replace("*", "")
    cleaned = _SPACE.sub(" ", cleaned).strip()
    cleaned = re.sub(r"\s+([,.!?])", r"\1", cleaned)
    if len(cleaned) < 2:
        return ""
    return cleaned
