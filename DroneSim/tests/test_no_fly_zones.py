import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from no_fly_zones import route_intersects_zone, validate_route_plan


class NoFlyZoneValidationTests(unittest.TestCase):
    def setUp(self):
        self.rectangle_zone = {
            "id": 1,
            "name": "School Airspace",
            "shape_type": "rectangle",
            "geometry": {
                "south": 43.7310,
                "north": 43.7330,
                "west": -79.7615,
                "east": -79.7590,
            },
            "buffer_distance_meters": 20,
            "is_active": True,
        }
        self.polygon_zone = {
            "id": 2,
            "name": "Polygon Block",
            "shape_type": "polygon",
            "geometry": {
                "points": [
                    {"lat": 43.7310, "lon": -79.7610},
                    {"lat": 43.7325, "lon": -79.7600},
                    {"lat": 43.7315, "lon": -79.7585},
                    {"lat": 43.7305, "lon": -79.7598},
                ]
            },
            "buffer_distance_meters": 25,
            "is_active": True,
        }
        self.circle_zone = {
            "id": 3,
            "name": "Helipad Circle",
            "shape_type": "circle",
            "geometry": {
                "center": {"lat": 43.7319, "lon": -79.7601},
                "radius_meters": 90,
            },
            "buffer_distance_meters": 15,
            "is_active": True,
        }

    def test_route_does_not_intersect_polygon(self):
        start = {"lat": 43.7340, "lon": -79.7640}
        end = {"lat": 43.7350, "lon": -79.7630}
        self.assertFalse(route_intersects_zone(start, end, self.polygon_zone))

    def test_route_intersects_polygon(self):
        start = {"lat": 43.7310, "lon": -79.7620}
        end = {"lat": 43.7314, "lon": -79.7580}
        self.assertTrue(route_intersects_zone(start, end, self.polygon_zone))

    def test_destination_inside_zone_blocks_dispatch(self):
        start = {"lat": 43.7340, "lon": -79.7640}
        end = {"lat": 43.7318, "lon": -79.7602}
        result = validate_route_plan(start, end, active_zones=[self.circle_zone])
        self.assertFalse(result["allowed"])
        self.assertIn("destination is inside", result["error"])

    def test_start_inside_zone_blocks_dispatch(self):
        start = {"lat": 43.73195, "lon": -79.7601}
        end = {"lat": 43.7340, "lon": -79.7640}
        result = validate_route_plan(start, end, active_zones=[self.circle_zone])
        self.assertFalse(result["allowed"])
        self.assertIn("starting inside", result["error"])

    def test_multiple_overlapping_zones_can_still_reroute(self):
        start = {"lat": 43.7308, "lon": -79.7632}
        end = {"lat": 43.7338, "lon": -79.7582}
        result = validate_route_plan(start, end, active_zones=[self.rectangle_zone, self.polygon_zone])
        self.assertTrue(result["allowed"])
        self.assertTrue(result["rerouted"])
        self.assertGreaterEqual(len(result["route_points"]), 3)

    def test_alternate_route_exists(self):
        start = {"lat": 43.7308, "lon": -79.7628}
        end = {"lat": 43.7334, "lon": -79.7584}
        result = validate_route_plan(start, end, active_zones=[self.rectangle_zone])
        self.assertTrue(result["allowed"])
        self.assertTrue(result["rerouted"])
        self.assertEqual(result["message"], "Route recalculated to avoid restricted airspace.")

    def test_alternate_route_does_not_exist(self):
        start = {"lat": 43.7317, "lon": -79.7645}
        end = {"lat": 43.7317, "lon": -79.7570}
        huge_zone = {
            "id": 99,
            "name": "Massive Restriction",
            "shape_type": "rectangle",
            "geometry": {
                "south": 43.7290,
                "north": 43.7350,
                "west": -79.7638,
                "east": -79.7574,
            },
            "buffer_distance_meters": 80,
            "is_active": True,
        }
        result = validate_route_plan(start, end, active_zones=[huge_zone])
        self.assertFalse(result["allowed"])
        self.assertIn("no safe route is available", result["error"])

    def test_inactive_zones_are_ignored(self):
        start = {"lat": 43.7308, "lon": -79.7628}
        end = {"lat": 43.7334, "lon": -79.7584}
        inactive_zone = dict(self.rectangle_zone)
        inactive_zone["is_active"] = False
        result = validate_route_plan(start, end, active_zones=[])
        self.assertTrue(result["allowed"])
        self.assertFalse(result["rerouted"])


if __name__ == "__main__":
    unittest.main()
