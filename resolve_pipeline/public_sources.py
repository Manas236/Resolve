"""Extract source evidence from public HTML without a platform API key."""

from dataclasses import dataclass
from html.parser import HTMLParser
import json
import re
from urllib.parse import parse_qs, urlsplit

_ID = re.compile(r"[A-Za-z0-9_-]{11}")
_TRACK_ID = re.compile(r"spotify:track:([A-Za-z0-9]{22})")


@dataclass(frozen=True)
class PlaylistTrack:
    id: str
    title: str
    artist: str
    duration_seconds: float


class _SourcePage(HTMLParser):
    def __init__(self):
        super().__init__()
        self.next_json = []
        self.addresses = []
        self.capture = False

    def handle_starttag(self, tag, attributes):
        attributes = dict(attributes)
        if tag == "script" and attributes.get("id") == "__NEXT_DATA__":
            self.capture = True
        for name in {"a": ("href",), "iframe": ("src",), "embed": ("src",),
                     "object": ("data",), "param": ("value",)}.get(tag, ()):
            if attributes.get(name):
                self.addresses.append(attributes[name])

    def handle_endtag(self, tag):
        if tag == "script":
            self.capture = False

    def handle_data(self, data):
        if self.capture:
            self.next_json.append(data)


def spotify_playlist_tracks(html):
    page = _SourcePage()
    page.feed(html)
    if not page.next_json:
        return []
    try:
        entity = json.loads("".join(page.next_json))["props"]["pageProps"]["state"]["data"]["entity"]
        entries = entity["trackList"]
    except (KeyError, TypeError, ValueError):
        return []
    if entity.get("type") != "playlist" or not isinstance(entries, list):
        return []
    tracks = {}
    for row in entries:
        if not isinstance(row, dict) or row.get("entityType") != "track":
            continue
        identifier = _TRACK_ID.fullmatch(str(row.get("uri", "")))
        title, artist, duration = row.get("title"), row.get("subtitle"), row.get("duration")
        if (identifier and isinstance(title, str) and title.strip()
                and isinstance(artist, str) and artist.strip()
                and type(duration) is int and 0 < duration <= 86_400_000):
            track_id = identifier.group(1)
            tracks.setdefault(track_id, PlaylistTrack(track_id, title.strip(),
                                                      " ".join(artist.split()), duration / 1000))
    return list(tracks.values())


def article_watch_links(html):
    """Read public YouTube links or embeds and normalize to single-video watch URLs."""
    page = _SourcePage()
    page.feed(html)
    ids = {}
    for address in page.addresses:
        try:
            parts = urlsplit(address.strip())
            hostname = parts.hostname
        except ValueError:
            continue
        if parts.scheme not in ("", "http", "https") or parts.username or parts.password:
            continue
        path = parts.path.split("/")
        identifier = None
        if hostname == "youtu.be" and len(path) == 2:
            identifier = path[1]
        elif hostname in ("youtube.com", "www.youtube.com", "m.youtube.com", "www.youtube-nocookie.com"):
            if parts.path == "/watch":
                identifier = parse_qs(parts.query).get("v", (None,))[0]
            elif len(path) == 3 and path[1] in ("v", "embed", "shorts"):
                identifier = path[2]
        if identifier is not None and _ID.fullmatch(identifier):
            ids[identifier] = f"https://www.youtube.com/watch?v={identifier}"
    return list(ids.values())
