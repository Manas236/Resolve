from datetime import datetime, timedelta, timezone
import unittest

from resolve_pipeline.catalog import Mention, Timing, rank_mentions, select_timestamp


class CatalogTests(unittest.TestCase):
    def test_fresh_independent_mentions_and_ranking_feedback(self):
        now = datetime(2026, 11, 22, tzinfo=timezone.utc)
        rows = [
            Mention("audio", "Start", "playlist", now, " Artist "),
            Mention("audio", "START", "playlist", now - timedelta(days=1), "artist"),
            Mention("audio", "Ｓｔａｒｔ", "article", now - timedelta(days=7), "ARTIST"),
            Mention("audio", "Start (Remix)", "playlist", now, "Artist"),
        ]
        result = rank_mentions(rows, now=now)
        self.assertEqual(result[0].sources, ("article", "playlist"))
        self.assertEqual(len(result), 2)
        adjusted = rank_mentions(rows, now=now, feedback={rows[0].key(): 0.4})
        self.assertEqual(adjusted[0].mention.title, "Start (Remix)")
        also = Mention("video", "Reposted artwork title", "video blog", now, stable_id="v")
        original = Mention("video", "Original title", "playlist", now, stable_id="v")
        self.assertEqual(len(rank_mentions([also, original], now=now)), 1)
        with self.assertRaises(ValueError):
            rank_mentions([Mention("audio", "Start", "playlist", now)], now=now)

    def test_timestamp_priority_corroboration_and_no_evidence(self):
        energy = Timing("audio_energy", 20, "local-decoder")
        social = Timing("social", 45, "public-sound-page")
        gemini = Timing("gemini", 22, "grounded-answer")
        invalid = Timing("youtube_replay", 120, "watch")
        valid = Timing("youtube_replay", 48, "watch")
        candidates = [energy, social, gemini, invalid]
        self.assertEqual(select_timestamp(candidates, duration=60, energy_peaks=[20]), energy)
        self.assertEqual(select_timestamp(candidates, duration=60, energy_peaks=[20],
                                          gemini_grounding=True), gemini)
        self.assertEqual(select_timestamp(candidates, duration=60, energy_peaks=[43]), social)
        self.assertEqual(select_timestamp(candidates + [valid], duration=60, energy_peaks=[]), valid)
        self.assertIsNone(select_timestamp([social, gemini], duration=60, energy_peaks=[]))
