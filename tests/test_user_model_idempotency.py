import unittest
from app.services.agent.personalization.user_model import UserModel


class UserModelIdempotencyTests(unittest.TestCase):
    def test_repeated_aggregation_does_not_inflate_habits(self):
        actions = [
            {"id": 1, "tool": "first", "target": "alpha", "args": "{}", "ok": 1,
             "created_at": "1", "verification_verdict": "PASS"},
            {"id": 2, "tool": "second", "target": "beta", "args": "{}", "ok": 1,
             "created_at": "2", "verification_verdict": "PASS"},
        ]
        model = UserModel(db_path=None, action_provider=lambda: list(actions), min_observations=1)
        model.start()
        self.assertEqual(model.aggregate_from_provider(), 1)
        self.assertEqual(model.aggregate_from_provider(), 0)
        habits = model.top_habits()
        self.assertEqual(len(habits), 1)
        self.assertEqual(habits[0]["count"], 1)

    def test_incremental_batches_preserve_one_cross_batch_pair(self):
        actions = [{"id": 1, "tool": "first", "target": "alpha", "args": "{}", "ok": 1,
                    "created_at": "1", "verification_verdict": "PASS"}]
        model = UserModel(db_path=None, action_provider=lambda: list(actions), min_observations=1)
        model.start()
        self.assertEqual(model.aggregate_from_provider(), 0)
        actions.append({"id": 2, "tool": "second", "target": "beta", "args": "{}", "ok": 1,
                        "created_at": "2", "verification_verdict": "PASS"})
        self.assertEqual(model.aggregate_from_provider(), 1)
        self.assertEqual(model.top_habits()[0]["count"], 1)

    def test_failed_or_unknown_rows_are_not_learned(self):
        actions = [
            {"id": 1, "tool": "first", "target": "alpha", "args": "{}", "ok": 1,
             "created_at": "1", "verification_verdict": "UNKNOWN"},
            {"id": 2, "tool": "second", "target": "beta", "args": "{}", "ok": 0,
             "created_at": "2", "verification_verdict": "FAIL"},
        ]
        model = UserModel(db_path=None, action_provider=lambda: actions, min_observations=1)
        model.start()
        self.assertEqual(model.aggregate_from_provider(), 0)
        self.assertEqual(model.top_habits(), [])


if __name__ == "__main__": unittest.main()
