"""Provider-independent evidence ranking and timestamp selection."""

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
import unicodedata


def _name(value):
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


@dataclass(frozen=True)
class Mention:
    kind: str
    title: str
    source: str
    observed_at: datetime
    artist: str = ""
    stable_id: str = ""

    def key(self):
        if self.kind == "audio" and _name(self.artist):
            return self.kind, _name(self.artist), _name(self.title)
        if self.kind == "video" and self.stable_id.strip():
            return self.kind, self.stable_id.strip()
        raise ValueError("Track mentions require an artist and video mentions require a stable ID")


@dataclass(frozen=True)
class Candidate:
    mention: Mention
    sources: tuple[str, ...]
    score: float


def rank_mentions(mentions, *, now, feedback=None, half_life_days=14):
    """Deduplicate evidence per page, rank by independent recency-weighted mentions."""
    if now.utcoffset() is None or not isfinite(half_life_days) or half_life_days <= 0:
        raise ValueError("Provide a timezone-aware clock and a positive evidence half-life")
    now = now.astimezone(timezone.utc)
    by_identity = {}
    for mention in mentions:
        if (not _name(mention.title) or not mention.source.strip()
                or mention.observed_at.utcoffset() is None or mention.observed_at > now):
            raise ValueError("Invalid or future catalog evidence")
        sources = by_identity.setdefault(mention.key(), {})
        page = mention.source.strip()
        if page not in sources or mention.observed_at > sources[page].observed_at:
            sources[page] = mention

    ranked = []
    for key, sources in by_identity.items():
        score = sum(0.5 ** ((now - mention.observed_at).total_seconds() / 86400 / half_life_days)
                    for mention in sources.values())
        multiplier = (feedback or {}).get(key, 1)
        if not isfinite(multiplier) or not 0 <= multiplier <= 1:
            raise ValueError("Catalog feedback multipliers must be between 0 and 1")
        recent = max(sources.values(), key=lambda mention: (mention.observed_at, mention.source))
        ranked.append(Candidate(recent, tuple(sorted(sources)), score * multiplier))
    return sorted(ranked, key=lambda row: (-row.score, row.mention.key()))


METHODS = ("youtube_replay", "social", "gemini", "audio_energy")


@dataclass(frozen=True)
class Timing:
    method: str
    at_seconds: float
    source: str


def select_timestamp(candidates, *, duration, energy_peaks, gemini_grounding=False, tolerance=3):
    """Retain source attribution and require audio corroboration for social/Gemini."""
    if not isfinite(duration) or duration <= 0 or not isfinite(tolerance) or tolerance < 0:
        raise ValueError("Invalid timestamp bounds")
    peaks = tuple(peak for peak in energy_peaks if isfinite(peak) and 0 <= peak < duration)
    by_method = {method: [] for method in METHODS}
    for candidate in candidates:
        if (candidate.method in by_method and isfinite(candidate.at_seconds)
                and 0 <= candidate.at_seconds < duration and candidate.source.strip()):
            by_method[candidate.method].append(candidate)
    for method, choices in by_method.items():
        if method == "gemini" and not gemini_grounding:
            continue
        for candidate in choices:
            if (method in ("youtube_replay", "audio_energy")
                    or any(abs(peak - candidate.at_seconds) <= tolerance for peak in peaks)):
                return candidate
    return None
