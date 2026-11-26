"""Editorial contracts shared by generation, editing and rendering."""
import json
import logging
import random
import re
from pathlib import Path

BANK = Path(__file__).with_name("poetic_bank.txt")
RUBRIC = ("theme_fit", "clarity", "quotability", "closing_turn", "originality", "length")
BAD = re.compile(r"\b(grind|beast mode|no excuses|hustle|reps?|gains|leg day)\b|^(earn|push|rise|chase|never quit|keep going)\b", re.I)
SENTENCES = re.compile(r"(?<=[.!?])\s+")


def reading_seconds(text, fmt):
    low, high = (20, 45) if fmt == "P" else (10, 15)
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
then a resolving clause separated by a comma. Write a clear thought understood in one read
about discipline, effort, sacrifice, becoming, solitude, self-hatred turning into self-respect,
irony or paradox. Tone reference only: Luck only favors those who are willing to take action,
take risks, and prepare for opportunities. Use fresh words and put the turn last. Plain declarative
statements are welcome. At most one image. No miniature stories, imperatives or slogans.
Return a JSON object with candidates, each containing only text."""
    response = request(prompt)
    rows = response.get("candidates") if isinstance(response, dict) else None
    if not isinstance(rows, list) or len(rows) != 5:
        return None
    if any(not isinstance(row, dict) or not isinstance(row.get("text"), str) for row in rows):
        return None
    texts = [row["text"] for row in rows]
    judgment = request(f"Judge the five texts independently for {fmt}. Theme fit means discipline, effort, "
                       "sacrifice, becoming, solitude, self-respect, irony or paradox. Clarity means understood "
                       "on first read. Reward memorable wording and a turn at the end. Length is 40-75 words "
                       "for P or 10-25 for L/F4. Return scores as a five-element array in input order, each "
                       f"with integer ratings 0-5 for {', '.join(RUBRIC)}. Texts: {json.dumps(texts)}")
    grades = judgment.get("scores") if isinstance(judgment, dict) else None
    if not isinstance(grades, list) or len(grades) != 5:
        return None
    ranked = []
    for row, grade in zip(rows, grades):
        try:
            piece = as_fields(validate_piece(row["text"], fmt), fmt)
            scores = [grade[k] for k in RUBRIC]
            if any(type(n) is not int or not 0 <= n <= 5 for n in scores):
                continue
            if grade["theme_fit"] >= 4 and unused(piece, used, normalize):
                ranked.append((sum(scores), piece | {"scores": grade}))
        except (KeyError, TypeError, ValueError):
            continue
    return max(ranked, key=lambda item: item[0])[1] if ranked else None


def fallback(fmt, used, normalize):
    options = []
    seen = []
    for text in BANK.read_text(encoding="utf-8").splitlines():
        try:
            piece = as_fields(validate_piece(text, fmt), fmt)
        except ValueError:
            continue
        if unused(piece, used, normalize):
            options.append(piece)
        else:
            seen.append(piece)
    if not options:
        if not seen:
            raise ValueError("No valid manuscript entries for this format.")
        logging.getLogger(__name__).warning("Reusing least recently used manuscript entry for %s", fmt)
        def last_use(piece):
            blocks = [piece["hook"], *piece["lines"]]
            return max(used.get(normalize(block), 0) for block in [*blocks, " ".join(blocks)]) if isinstance(used, dict) else 0
        return min(seen, key=last_use) | {"source": "bank"}
    return random.choice(options) | {"source": "bank"}
