"""Generate and validate text burned into a reel."""
import json
from pathlib import Path
import random
import re
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BANK = Path(__file__).with_name("caption_bank.txt")
MODEL = "gemini-2.5-flash"


def normalized(line):
    return " ".join(re.findall(r"[a-z0-9]+", line.casefold()))


def checked_text(hook, lines, format_id):
    if not isinstance(hook, str) or not isinstance(lines, list) or any(not isinstance(line, str) for line in lines):
        raise ValueError("Invalid reel text")
    if not (2 <= len(lines) <= 4 if format_id == "F2" else not lines):
        raise ValueError("Use 2–4 lines for F2. Speech formats use a hook.")
    values = [" ".join(line.split()) for line in [hook, *lines]]
    for line in values:
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9’' ,.!?–-]{1,41}", line) or not 2 <= len(line.split()) <= 6:
            raise ValueError("Use 2–6 words and up to 42 characters per line, without hashtags.")
    if len({normalized(line) for line in values}) != len(values):
        raise ValueError("Each line must be distinct.")
    return {"hook": values[0], "lines": values[1:]}


def gemini_text(api_key, format_id, context, used):
    prompt = (
        "Write original English on-screen text for a gym training reel. Direct coaching cues, "
        "2–6 words and at most 42 characters each. No hashtags, brands, profanity, cliché, or filler. "
        "Return JSON with a hook string and a lines array. "
        + ("Write 2–4 separate punchy lines that build on the hook. "
           if format_id == "F2" else "Return an empty lines array. ")
        + f"Footage context: {context[:180]}. Avoid repeating: {', '.join(sorted(used))[:3500]}."
    )
    data = gemini_json(api_key, prompt)
    try:
        return checked_text(data["hook"], data["lines"], format_id)
    except (ValueError, KeyError, TypeError):
        return None


def gemini_json(api_key, prompt):
    request = Request(
        f"https://generativelanguage.googleapis.com/v1beta/models/{MODEL}:generateContent",
        data=json.dumps({"contents": [{"parts": [{"text": prompt}]}],
                         "generationConfig": {"responseMimeType": "application/json",
                                              "temperature": 0.85}}).encode(),
        headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
    )
    try:
        with urlopen(request, timeout=12) as response:
            payload = json.load(response)
        return json.loads(payload["candidates"][0]["content"]["parts"][0]["text"])
    except HTTPError as error:
        error.close()
        return None
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def choose_text(format_id, used, proposed=None, *, context=""):
    if proposed and not any(normalized(line) in used for line in [proposed["hook"], *proposed["lines"]]):
        return {**proposed, "source": "gemini"}
    bank = BANK.read_text(encoding="utf-8").splitlines()
    choices = [line for line in bank if normalized(line) not in used]
    count = 4 if format_id == "F2" else 1
    if len(choices) < count:
        raise ValueError("Caption bank exhausted for the last 100 reels. Add fresh lines or restore Gemini.")
    context = normalized(context)
    topical = bank[96:144]
    if any(word in context for word in ("sprint", "track", "running")):
        topical = bank[48:72]
    elif any(word in context for word in ("boxing", "punch", "kickbox")):
        topical = bank[72:96]
    elif any(word in context for word in ("squat", "deadlift", "powerlift", "barbell", "weight")):
        topical = bank[:48]
    topical = [line for line in topical if normalized(line) not in used]
    hook, *lines = random.sample(topical if len(topical) >= count else choices, count)
    return {**checked_text(hook, lines, format_id), "source": "bank"}
