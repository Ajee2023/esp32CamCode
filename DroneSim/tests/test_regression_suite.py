import io
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import app
import inspection_pipeline
import no_fly_zones


class WorkflowRegressionTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "workflow_regression.db")
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

        with app.state_lock:
            app.bin_state["next_id"] = 1
            app.bin_state["bins"] = []
            app.bin_state["last_zone"] = None
            app.bin_state["last_summary"] = None
            app.zone_mission_state["status"] = "idle"
            app.zone_mission_state["message"] = "No zone survey has been started."
            app.zone_mission_state["completed_waypoints"] = 0
            app.zone_mission_state["total_waypoints"] = 0
            app.zone_mission_state["started_at"] = ""
            app.zone_mission_state["drone_id"] = app.DRONE_ID

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

    def test_bin_zone_and_no_fly_endpoints_stay_available(self):
        create_bin = self.client.post(
            "/bins",
            json={
                "bin_type": "blue",
                "lat": 43.7322,
                "lon": -79.7631,
                "address": "10 Demo Street",
                "label": "Blue Bin",
            },
        )
        self.assertEqual(create_bin.status_code, 200)
        created_bin = create_bin.get_json()["bin"]

        overflow = self.client.post(f"/bins/{created_bin['id']}/overflow", json={"is_overflowing": True})
        self.assertEqual(overflow.status_code, 200)
        self.assertTrue(overflow.get_json()["bin"]["is_overflowing"])

        zone_payload = {"south": 43.7320, "north": 43.7325, "west": -79.7635, "east": -79.7627}
        zone_summary = self.client.post("/zone-summary", json=zone_payload)
        self.assertEqual(zone_summary.status_code, 200)
        self.assertEqual(zone_summary.get_json()["summary"]["zone"], zone_payload)

        no_fly = self.client.post(
            "/no-fly-zones",
            json={
                "name": "School Block",
                "description": "Restricted airspace",
                "shape_type": "polygon",
                "geometry": {
                    "points": [
                        {"lat": 43.7318, "lon": -79.7634},
                        {"lat": 43.7327, "lon": -79.7634},
                        {"lat": 43.7327, "lon": -79.7628},
                        {"lat": 43.7318, "lon": -79.7628},
                    ]
                },
                "buffer_distance_meters": 20,
                "is_active": True,
                "created_by": "test",
            },
        )
        self.assertEqual(no_fly.status_code, 200)

    def test_demo_image_upload_refreshes_saved_flight_summary_entry(self):
        summary = {
            "zone": {"south": 43.7320, "north": 43.7325, "west": -79.7635, "east": -79.7627},
            "dispatch_message": "Dispatch summary ready.",
            "captured_at": "2026-07-22T12:00:00+00:00",
            "addresses": [
                {
                    "id": 7,
                    "bin_type": "black" if False else "garbage",
                    "label": "Black Bin",
                    "condition": "overflowing",
                    "priority": "HIGH",
                    "priority_score": 98,
                    "request_id": "BIN-7",
                    "lat": 43.7322,
                    "lon": -79.7630,
                    "address": "7 Demo Street",
                    "captured_at": "2026-07-22T11:59:00+00:00",
                    "drone_id": "DRONE-001",
                    "latest_image": None,
                }
            ],
        }
        app.record_dispatch_run(summary, mission_id=12, started_at="2026-07-22T11:55:00+00:00", drone_id="DRONE-001")
        history_before = app.list_dispatch_history()
        entry = history_before["entries"][0]
        self.assertIsNone(entry["latest_image"])

        with app.state_lock:
            app.bin_state["bins"] = [
                {
                    "id": 7,
                    "bin_type": "garbage",
                    "label": "Black Bin",
                    "condition": "overflowing",
                    "is_overflowing": True,
                    "priority_level": "HIGH",
                    "priority_score": 98,
                    "request_id": "BIN-7",
                    "lat": 43.7322,
                    "lon": -79.7630,
                    "address": "7 Demo Street",
                }
            ]
            app.bin_state["last_zone"] = dict(summary["zone"])
            app.bin_state["last_summary"] = dict(summary)

        response = self.client.post(
            f"/bins/7/demo-image",
            data={
                "entry_id": str(entry["entry_id"]),
                "image": (io.BytesIO(b"demo-image-bytes"), "demo.jpg"),
            },
            content_type="multipart/form-data",
        )

        self.assertEqual(response.status_code, 200)
        refreshed_history = app.list_dispatch_history()
        refreshed_entry = refreshed_history["entries"][0]
        self.assertIsNotNone(refreshed_entry["latest_image"])
        self.assertEqual(refreshed_entry["latest_image"]["provider_name"], "demo-upload-provider")
        self.assertEqual(refreshed_entry["review_status"], "DEMO_OVERRIDE")

    def test_clear_bins_does_not_reuse_old_bin_ids_after_demo_image_exists(self):
        with app.state_lock:
            app.bin_state["bins"] = [
                {
                    "id": 1,
                    "bin_type": "green",
                    "label": "Green Bin",
                    "condition": "standard",
                    "is_overflowing": False,
                    "priority_level": "NORMAL",
                    "priority_score": 20,
                    "request_id": "BIN-1",
                    "lat": 43.7322,
                    "lon": -79.7630,
                    "address": "1 Demo Street",
                }
            ]
            app.bin_state["next_id"] = 2

        upload_response = self.client.post(
            "/bins/1/demo-image",
            data={"image": (io.BytesIO(b"old-demo-image"), "old-demo.jpg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(upload_response.status_code, 200)

        clear_response = self.client.delete("/bins")
        self.assertEqual(clear_response.status_code, 200)

        create_response = self.client.post(
            "/bins",
            json={
                "bin_type": "blue",
                "lat": 43.7330,
                "lon": -79.7640,
                "address": "2 Fresh Street",
                "label": "Blue Bin",
            },
        )
        self.assertEqual(create_response.status_code, 200)
        new_bin = create_response.get_json()["bin"]
        self.assertGreater(new_bin["id"], 1)

    def test_truck_dispatch_workflow_closes_after_api_confirmation_with_image(self):
        summary = {
            "zone": {"south": 1.0, "north": 2.0, "west": 3.0, "east": 4.0},
            "dispatch_message": "Dispatch summary ready.",
            "captured_at": "2026-07-22T15:00:00+00:00",
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
                    "captured_at": "2026-07-22T15:00:01+00:00",
                    "drone_id": "DRONE-001",
                    "latest_image": None,
                }
            ],
        }
        app.record_dispatch_run(summary, mission_id=5, started_at="2026-07-22T14:58:00+00:00", drone_id="DRONE-001")
        entry_id = app.list_dispatch_history()["entries"][0]["entry_id"]

        assign_response = self.client.post(
            "/truck-dispatch/assign",
            json={"entry_ids": [entry_id], "truck_number": "TRUCK-7", "route_number": "ROUTE-12"},
        )
        self.assertEqual(assign_response.status_code, 200)
        item_id = assign_response.get_json()["truck_dispatch"]["batches"][0]["items"][0]["item_id"]

        for _ in range(4):
            advance_response = self.client.post(f"/truck-dispatch/items/{item_id}/advance")
            self.assertEqual(advance_response.status_code, 200)

        confirm_response = self.client.post(
            f"/truck-dispatch/items/{item_id}/api-confirmation",
            data={"image": (io.BytesIO(b"truck-closure"), "closure.jpg")},
            content_type="multipart/form-data",
        )
        self.assertEqual(confirm_response.status_code, 200)
        payload = confirm_response.get_json()
        item = payload["truck_dispatch"]["batches"][0]["items"][0]
        self.assertEqual(item["status"], "CLOSED")
        self.assertIsNotNone(item["api_image"])


class FrontendContractTests(unittest.TestCase):
    def test_address_modal_and_shared_demo_image_uploader_exist(self):
        template_path = os.path.join(os.path.dirname(__file__), "..", "templates", "index.html")
        with open(template_path, "r", encoding="utf-8") as handle:
            html = handle.read()

        self.assertIn('id="addressModal"', html)
        self.assertIn('function promptForAddress()', html)
        self.assertIn('function confirmAddressModal()', html)
        self.assertIn('id="globalDemoImageInput"', html)
        self.assertIn('function handleGlobalDemoImageSelected', html)
        self.assertIn('formData.append(\'entry_id\'', html)


if __name__ == "__main__":
    unittest.main()
