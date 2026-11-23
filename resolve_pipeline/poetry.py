"""Editorial contracts shared by generation, editing and rendering."""
import random
import re
from pathlib import Path

BANK = Path(__file__).with_name("poetic_bank.txt")
RUBRIC = ("originality", "imagery", "closing_turn", "no_cliches", "length")
BAD = re.compile(r"\b(grind|beast mode|no excuses|hustle|reps?|gains|leg day)\b|^(earn|push|rise|chase|never quit|keep going)\b", re.I)
SENTENCES = re.compile(r"(?<=[.!?])\s+")


def reading_seconds(text, fmt):
    low, high = (20, 35) if fmt == "P" else (10, 15)
    return min(high, max(low, len(text.split()) / 3.3 + 4))


def validate_piece(text, fmt):
    if not isinstance(text, str):
        raise ValueError("Enter a piece of text.")
    text = " ".join(text.split())
    low, high = (40, 75) if fmt == "P" else (10, 25)
    if not low <= len(text.split()) <= high or not re.fullmatch(r"[A-Za-z0-9’' ,.!?—–:;-]+", text):
        raise ValueError(f"Use {low}–{high} words of sentence-case English prose.")
    if not text[0].isupper() or re.search(r"\b[A-Z]{3,}\b", text) or text.isupper() or BAD.search(text) or text[-1] not in ".!?":
        raise ValueError("Use a complete, quiet observation without slogans.")
    if fmt == "L" and len(SENTENCES.split(text)) != 1:
        raise ValueError("A one-liner needs one sentence.")
    if fmt == "P" and not 3 <= len(SENTENCES.split(text)) <= 4:
        raise ValueError("A paragraph needs three or four sentences.")
    return text


def as_fields(text, fmt):
    if fmt == "F4":
        parts = re.split(r",\s+|\s+(?=until\b|before\b|but\b|though\b|when\b)", text, maxsplit=1)
        if len(parts) != 2 or min(len(p.split()) for p in parts) < 3:
            raise ValueError("A reveal needs two distinct clauses.")
        return {"hook": parts[0], "lines": [parts[1]]}
    return {"hook": text, "lines": []}


def unused(piece, used, normalize):
    blocks = [piece["hook"], *piece["lines"]]
    return all(normalize(text) not in used for text in [*blocks, " ".join(blocks)])


def candidate(request, fmt, context, used, normalize):
    prompt = f"""Write exactly five original English pieces for format {fmt} inspired by {context[:180]}.
P: 40–75 words in 3–4 sentences. L: 10–25 words. F4: 10–25 words, an apparent paradox,
then a resolving clause separated by a comma. Quietly intense observations on discipline,
becoming, solitude or acceptance. Third person or universal perspective. Use concrete detail
and a turn at the end. No borrowed captions, quotes, lyrics, imperatives, gym slang, clichés,
emojis, hashtags or shouting. Score each criterion from 0 to 5. Originality: a stock saying
earns zero, a fresh observation five. Imagery: name something a reader can see or hear.
Closing turn: the ending changes the meaning of the opening. No clichés: reward language
free of slogans and familiar motivational formulas. Length: full marks only when the word
and sentence limits are met without padding. Use these exact keys: {', '.join(RUBRIC)}. Return a JSON
object with candidates, each containing text and scores keyed by those five rubric names."""
    response = request(prompt)
    rows = response.get("candidates") if isinstance(response, dict) else None
    if not isinstance(rows, list) or len(rows) != 5:
        return None
    ranked = []
    for row in rows:
        try:
            piece = as_fields(validate_piece(row["text"], fmt), fmt)
            scores = [row["scores"][k] for k in RUBRIC]
            if any(type(n) is not int or not 0 <= n <= 5 for n in scores):
                continue
            if unused(piece, used, normalize):
                ranked.append((sum(scores), piece | {"scores": row["scores"]}))
        except (KeyError, TypeError, ValueError):
            continue
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


def fallback(fmt, used, normalize):
    options = []
    for text in BANK.read_text(encoding="utf-8").splitlines():
        try:
            piece = as_fields(validate_piece(text, fmt), fmt)
        except ValueError:
            continue
        if unused(piece, used, normalize):
            options.append(piece)
    if not options:
        raise ValueError("All eligible fallback pieces appeared in the last 200 reels.")
    return random.choice(options) | {"source": "bank"}
