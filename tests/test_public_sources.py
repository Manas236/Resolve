import json
import unittest

from resolve_pipeline.public_sources import article_watch_links, spotify_playlist_tracks


class PublicSourceTests(unittest.TestCase):
    def test_public_playlist_document_metadata(self):
        row = {"entityType": "track", "title": "Track - Slowed", "subtitle": "Artist,\xa0Second",
               "duration": 102140, "uri": "spotify:track:abcdefghijklmnopqrstuv"}
        entity = {"type": "playlist", "trackList": [row, row, {"entityType": "episode"},
                                                   dict(row, uri="spotify:track:bad")]}
        value = {"props": {"pageProps": {"state": {"data": {"entity": entity}}}}}
        tracks = spotify_playlist_tracks(
            '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(value) + "</script>",
        )
        self.assertEqual(len(tracks), 1)
        self.assertEqual((tracks[0].title, tracks[0].artist, tracks[0].duration_seconds),
                         ("Track - Slowed", "Artist, Second", 102.14))
        self.assertEqual(spotify_playlist_tracks("<h1>Access denied</h1>"), [])

    def test_articles_read_old_and_modern_embeds_without_lookalikes(self):
        page = '''
        <param name="movie" value="http://www.youtube.com/v/MEGSiX0JA-s?fs=1&amp;hl=en_GB">
        <a href="https://youtu.be/MEGSiX0JA-s?t=42">duplicate</a>
        <iframe src="//www.youtube-nocookie.com/embed/WO4tIrjBDkk"></iframe>
        <a href="https://www.youtube.com.example.org/watch?v=_Z5OookwOoY">lookalike</a>
        <a href="https://www.youtube.com/watch?v=no">invalid</a>
        '''
        self.assertEqual(article_watch_links(page), [
            "https://www.youtube.com/watch?v=MEGSiX0JA-s",
            "https://www.youtube.com/watch?v=WO4tIrjBDkk",
        ])
