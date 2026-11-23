"""Stable identities shared by Windows discovery and the bundle importer."""
import re
import unicodedata
from urllib.parse import parse_qs, urlsplit


def watch_url(value):
    try:
        parts = urlsplit(value)
        if parts.scheme != "https" or parts.username or parts.password:
            return None
        if parts.hostname == "youtu.be":
            identifier = parts.path.strip("/")
        elif parts.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com"} and parts.path == "/watch":
            identifier = parse_qs(parts.query).get("v", [""])[0]
        else:
            return None
    except ValueError:
        return None
    if re.fullmatch(r"[A-Za-z0-9_-]{11}", identifier):
        return f"https://www.youtube.com/watch?v={identifier}"
    return None


def words(value):
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", value).casefold()))


def asset_keys(item):
    kind = item["kind"]
    source = item.get("source", "")
    keys = {f"{kind}:url:{watch_url(source) or source}"} if source else set()
    if kind == "song":
        if item.get("spotify_id"):
            keys.add("song:spotify:" + item["spotify_id"])
        title, _, credits = item.get("title", "").partition(" — ")
        if credits:
            keys.add(f"song:recording:{words(title)}:{words(credits.split(',')[0])}")
    return keys
