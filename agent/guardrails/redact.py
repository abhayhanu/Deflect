"""PII scrubbing. Deterministic, no model call.

Street level address parts are redacted but the locality, city and pin code stay visible,
because the address policy has a same city rule the agent must be able to check.
"""

import re


class RedactionError(RuntimeError):
    pass


# Order matters. Emails go before UPI handles, and cards before phones.
PATTERNS = [
    ("EMAIL", re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9\-]+(?:\.[A-Za-z0-9\-]+)*\.[A-Za-z]{2,}")),
    ("UPI", re.compile(r"\b[A-Za-z0-9._\-]{2,}@[A-Za-z]{2,}\b")),
    ("CARD", re.compile(r"(?<![\w])\d(?:[ \-]?\d){12,18}(?![\w])")),
    ("PHONE", re.compile(r"(?<![\w+])(?:\+?91[ \-]?|0)?[6-9]\d{2}(?:[ \-]?\d){7}(?![\w])")),
    ("ADDRESS", re.compile(
        r"\b(?:flat|house|h\.?\s?no\.?|plot|door\s?no\.?|room)\s*(?:no\.?\s*)?[A-Za-z]?\s?\d{1,5}[A-Za-z]?\b",
        re.IGNORECASE,
    )),
    ("ADDRESS", re.compile(
        r"\b\d{1,4}[A-Za-z]?,?\s+(?:[A-Z][A-Za-z]*\s+){1,3}"
        r"(?:Street|St|Road|Rd|Lane|Marg|Cross|Main|Avenue)\b"
    )),
]


def redact(text: str) -> tuple[str, dict[str, str]]:
    """Returns the scrubbed text and a map from each placeholder to the original value."""
    if not isinstance(text, str):
        raise RedactionError(f"expected a string, got {type(text).__name__}")

    mapping: dict[str, str] = {}
    seen: dict[tuple[str, str], str] = {}
    counts: dict[str, int] = {}

    def replace(kind: str, match: re.Match) -> str:
        value = match.group(0)
        key = (kind, value.lower())
        if key not in seen:
            counts[kind] = counts.get(kind, 0) + 1
            placeholder = f"<{kind}_{counts[kind]}>"
            seen[key] = placeholder
            mapping[placeholder] = value
        return seen[key]

    for kind, pattern in PATTERNS:
        text = pattern.sub(lambda m, kind=kind: replace(kind, m), text)

    # A second pass must find nothing. If it does, stop the run rather than leak.
    leftovers = [kind for kind, pattern in PATTERNS if pattern.search(text)]
    if leftovers:
        raise RedactionError(f"PII still present after redaction: {sorted(set(leftovers))}")
    return text, mapping


PLACEHOLDER = re.compile(r"<(?:%s)_\d+>" % "|".join(sorted({kind for kind, _ in PATTERNS})))


def rehydrate(text: str, mapping: dict[str, str]) -> str:
    for placeholder, value in mapping.items():
        text = text.replace(placeholder, value)
    return text


def unknown_placeholders(text: str) -> list[str]:
    return PLACEHOLDER.findall(text)


def drop_unknown_placeholders(text: str) -> str:
    """A model sometimes invents a placeholder, like greeting the customer as <EMAIL_1> when the
    ticket had no email. Nothing can fill it, so it is removed rather than sent to a customer."""
    text = PLACEHOLDER.sub("", text)
    text = re.sub(r"[ \t]+([,.!?])", r"\1", text)
    text = re.sub(r",([.!?])", r"\1", text)
    return re.sub(r"[ \t]{2,}", " ", text)
