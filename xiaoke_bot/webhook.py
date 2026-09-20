from __future__ import annotations

import json
import re
from typing import Any

_PLACEHOLDER = re.compile(r"\{([A-Za-z0-9_.]+)\}")
_AUTO_FIELDS = ("text", "content", "message", "msg", "body", "title", "desc", "description")


def _lookup(data: Any, dotted: str) -> Any:
    current = data
    for part in dotted.split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _render_template(template: str, data: Any, raw: str) -> str:
    def replace(match: "re.Match[str]") -> str:
        key = match.group(1)
        if key in ("_body", "_raw"):
            return raw
        if key == "_json":
            try:
                return json.dumps(data, ensure_ascii=False)
            except (TypeError, ValueError):
                return raw
        value = _lookup(data, key)
        if value is None:
            return ""
        if isinstance(value, (dict, list)):
            return json.dumps(value, ensure_ascii=False)
        return str(value)

    return _PLACEHOLDER.sub(replace, template)


def _auto_extract(data: dict[str, Any]) -> str:
    for key in _AUTO_FIELDS:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return str(value)
    return ""


def build_webhook_message(
    raw: bytes | str,
    content_type: str,
    template: str,
    prefix: str,
    max_len: int,
) -> str:
    """Turn a received webhook body into a QQ message. Pure and testable.

    - If a template is set, substitute {field} / {a.b} / {_body} / {_json}.
    - Else if JSON, use the first common text field, falling back to compact JSON.
    - Else use the raw text. An optional prefix label is prepended.
    """
    text = raw.decode("utf-8", errors="replace") if isinstance(raw, bytes) else str(raw)
    text = text.strip()
    data: Any = None
    if "json" in (content_type or "").lower() or text[:1] in ("{", "["):
        try:
            data = json.loads(text)
        except ValueError:
            data = None
    if template.strip():
        message = _render_template(template, data, text)
    elif isinstance(data, dict):
        message = _auto_extract(data) or json.dumps(data, ensure_ascii=False)
    elif isinstance(data, list):
        message = json.dumps(data, ensure_ascii=False)
    else:
        message = text
    message = message.strip()
    label = prefix.strip()
    if label:
        message = f"{label}\n{message}" if message else label
    return message[: max(1, max_len)].strip()
