import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app
import inspection_pipeline
import no_fly_zones


class DispatchHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "dispatch_history_test.db")
        self.original_app_db = app.DATABASE_PATH
        self.original_no_fly_db = no_fly_zones.DATABASE_PATH
        self.original_inspection_db = inspection_pipeline.DATABASE_PATH

        app.DATABASE_PATH = self.db_path
        no_fly_zones.DATABASE_PATH = self.db_path
        inspection_pipeline.DATABASE_PATH = self.db_path
        app.ensure_dispatch_history_database()

    def tearDown(self):
        app.DATABASE_PATH = self.original_app_db
        no_fly_zones.DATABASE_PATH = self.original_no_fly_db
        inspection_pipeline.DATABASE_PATH = self.original_inspection_db
        self.temp_dir.cleanup()

    def test_recorded_entries_keep_individual_timestamp_and_drone(self):
        summary = {
            "zone": {"south": 1.0, "north": 2.0, "west": 3.0, "east": 4.0},
            "dispatch_message": "Dispatch summary ready.",
            "captured_at": "2026-07-21T16:00:00+00:00",
            "addresses": [
                {
                    "id": 10,
                    "bin_type": "blue",
                    "label": "Blue Bin",
                    "condition": "overflowing",
                    "priority": "HIGH",
                    "priority_score": 92,
                    "request_id": "BIN-10",
                    "open_request_id": "REQ-10",
                    "has_open_request": True,
                    "duplicate_open_request": False,
                    "location_importance": "school",
                    "inspection_confidence": 0.95,
                    "loose_garbage_detected": True,
                    "request_created_at": "2026-07-20T10:00:00+00:00",
                    "last_pickup_at": "2026-07-19T10:00:00+00:00",
                    "lat": 43.73,
                    "lon": -79.76,
                    "address": "10 Sample St",
                    "captured_at": "2026-07-21T15:55:00+00:00",
                    "drone_id": "DRONE-001",
                    "latest_image": None,
                }
            ],
        }

        run_id = app.record_dispatch_run(summary, mission_id=7, started_at="2026-07-21T15:50:00+00:00", drone_id="DRONE-001")
        history = app.list_dispatch_history()

        self.assertEqual(run_id, 1)
        self.assertEqual(history["total"], 1)
        self.assertEqual(history["counts"]["blue"], 1)
        self.assertEqual(history["entries"][0]["captured_at"], "2026-07-21T15:55:00+00:00")
        self.assertEqual(history["entries"][0]["drone_id"], "DRONE-001")
        self.assertEqual(history["entries"][0]["priority_score"], 92)

    def test_delete_dispatch_entry_removes_only_target_entry(self):
        first_summary = {
            "zone": {"south": 1.0, "north": 2.0, "west": 3.0, "east": 4.0},
            "dispatch_message": "First run",
            "captured_at": "2026-07-21T16:00:00+00:00",
            "addresses": [
                {
                    "id": 1,
                    "bin_type": "green",
                    "label": "Green Bin",
                    "condition": "standard",
                    "priority": "NORMAL",
                    "priority_score": 20,
                    "request_id": "BIN-1",
                    "lat": 43.73,
                    "lon": -79.76,
                    "address": "1 First St",
                    "captured_at": "2026-07-21T16:00:01+00:00",
                    "drone_id": "DRONE-001",
                    "latest_image": None,
                }
            ],
        }
        second_summary = {
            "zone": {"south": 5.0, "north": 6.0, "west": 7.0, "east": 8.0},
            "dispatch_message": "Second run",
            "captured_at": "2026-07-21T17:00:00+00:00",
            "addresses": [
                {
                    "id": 2,
                    "bin_type": "garbage",
                    "label": "Black Bin",
                    "condition": "overflowing",
                    "priority": "HIGH",
                    "priority_score": 88,
                    "request_id": "BIN-2",
                    "lat": 43.74,
                    "lon": -79.75,
                    "address": "2 Second St",
                    "captured_at": "2026-07-21T17:00:01+00:00",
                    "drone_id": "DRONE-001",
                    "latest_image": None,
                }
            ],
        }

        app.record_dispatch_run(first_summary, mission_id=1, started_at="2026-07-21T15:59:00+00:00", drone_id="DRONE-001")
        app.record_dispatch_run(second_summary, mission_id=2, started_at="2026-07-21T16:59:00+00:00", drone_id="DRONE-001")

        history_before = app.list_dispatch_history()
        first_entry_id = history_before["entries"][0]["entry_id"]
        deleted = app.delete_dispatch_entry(first_entry_id)
        history_after = app.list_dispatch_history()

        self.assertEqual(history_before["total"], 2)
        self.assertIsNotNone(deleted)
        self.assertEqual(history_after["total"], 1)
        self.assertNotEqual(history_after["entries"][0]["entry_id"], first_entry_id)

    def test_duplicate_request_ids_are_normalized_in_history(self):
        first_summary = {
            "zone": {"south": 1.0, "north": 2.0, "west": 3.0, "east": 4.0},
            "dispatch_message": "First run",
            "captured_at": "2026-07-21T16:00:00+00:00",
            "addresses": [
                {
                    "id": 1,
                    "bin_type": "green",
                    "label": "Green Bin",
                    "condition": "standard",
                    "priority": "NORMAL",
                    "priority_score": 20,
                    "request_id": "BIN-1",
                    "lat": 43.73,
                    "lon": -79.76,
                    "address": "1 First St",
                    "captured_at": "2026-07-21T16:00:01+00:00",
                    "drone_id": "DRONE-001",
                    "latest_image": None,
                }
            ],
        }
        second_summary = {
            "zone": {"south": 5.0, "north": 6.0, "west": 7.0, "east": 8.0},
            "dispatch_message": "Second run",
            "captured_at": "2026-07-21T17:00:00+00:00",
            "addresses": [
                {
                    "id": 2,
                    "bin_type": "blue",
                    "label": "Blue Bin",
                    "condition": "standard",
                    "priority": "NORMAL",
                    "priority_score": 22,
                    "request_id": "BIN-1",
                    "lat": 43.74,
                    "lon": -79.75,
                    "address": "2 Second St",
                    "captured_at": "2026-07-21T17:00:01+00:00",
                    "drone_id": "DRONE-001",
                    "latest_image": None,
                }
            ],
        }

        app.record_dispatch_run(first_summary, mission_id=1, started_at="2026-07-21T15:59:00+00:00", drone_id="DRONE-001")
        app.record_dispatch_run(second_summary, mission_id=2, started_at="2026-07-21T16:59:00+00:00", drone_id="DRONE-001")
        app.ensure_dispatch_history_database()
        history = app.list_dispatch_history()

        request_ids = [entry["request_id"] for entry in history["entries"]]
        self.assertEqual(len(request_ids), len(set(request_ids)))
        self.assertIn("BIN-1", request_ids)

    def test_delete_dispatch_entry_cleans_linked_images(self):
        app.inspection_service.config.db_path = self.db_path
        app.inspection_service.config.storage_root = self.temp_dir.name
        app.inspection_service.original_dir = os.path.join(self.temp_dir.name, "captures", "original")
        app.inspection_service.processed_dir = os.path.join(self.temp_dir.name, "captures", "processed")
        os.makedirs(app.inspection_service.original_dir, exist_ok=True)
        os.makedirs(app.inspection_service.processed_dir, exist_ok=True)

        summary = {
            "zone": {"south": 1.0, "north": 2.0, "west": 3.0, "east": 4.0},
            "dispatch_message": "Dispatch summary ready.",
            "captured_at": "2026-07-22T16:00:00+00:00",
            "addresses": [
                {
                    "id": 11,
                    "bin_type": "blue",
                    "label": "Blue Bin",
                    "condition": "overflowing",
                    "priority": "HIGH",
                    "priority_score": 92,
                    "request_id": "BIN-11",
                    "lat": 43.73,
                    "lon": -79.76,
                    "address": "11 Sample St",
                    "captured_at": "2026-07-22T15:55:00+00:00",
                    "drone_id": "DRONE-001",
                    "latest_image": None,
                }
            ],
        }
        app.record_dispatch_run(summary, mission_id=7, started_at="2026-07-22T15:50:00+00:00", drone_id="DRONE-001")
        entry = app.list_dispatch_history()["entries"][0]

        with app.state_lock:
            app.bin_state["bins"] = [
                {
                    "id": 11,
                    "bin_type": "blue",
                    "label": "Blue Bin",
                    "condition": "overflowing",
                    "is_overflowing": True,
                    "priority_level": "HIGH",
                    "priority_score": 92,
                    "request_id": "BIN-11",
                    "lat": 43.73,
                    "lon": -79.76,
                    "address": "11 Sample St",
                }
            ]

        upload_response = app.app.test_client().post(
            "/bins/11/demo-image",
            data={
                "entry_id": str(entry["entry_id"]),
                "image": (io.BytesIO(b"dispatch-image"), "demo.jpg"),
            },
            content_type="multipart/form-data",
        )
        self.assertEqual(upload_response.status_code, 200)
        image_id = upload_response.get_json()["image"]["image_id"]

        deleted = app.delete_dispatch_entry(entry["entry_id"])
        self.assertIsNotNone(deleted)
        self.assertIsNone(inspection_pipeline.get_image_record(image_id, db_path=self.db_path))


if __name__ == "__main__":
    unittest.main()
