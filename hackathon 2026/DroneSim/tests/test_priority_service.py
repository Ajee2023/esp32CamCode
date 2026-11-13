import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from priority_service import calculate_priority, prioritize_dispatch_items


class PriorityServiceTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 7, 20, 12, 0, tzinfo=timezone.utc)

    def test_overflowing_bin_starts_high(self):
        result = calculate_priority(
            {
                "id": 1,
                "bin_type": "garbage",
                "is_overflowing": True,
                "address": "10 Sample Rd",
            },
            self.now,
        )
        self.assertEqual(result["priority_level"], "HIGH")
        self.assertGreaterEqual(result["priority_score"], 80)

    def test_overflowing_school_bin_can_become_critical(self):
        result = calculate_priority(
            {
                "id": 2,
                "bin_type": "garbage",
                "is_overflowing": True,
                "address": "123 Public School Lane",
            },
            self.now,
        )
        self.assertEqual(result["priority_level"], "CRITICAL")
        self.assertGreaterEqual(result["priority_score"], 120)

    def test_older_request_increases_priority(self):
        newer = calculate_priority(
            {
                "id": 3,
                "bin_type": "blue",
                "is_overflowing": False,
                "request_created_at": self.now.isoformat(),
            },
            self.now,
        )
        older = calculate_priority(
            {
                "id": 4,
                "bin_type": "blue",
                "is_overflowing": False,
                "request_created_at": (self.now - timedelta(hours=72)).isoformat(),
            },
            self.now,
        )
        self.assertGreater(older["priority_score"], newer["priority_score"])

    def test_loose_garbage_raises_priority(self):
        baseline = calculate_priority(
            {
                "id": 5,
                "bin_type": "green",
                "is_overflowing": True,
            },
            self.now,
        )
        elevated = calculate_priority(
            {
                "id": 6,
                "bin_type": "green",
                "is_overflowing": True,
                "loose_garbage_detected": True,
            },
            self.now,
        )
        self.assertGreater(elevated["priority_score"], baseline["priority_score"])
        self.assertEqual(elevated["priority_level"], "CRITICAL")

    def test_dispatch_items_sort_by_score_then_request_age(self):
        items = prioritize_dispatch_items(
            [
                {
                    "id": 7,
                    "bin_type": "blue",
                    "is_overflowing": False,
                    "request_created_at": (self.now - timedelta(hours=12)).isoformat(),
                },
                {
                    "id": 8,
                    "bin_type": "garbage",
                    "is_overflowing": True,
                    "request_created_at": (self.now - timedelta(hours=1)).isoformat(),
                },
                {
                    "id": 9,
                    "bin_type": "green",
                    "is_overflowing": True,
                    "request_created_at": (self.now - timedelta(hours=24)).isoformat(),
                },
            ],
            self.now,
        )
        self.assertEqual(items[0]["id"], 9)
        self.assertEqual(items[1]["id"], 8)

    def test_duplicate_open_requests_are_flagged(self):
        items = prioritize_dispatch_items(
            [
                {
                    "id": 10,
                    "bin_type": "garbage",
                    "is_overflowing": True,
                    "has_open_request": True,
                    "open_request_id": "REQ-100",
                },
                {
                    "id": 11,
                    "bin_type": "garbage",
                    "is_overflowing": True,
                    "has_open_request": True,
                    "open_request_id": "REQ-100",
                },
            ],
            self.now,
        )
        flagged = [item for item in items if item["duplicate_open_request"]]
        self.assertEqual(len(flagged), 1)


if __name__ == "__main__":
    unittest.main()
