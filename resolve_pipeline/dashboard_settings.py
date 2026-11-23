"""Validation for persisted Resolve operator settings."""

import re

from .caption_style import PRESETS

DEFAULTS = {
    "posting_slots": "09:00,18:00",
    "volume": "2",
    "mode": "approval",
    "refresh_cadence": "weekly",
    "format_weights": "1,1,1",
    "caption_preset": "anton",
}


def validate_settings(values):
    if values.get("caption_preset", "anton") not in PRESETS:
        raise ValueError("Choose a caption preset.")
    slots = [item.strip() for item in values["posting_slots"].split(",")]
    if (len(set(slots)) != len(slots) or
            any(not re.fullmatch(r"(?:[01][0-9]|2[0-3]):[0-5][0-9]", slot) for slot in slots)):
        raise ValueError("Enter distinct posting slots in HH:MM format")
    volume = int(values["volume"])
    if not 1 <= volume <= len(slots):
        raise ValueError("Daily volume must fit the available slots")
    if values["mode"] not in ("approval", "auto") or values["refresh_cadence"] not in ("weekly", "monthly"):
        raise ValueError("Invalid approval mode or refresh cadence")
    weights = [int(item) for item in values["format_weights"].split(",")]
    if len(weights) != 3 or not any(weights) or any(not 0 <= weight <= 100 for weight in weights):
        raise ValueError("Provide three format weights from 0 to 100 with at least one positive")
    return {**values, "posting_slots": ",".join(sorted(slots)), "volume": str(volume),
            "format_weights": ",".join(map(str, weights))}
