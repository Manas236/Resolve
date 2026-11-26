import unittest
from unittest.mock import patch

from resolve_pipeline import quotes
from resolve_pipeline.caption_style import quiet_ass


class GenreBankTests(unittest.TestCase):
    def test_threshold_and_approval(self):
        rows = {i: {"creator": str(i % 7), "id": i} for i in range(60)}
        states = dict.fromkeys(rows, "active")
        quotes.check_cap(rows, states)
        rows[59]["creator"] = "0"
        with self.assertRaises(ValueError):
            quotes.check_cap(rows, states)
        states[59] = "retired"
        quotes.check_cap(rows, states)

    def test_candidate_and_recent_creator_excluded(self):
        row = dict(id="1", creator="one", text="A fixture.", formats=["L"])
        with patch.object(quotes, "genre_catalog", return_value={"1": row}):
            for states, recent in [({}, []), ({"1": "active"}, ["one"])]:
                with self.assertRaises(ValueError):
                    quotes.select_genre("L", [], recent, states)
            self.assertEqual(quotes.select_genre("L", [], [], {"1": "active"})["hook"], row["text"])
        self.assertNotIn("invisible", quiet_ass("L", row["text"], [], [], 10000, speaker="invisible"))
