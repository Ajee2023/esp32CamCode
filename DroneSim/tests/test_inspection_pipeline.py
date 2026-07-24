import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from inspection_pipeline import (
    CAPTURE_STATUS_COMPLETED,
    CAPTURE_STATUS_MANUAL_REVIEW,
    CaptureConfig,
    PropertyImageCaptureProvider,
    SurveyInspectionImageService,
    get_image_record,
    set_manual_review,
)


class FailingProvider(PropertyImageCaptureProvider):
    name = "failing-provider"

    def capture_property_image(self, bin_item, survey_context, attempt=0):
        raise RuntimeError("Provider unavailable")


class InspectionPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.mkdtemp(prefix="drone-inspection-")
        self.db_path = os.path.join(self.temp_dir, "inspection.db")
        self.service = SurveyInspectionImageService(
            CaptureConfig(
                storage_root=self.temp_dir,
                db_path=self.db_path,
                max_retry_count=1,
                retry_interval_seconds=0.0,
            )
        )
        self.bin_item = {
            "id": 101,
            "bin_type": "garbage",
            "label": "Black Bin",
            "is_overflowing": True,
            "condition": "overflowing",
            "lat": 43.7320,
            "lon": -79.7620,
            "address": "123 Public School Lane",
            "request_id": "REQ-101",
            "location_importance": "school",
            "inspection_confidence": 0.92,
            "loose_garbage_detected": True,
        }
        self.context = {
            "zone": {"south": 43.7310, "north": 43.7330, "west": -79.7630, "east": -79.7610},
            "waypoint": {"lat": 43.7320, "lon": -79.7620},
            "mission_id": 7,
        }

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_capture_needed_only_when_waypoint_is_near_bin(self):
        self.assertTrue(self.service.capture_needed_for_bin(self.bin_item, {"lat": 43.7320, "lon": -79.7620}))
        self.assertFalse(self.service.capture_needed_for_bin(self.bin_item, {"lat": 43.7350, "lon": -79.7700}))

    def test_capture_bin_evidence_stores_processed_image_only_by_default(self):
        result = self.service.capture_bin_evidence(dict(self.bin_item), dict(self.context))
        self.assertEqual(result["capture_status"], CAPTURE_STATUS_COMPLETED)
        self.assertTrue(os.path.exists(result["processed_image_path"]))
        self.assertIsNone(result["original_image_path"])
        self.assertTrue(result["faces_detected"])
        self.assertTrue(result["people_detected"])
        self.assertTrue(result["license_plates_detected"] or result["faces_detected"])

    def test_moving_vehicle_retry_uses_fallback_and_keeps_detection_flag(self):
        moving_vehicle_bin = dict(self.bin_item)
        moving_vehicle_bin["id"] = 102
        moving_vehicle_bin["request_id"] = "REQ-102"
        moving_vehicle_bin["simulated_moving_vehicle_attempts"] = 4
        result = self.service.capture_bin_evidence(moving_vehicle_bin, dict(self.context))
        self.assertEqual(result["capture_status"], CAPTURE_STATUS_MANUAL_REVIEW)
        self.assertTrue(result["moving_vehicle_detected"])
        self.assertTrue(result["manual_review_required"])
        self.assertTrue(os.path.exists(result["processed_image_path"]))

    def test_manual_review_toggle_updates_record(self):
        result = self.service.capture_bin_evidence(dict(self.bin_item), dict(self.context))
        cleared = set_manual_review(result["image_id"], False, db_path=self.db_path)
        self.assertEqual(cleared["capture_status"], CAPTURE_STATUS_COMPLETED)
        self.assertFalse(cleared["manual_review_required"])
        refreshed = get_image_record(result["image_id"], db_path=self.db_path)
        self.assertEqual(refreshed["review_status"], "AUTO_APPROVED")

    def test_provider_failure_falls_back_to_placeholder(self):
        service = SurveyInspectionImageService(
            CaptureConfig(
                storage_root=self.temp_dir,
                db_path=os.path.join(self.temp_dir, "fallback.db"),
                max_retry_count=0,
                retry_interval_seconds=0.0,
                placeholder_on_failure=True,
            ),
            provider=FailingProvider(),
        )
        result = service.capture_bin_evidence(dict(self.bin_item), dict(self.context))
        self.assertIn("processed", result["processed_image_path"])
        self.assertEqual(result["provider_name"], "placeholder-provider")

    def test_demo_override_image_creates_completed_record(self):
        png_bytes = (
            b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
            b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc`\x00\x01"
            b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
        )
        result = self.service.attach_demo_override_image(
            dict(self.bin_item),
            file_name="frontage.png",
            file_bytes=png_bytes,
            mime_type="image/png",
        )
        self.assertEqual(result["capture_status"], CAPTURE_STATUS_COMPLETED)
        self.assertEqual(result["provider_name"], "demo-upload-provider")
        self.assertTrue(os.path.exists(result["processed_image_path"]))


if __name__ == "__main__":
    unittest.main()
