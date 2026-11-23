"""Validation for persisted Resolve operator settings."""

import re

from .caption_style import PRESETS

DEFAULTS = {
    "posting_slots": "09:00,18:00",
    "volume": "2",
    "mode": "approval",
    "refresh_cadence": "weekly",
    "format_weights": "3,0,0,1,4,4",
    "kokoro_voice": "am_michael",
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
    if len(weights) == 3:
        weights.append(1)
    if len(weights) == 4:
        weights.extend([4, 4])
    if len(weights) == 6:
        weights[1:3] = [0, 0]
    if len(weights) != 6 or not any(weights) or any(not 0 <= weight <= 100 for weight in weights):
        raise ValueError("Provide six format weights from 0 to 100 with at least one positive")
    if not re.fullmatch(r"[a-z]{2}_[a-z0-9_]+", values.get("kokoro_voice", "am_michael")):
        raise ValueError("Enter a Kokoro voice ID such as am_michael.")
    return {**values, "posting_slots": ",".join(sorted(slots)), "volume": str(volume),
            "format_weights": ",".join(map(str, weights))}
