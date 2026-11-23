"""Sentence-bounded speech selection and music matching for F1."""
from dataclasses import dataclass, replace
from pathlib import Path
import re
import subprocess

from .reel_text import gemini_json

MOODS = {"aggressive", "defiant", "calm resolve", "grief-to-strength"}
TERMINAL = re.compile(r"""[.!?]['"’”)]*$""")


@dataclass(frozen=True)
class Passage:
    words: tuple
    energy: float
    clip_start_ms: int
    clip_end_ms: int

    @property
    def transcript(self):
        return " ".join(word.text for word in self.words)


def candidates(words, energy=lambda a, b: 0):
    """Enumerate complete passages. Unpunctuated tails do not qualify."""
    bounds = [0]
    for index, word in enumerate(words):
        if TERMINAL.search(word.text) and not re.fullmatch(r"(?:[A-Z]|Mr|Mrs|Dr|Ms|St)\.", word.text):
            bounds.append(index + 1)
    for i, begin in enumerate(bounds[:-1]):
        start = words[begin].start_ms
        for finish in bounds[i + 1:]:
            end = words[finish - 1].end_ms
            if end - start > 35000:
                break
            if end - start >= 20000:
                yield Passage(tuple(words[begin:finish]), energy(start, end), start, end)


def select_passage(words, samples, key="", sample_rate=16000):
    import numpy as np

    squares = np.concatenate(([0], np.cumsum(np.square(samples, dtype=np.float64))))

    def energy(start, end):
        lo = min(start * sample_rate // 1000, len(squares) - 1)
        hi = min(end * sample_rate // 1000, len(squares) - 1)
        return float(np.sqrt(max(0, squares[hi] - squares[lo]) / (hi - lo))) if hi > lo else 0

    options = sorted(candidates(words, energy), key=lambda p: (p.energy, len(p.words)), reverse=True)
    if not options:
        raise ValueError("Speech has no complete sentence-bounded passage lasting 20–35 seconds.")
    shortlist = options[:16]
    mood, intensity = "calm resolve", min(1, shortlist[0].energy * 5)
    selected, reason = shortlist[0], "Highest speech RMS among complete 20–35s passages."
    if key:
        supplied = "\n".join(f"{i}: {p.transcript}" for i, p in enumerate(shortlist))
        data = gemini_json(key, "Pick a self-contained gym motivational excerpt from these numbered sentence-bounded passages. "
                           "Return JSON with integer index, mood (aggressive, defiant, calm resolve, grief-to-strength), "
                           f"energy (number 0–1), and short reason.\n{supplied}")
        if isinstance(data, dict) and type(data.get("index")) is int and 0 <= data["index"] < len(shortlist):
            selected, reason = shortlist[data["index"]], str(data.get("reason", "Gemini selected passage."))[:300]
            mood = data["mood"] if data.get("mood") in MOODS else mood
            if type(data.get("energy")) in (int, float) and 0 <= data["energy"] <= 1:
                intensity = data["energy"]
    return selected, {"mood": mood, "energy": round(intensity, 3), "passage_reason": reason}


def decode_speech(path, limit=None):
    import numpy as np

    duration = ["-t", str(limit)] if limit else []
    pcm = subprocess.check_output(["ffmpeg", "-nostdin", "-v", "error", "-protocol_whitelist", "file",
                                   "-i", str(Path(path).resolve()), *duration, "-map", "0:a:0", "-ac", "1",
                                   "-ar", "16000", "-f", "f32le", "pipe:1"], timeout=180)
    return np.frombuffer(pcm, dtype="<f4")


def relative_words(passage):
    return [replace(word, start_ms=word.start_ms - passage.clip_start_ms,
                    end_ms=word.end_ms - passage.clip_start_ms) for word in passage.words]
