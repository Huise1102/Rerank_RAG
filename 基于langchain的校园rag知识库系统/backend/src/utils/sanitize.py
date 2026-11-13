import re

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
TAG_PATTERN = re.compile(r"</?[a-zA-Z][^>]*>")


def sanitize_text(value: str | None, max_length: int) -> str:
    if not value:
        return ""
    text = TAG_PATTERN.sub("", value)
    text = CONTROL_CHARS.sub("", text)
    text = text.replace("<", "&lt;").replace(">", "&gt;")
    return text.strip()[:max_length]
