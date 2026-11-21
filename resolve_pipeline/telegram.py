"""Telegram review callback parsing, independent of network transport."""

import re

_CALLBACK = re.compile(r"(approve|reject|regenerate|muted):([A-Za-z0-9_-]{1,48})")


def review_callback(update, *, chat_id, reviewer_id):
    """Return an action and reel ID only for the configured reviewer and chat."""
    callback = update.get("callback_query") or {}
    sender = callback.get("from") or {}
    message = callback.get("message") or {}
    chat = message.get("chat") or {}
    if sender.get("id") != reviewer_id or chat.get("id") != chat_id:
        return None
    data = callback.get("data")
    match = _CALLBACK.fullmatch(data) if isinstance(data, str) else None
    return match.groups() if match is not None else None
