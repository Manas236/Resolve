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


def genre_catalog():
    """Read local exports afresh so a running dashboard sees new candidates."""
    import os
    paths = [Path("state/genre_quotes/genre_quotes.jsonl"),
             Path(os.environ.get("RESOLVE_GENRE_BANK", "state/genre_quotes/candidates.jsonl"))]
    rows = {}
    seen = set()
    for path in paths:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            creator = (row.get("creator") or row["speaker"]).strip().lstrip("@").casefold()
            key = " ".join(row["text"].casefold().split())
            if key in seen:
                continue
            seen.add(key)
            rows[row["id"]] = {**row, "creator": creator, "speaker": creator}
    return rows


def check_cap(rows, states):
    from collections import Counter
    creators = [q["creator"] for q in rows.values() if states.get(q["id"]) in {"active", "pinned"}]
    if len(creators) >= 60 and max(Counter(creators).values()) * 100 > 15 * len(creators):
        raise ValueError("This change exceeds the 15% active creator limit.")


def select_genre(format_id, excluded, speakers, states):
    import re
    choices = []
    for q in genre_catalog().values():
        if (states.get(q["id"]) not in {"active", "pinned"} or format_id not in q["formats"]
                or q["id"] in excluded or q["creator"] in speakers):
            continue
        body = q["text"]
        if format_id == "P" and not (35 <= len(body.split()) <= 115 and
                                      2 <= len(re.split(r"(?<=[.!?])\s+", body.strip())) <= 5):
            continue
        cut = q.get("split") if format_id == "F4" else None
        if format_id == "F4" and not (isinstance(cut, int) and 0 < cut < len(body)
                                      and (body[cut].isspace() or body[cut-1].isspace())):
            continue
        choices.append({**q, "hook": body[:cut].rstrip(), "lines": [body[cut:].lstrip()] if cut else []})
    if not choices:
        raise ValueError(f"No approved {format_id} creator text meets the format and rotation rules.")
    return max(choices, key=lambda q: (states[q["id"]] == "pinned", q.get("views") or 0, q["id"]))
