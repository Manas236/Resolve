"""Published passages and their immutable source records."""
from functools import lru_cache
from pathlib import Path
import json


@lru_cache(maxsize=1)
def catalog():
    with Path(__file__).with_name("quotes.jsonl").open(encoding="utf-8") as stream:
        return {row["id"]: row for row in map(json.loads, stream)}


def select(format_id, excluded=(), speakers=(), states=None):
    states = states or {}
    eligible = [q for q in catalog().values() if format_id in q["formats"]
                and q["id"] not in excluded and q["speaker"] not in speakers
                and states.get(q["id"]) != "retired"]
    if not eligible:
        raise ValueError("No published quote meets the current format and repeat windows.")
    q = max(eligible, key=lambda q: (states.get(q["id"]) == "pinned", q["likes"] or 0,
                                     q["sites"] or 0, q["fit"], q["id"]))
    cut = q["split"] if format_id == "F4" else None
    return {**q, "hook": q["text"][:cut].rstrip(), "lines": [q["text"][cut:]] if cut else []}
