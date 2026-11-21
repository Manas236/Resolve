import unittest

from resolve_pipeline.telegram import review_callback


class ReviewCallbackTests(unittest.TestCase):
    def test_callback_requires_reviewer_and_chat(self):
        update = {"callback_query": {"from": {"id": 20}, "message": {"chat": {"id": 30}},
                                     "data": "approve:reel-1"}}
        self.assertEqual(review_callback(update, chat_id=30, reviewer_id=20), ("approve", "reel-1"))
        self.assertIsNone(review_callback(update, chat_id=30, reviewer_id=21))
        self.assertIsNone(review_callback(update, chat_id=31, reviewer_id=20))
        update["callback_query"]["data"] = "approve:../reel"
        self.assertIsNone(review_callback(update, chat_id=30, reviewer_id=20))
