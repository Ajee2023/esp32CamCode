import os
import sys
import tempfile
import unittest
from base64 import b64encode

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app
import inspection_pipeline
import no_fly_zones


class TruckDispatchTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "truck_dispatch_test.db")
        self.original_app_db = app.DATABASE_PATH
        self.original_no_fly_db = no_fly_zones.DATABASE_PATH
        self.original_inspection_db = inspection_pipeline.DATABASE_PATH
        self.original_service_db = app.inspection_service.config.db_path
        self.original_service_root = app.inspection_service.config.storage_root
        self.original_service_original_dir = app.inspection_service.original_dir
        self.original_service_processed_dir = app.inspection_service.processed_dir

        app.DATABASE_PATH = self.db_path
        no_fly_zones.DATABASE_PATH = self.db_path
        inspection_pipeline.DATABASE_PATH = self.db_path
        app.inspection_service.config.db_path = self.db_path
        app.inspection_service.config.storage_root = self.temp_dir.name
        app.inspection_service.original_dir = os.path.join(self.temp_dir.name, "captures", "original")
        app.inspection_service.processed_dir = os.path.join(self.temp_dir.name, "captures", "processed")
        os.makedirs(app.inspection_service.original_dir, exist_ok=True)
        os.makedirs(app.inspection_service.processed_dir, exist_ok=True)
        app.ensure_dispatch_history_database()
        self.client = app.app.test_client()

    def tearDown(self):
        app.DATABASE_PATH = self.original_app_db
        no_fly_zones.DATABASE_PATH = self.original_no_fly_db
        inspection_pipeline.DATABASE_PATH = self.original_inspection_db
        app.inspection_service.config.db_path = self.original_service_db
        app.inspection_service.config.storage_root = self.original_service_root
        app.inspection_service.original_dir = self.original_service_original_dir
        app.inspection_service.processed_dir = self.original_service_processed_dir
        self.temp_dir.cleanup()

    def _record_sample_dispatch_entry(self):
        summary = {
            "zone": {"south": 1.0, "north": 2.0, "west": 3.0, "east": 4.0},
            "dispatch_message": "Dispatch summary ready.",
            "captured_at": "2026-07-21T16:00:00+00:00",
            "addresses": [
                {
                    "id": 3,
                    "bin_type": "blue",
                    "label": "Blue Bin",
                    "condition": "overflowing",
                    "priority": "HIGH",
                    "priority_score": 92,
                    "request_id": "BIN-3",
                    "lat": 43.73,
                    "lon": -79.76,
                    "address": "3 Route St",
                    "captured_at": "2026-07-21T16:00:01+00:00",
                    "drone_id": "DRONE-001",
                    "latest_image": None,
                }
            ],
        }
        app.record_dispatch_run(summary, mission_id=5, started_at="2026-07-21T15:58:00+00:00", drone_id="DRONE-001")
        return app.list_dispatch_history()["entries"][0]["entry_id"]

    def test_assign_dispatch_entry_to_truck_and_close_via_api(self):
        entry_id = self._record_sample_dispatch_entry()

        assigned = app.assign_dispatch_entries_to_truck([entry_id], "TRUCK-7", "ROUTE-12")
        self.assertEqual(assigned["assigned_entry_ids"], [entry_id])
        self.assertEqual(assigned["truck_dispatch"]["open_item_count"], 1)

        batch = assigned["truck_dispatch"]["batches"][0]
        item = batch["items"][0]
        self.assertEqual(item["status"], "ASSIGNED")
        self.assertEqual(item["condition"], "overflowing")
        self.assertEqual(item["priority_level"], "HIGH")

        app.advance_truck_dispatch_item(item["item_id"])
        app.advance_truck_dispatch_item(item["item_id"])
        app.advance_truck_dispatch_item(item["item_id"])
        pending = app.advance_truck_dispatch_item(item["item_id"])

        current_item = pending["batches"][0]["items"][0]
        self.assertEqual(current_item["status"], "API_PENDING_CONFIRMATION")

        closed = app.confirm_truck_dispatch_item_api(item["item_id"])
        closed_item = closed["batches"][0]["items"][0]
        self.assertEqual(closed_item["status"], "CLOSED")
        self.assertEqual(closed["open_item_count"], 0)

        history = app.list_dispatch_history()
        self.assertEqual(history["entries"][0]["truck_assignment"]["truck_number"], "TRUCK-7")
        self.assertTrue(history["entries"][0]["truck_assignment"]["is_closed"])

    def test_duplicate_open_assignment_is_skipped(self):
        entry_id = self._record_sample_dispatch_entry()
        app.assign_dispatch_entries_to_truck([entry_id], "TRUCK-1", "ROUTE-1")
        second_attempt = app.assign_dispatch_entries_to_truck([entry_id], "TRUCK-2", "ROUTE-2")

        self.assertEqual(second_attempt["assigned_entry_ids"], [])
        self.assertEqual(len(second_attempt["skipped"]), 1)
        self.assertIn("Already assigned", second_attempt["skipped"][0]["reason"])

    def test_delete_truck_dispatch_item_removes_empty_batch(self):
        entry_id = self._record_sample_dispatch_entry()
        assigned = app.assign_dispatch_entries_to_truck([entry_id], "TRUCK-8", "ROUTE-15")
        item_id = assigned["truck_dispatch"]["batches"][0]["items"][0]["item_id"]

        deleted = app.delete_truck_dispatch_item(item_id)
        remaining = app.list_truck_dispatch()

        self.assertIsNotNone(deleted)
        self.assertEqual(deleted["item_id"], item_id)
        self.assertEqual(remaining["open_item_count"], 0)
        self.assertEqual(len(remaining["batches"]), 0)

    def test_api_confirmation_accepts_base64_closure_image(self):
        entry_id = self._record_sample_dispatch_entry()
        assigned = app.assign_dispatch_entries_to_truck([entry_id], "TRUCK-3", "ROUTE-9")
        item_id = assigned["truck_dispatch"]["batches"][0]["items"][0]["item_id"]

        app.advance_truck_dispatch_item(item_id)
        app.advance_truck_dispatch_item(item_id)
        app.advance_truck_dispatch_item(item_id)
        app.advance_truck_dispatch_item(item_id)

        image_base64 = b64encode(b"fake esp32 image bytes").decode("ascii")
        response = self.client.post(
            f"/truck-dispatch/items/{item_id}/api-confirmation",
            json={
                "confirmation_status": "CONFIRMED",
                "image_base64": image_base64,
                "file_name": "esp32_capture.jpg",
                "mime_type": "image/jpeg",
            },
        )
        payload = response.get_json()

        self.assertEqual(response.status_code, 200)
        self.assertEqual(payload["truck_dispatch"]["open_item_count"], 0)
        api_image = payload["truck_dispatch"]["batches"][0]["items"][0]["api_image"]
        self.assertIsNotNone(api_image)
        self.assertEqual(api_image["provider_name"], "esp32-cam-base64")
        self.assertEqual(payload["truck_dispatch"]["batches"][0]["items"][0]["status"], "CLOSED")

    def test_delete_truck_dispatch_item_cleans_linked_api_image(self):
        entry_id = self._record_sample_dispatch_entry()
        assigned = app.assign_dispatch_entries_to_truck([entry_id], "TRUCK-4", "ROUTE-10")
        item_id = assigned["truck_dispatch"]["batches"][0]["items"][0]["item_id"]

        app.advance_truck_dispatch_item(item_id)
        app.advance_truck_dispatch_item(item_id)
        app.advance_truck_dispatch_item(item_id)
        app.advance_truck_dispatch_item(item_id)

        response = self.client.post(
            f"/truck-dispatch/items/{item_id}/api-confirmation",
            json={
                "confirmation_status": "CONFIRMED",
                "image_base64": b64encode(b"cleanup image bytes").decode("ascii"),
                "file_name": "cleanup.jpg",
                "mime_type": "image/jpeg",
            },
        )
        self.assertEqual(response.status_code, 200)
        image_id = response.get_json()["truck_dispatch"]["batches"][0]["items"][0]["api_image"]["image_id"]

        deleted = app.delete_truck_dispatch_item(item_id)
        self.assertIsNotNone(deleted)
        self.assertIsNone(inspection_pipeline.get_image_record(image_id, db_path=self.db_path))


if __name__ == "__main__":
    unittest.main()
