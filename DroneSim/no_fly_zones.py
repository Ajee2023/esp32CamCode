import json
import math
import os
import sqlite3
from datetime import datetime, timezone


DATABASE_PATH = os.path.join(os.path.dirname(__file__), "data", "drone_sim.db")
EARTH_METERS_PER_DEGREE = 111_320.0
DEFAULT_BUFFER_METERS = 20.0


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat()


def ensure_database():
    os.makedirs(os.path.dirname(DATABASE_PATH), exist_ok=True)
    with sqlite3.connect(DATABASE_PATH) as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS no_fly_zones (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                shape_type TEXT NOT NULL,
                geometry_json TEXT NOT NULL,
                buffer_distance_meters REAL NOT NULL DEFAULT 20,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_by TEXT DEFAULT 'operator',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        connection.commit()


def row_to_zone(row):
    if row is None:
        return None
    return {
        "id": row["id"],
        "name": row["name"],
        "description": row["description"],
        "shape_type": row["shape_type"],
        "geometry": json.loads(row["geometry_json"]),
        "buffer_distance_meters": float(row["buffer_distance_meters"]),
        "is_active": bool(row["is_active"]),
        "created_by": row["created_by"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_connection():
    ensure_database()
    connection = sqlite3.connect(DATABASE_PATH)
    connection.row_factory = sqlite3.Row
    return connection


def list_zones(include_inactive=True):
    with get_connection() as connection:
        if include_inactive:
            rows = connection.execute(
                "SELECT * FROM no_fly_zones ORDER BY id DESC"
            ).fetchall()
        else:
            rows = connection.execute(
                "SELECT * FROM no_fly_zones WHERE is_active = 1 ORDER BY id DESC"
            ).fetchall()
    return [row_to_zone(row) for row in rows]


def get_zone(zone_id):
    with get_connection() as connection:
        row = connection.execute(
            "SELECT * FROM no_fly_zones WHERE id = ?",
            (zone_id,),
        ).fetchone()
    return row_to_zone(row)


def create_zone(name, description, shape_type, geometry, buffer_distance_meters, created_by="operator", is_active=True):
    now = utc_now_iso()
    geometry_json = json.dumps(geometry)
    with get_connection() as connection:
        cursor = connection.execute(
            """
            INSERT INTO no_fly_zones (
                name, description, shape_type, geometry_json,
                buffer_distance_meters, is_active, created_by, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                name,
                description,
                shape_type,
                geometry_json,
                float(buffer_distance_meters),
                1 if is_active else 0,
                created_by,
                now,
                now,
            ),
        )
        connection.commit()
        zone_id = cursor.lastrowid
    return get_zone(zone_id)


def update_zone(zone_id, name, description, shape_type, geometry, buffer_distance_meters, is_active):
    now = utc_now_iso()
    with get_connection() as connection:
        connection.execute(
            """
            UPDATE no_fly_zones
            SET name = ?, description = ?, shape_type = ?, geometry_json = ?,
                buffer_distance_meters = ?, is_active = ?, updated_at = ?
            WHERE id = ?
            """,
            (
                name,
                description,
                shape_type,
                json.dumps(geometry),
                float(buffer_distance_meters),
                1 if is_active else 0,
                now,
                zone_id,
            ),
        )
        connection.commit()
    return get_zone(zone_id)


def set_zone_active(zone_id, is_active):
    zone = get_zone(zone_id)
    if not zone:
        return None
    return update_zone(
        zone_id,
        zone["name"],
        zone["description"],
        zone["shape_type"],
        zone["geometry"],
        zone["buffer_distance_meters"],
        is_active,
    )


def delete_zone(zone_id):
    with get_connection() as connection:
        connection.execute("DELETE FROM no_fly_zones WHERE id = ?", (zone_id,))
        connection.commit()


def meters_to_lat_degrees(meters):
    return meters / EARTH_METERS_PER_DEGREE


def meters_to_lon_degrees(meters, latitude):
    scale = EARTH_METERS_PER_DEGREE * max(0.1, math.cos(math.radians(latitude)))
    return meters / scale


def distance_meters(point_a, point_b):
    avg_lat = (point_a["lat"] + point_b["lat"]) / 2.0
    dx = (point_b["lon"] - point_a["lon"]) * EARTH_METERS_PER_DEGREE * max(0.1, math.cos(math.radians(avg_lat)))
    dy = (point_b["lat"] - point_a["lat"]) * EARTH_METERS_PER_DEGREE
    return math.hypot(dx, dy)


def orientation(a, b, c):
    value = (b["lon"] - a["lon"]) * (c["lat"] - a["lat"]) - (b["lat"] - a["lat"]) * (c["lon"] - a["lon"])
    if abs(value) < 1e-12:
        return 0
    return 1 if value > 0 else -1


def on_segment(a, b, c):
    return (
        min(a["lat"], c["lat"]) - 1e-12 <= b["lat"] <= max(a["lat"], c["lat"]) + 1e-12 and
        min(a["lon"], c["lon"]) - 1e-12 <= b["lon"] <= max(a["lon"], c["lon"]) + 1e-12
    )


def segments_intersect(a, b, c, d):
    o1 = orientation(a, b, c)
    o2 = orientation(a, b, d)
    o3 = orientation(c, d, a)
    o4 = orientation(c, d, b)

    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and on_segment(a, c, b):
        return True
    if o2 == 0 and on_segment(a, d, b):
        return True
    if o3 == 0 and on_segment(c, a, d):
        return True
    if o4 == 0 and on_segment(c, b, d):
        return True
    return False


def point_in_polygon(point, polygon_points):
    inside = False
    point_lon = point["lon"]
    point_lat = point["lat"]
    count = len(polygon_points)
    for index in range(count):
        a = polygon_points[index]
        b = polygon_points[(index + 1) % count]
        intersects = ((a["lat"] > point_lat) != (b["lat"] > point_lat)) and (
            point_lon < (b["lon"] - a["lon"]) * (point_lat - a["lat"]) / max(1e-12, (b["lat"] - a["lat"])) + a["lon"]
        )
        if intersects:
            inside = not inside
    return inside


def segment_distance_to_point_meters(start, end, point):
    avg_lat = (start["lat"] + end["lat"] + point["lat"]) / 3.0
    sx = start["lon"] * EARTH_METERS_PER_DEGREE * max(0.1, math.cos(math.radians(avg_lat)))
    sy = start["lat"] * EARTH_METERS_PER_DEGREE
    ex = end["lon"] * EARTH_METERS_PER_DEGREE * max(0.1, math.cos(math.radians(avg_lat)))
    ey = end["lat"] * EARTH_METERS_PER_DEGREE
    px = point["lon"] * EARTH_METERS_PER_DEGREE * max(0.1, math.cos(math.radians(avg_lat)))
    py = point["lat"] * EARTH_METERS_PER_DEGREE

    dx = ex - sx
    dy = ey - sy
    if dx == 0 and dy == 0:
        return math.hypot(px - sx, py - sy)

    projection = ((px - sx) * dx + (py - sy) * dy) / (dx * dx + dy * dy)
    projection = max(0.0, min(1.0, projection))
    closest_x = sx + projection * dx
    closest_y = sy + projection * dy
    return math.hypot(px - closest_x, py - closest_y)


def zone_vertices(zone):
    geometry = zone["geometry"]
    shape_type = zone["shape_type"]
    if shape_type == "rectangle":
        south = geometry["south"]
        north = geometry["north"]
        west = geometry["west"]
        east = geometry["east"]
        return [
            {"lat": south, "lon": west},
            {"lat": south, "lon": east},
            {"lat": north, "lon": east},
            {"lat": north, "lon": west},
        ]
    if shape_type == "polygon":
        return [{"lat": float(point["lat"]), "lon": float(point["lon"])} for point in geometry["points"]]
    if shape_type == "circle":
        center = {"lat": float(geometry["center"]["lat"]), "lon": float(geometry["center"]["lon"])}
        radius = float(geometry["radius_meters"])
        points = []
        for index in range(16):
            angle = (2 * math.pi * index) / 16
            lat_offset = meters_to_lat_degrees(math.sin(angle) * radius)
            lon_offset = meters_to_lon_degrees(math.cos(angle) * radius, center["lat"])
            points.append({"lat": center["lat"] + lat_offset, "lon": center["lon"] + lon_offset})
        return points
    raise ValueError(f"Unsupported shape type: {shape_type}")


def point_inside_zone(point, zone, include_buffer=False):
    buffer_meters = float(zone.get("buffer_distance_meters", DEFAULT_BUFFER_METERS)) if include_buffer else 0.0
    geometry = zone["geometry"]
    shape_type = zone["shape_type"]

    if shape_type == "rectangle":
        south = float(geometry["south"]) - meters_to_lat_degrees(buffer_meters)
        north = float(geometry["north"]) + meters_to_lat_degrees(buffer_meters)
        lon_padding = meters_to_lon_degrees(buffer_meters, point["lat"])
        west = float(geometry["west"]) - lon_padding
        east = float(geometry["east"]) + lon_padding
        return south <= point["lat"] <= north and west <= point["lon"] <= east

    if shape_type == "circle":
        center = {"lat": float(geometry["center"]["lat"]), "lon": float(geometry["center"]["lon"])}
        radius = float(geometry["radius_meters"]) + buffer_meters
        return distance_meters(point, center) <= radius

    if shape_type == "polygon":
        vertices = zone_vertices(zone)
        if point_in_polygon(point, vertices):
            return True
        if not include_buffer or buffer_meters <= 0:
            return False
        for index, start in enumerate(vertices):
            end = vertices[(index + 1) % len(vertices)]
            if segment_distance_to_point_meters(start, end, point) <= buffer_meters:
                return True
        return False

    raise ValueError(f"Unsupported shape type: {shape_type}")


def route_intersects_zone(start, end, zone, include_buffer=False):
    if point_inside_zone(start, zone, include_buffer=include_buffer):
        return True
    if point_inside_zone(end, zone, include_buffer=include_buffer):
        return True

    if zone["shape_type"] == "circle":
        center = {"lat": float(zone["geometry"]["center"]["lat"]), "lon": float(zone["geometry"]["center"]["lon"])}
        radius = float(zone["geometry"]["radius_meters"])
        if include_buffer:
            radius += float(zone.get("buffer_distance_meters", DEFAULT_BUFFER_METERS))
        return segment_distance_to_point_meters(start, end, center) <= radius

    vertices = zone_vertices(zone)
    for index, vertex in enumerate(vertices):
        next_vertex = vertices[(index + 1) % len(vertices)]
        if segments_intersect(start, end, vertex, next_vertex):
            return True
    return False


def route_near_zone(start, end, zone):
    if route_intersects_zone(start, end, zone, include_buffer=True):
        return not route_intersects_zone(start, end, zone, include_buffer=False)
    return False


def zone_bounds(zone):
    vertices = zone_vertices(zone)
    return {
        "south": min(point["lat"] for point in vertices),
        "north": max(point["lat"] for point in vertices),
        "west": min(point["lon"] for point in vertices),
        "east": max(point["lon"] for point in vertices),
    }


def segment_safe(start, end, active_zones):
    for zone in active_zones:
        if route_intersects_zone(start, end, zone, include_buffer=False):
            return False
    return True


def route_warning_messages(route_points, active_zones):
    warnings = []
    for start, end in zip(route_points, route_points[1:]):
        for zone in active_zones:
            if route_near_zone(start, end, zone):
                warnings.append(
                    f"Caution: the planned route passes close to an active no-fly zone ({zone['name']})."
                )
    return warnings


def build_detour_candidates(start, end, zone):
    bounds = zone_bounds(zone)
    latitude = (start["lat"] + end["lat"]) / 2.0
    margin = max(DEFAULT_BUFFER_METERS, float(zone.get("buffer_distance_meters", DEFAULT_BUFFER_METERS))) + 18.0
    lat_margin = meters_to_lat_degrees(margin)
    lon_margin = meters_to_lon_degrees(margin, latitude)

    return [
        {"lat": bounds["south"] - lat_margin, "lon": bounds["west"] - lon_margin},
        {"lat": bounds["south"] - lat_margin, "lon": bounds["east"] + lon_margin},
        {"lat": bounds["north"] + lat_margin, "lon": bounds["west"] - lon_margin},
        {"lat": bounds["north"] + lat_margin, "lon": bounds["east"] + lon_margin},
        {"lat": bounds["south"] - lat_margin, "lon": (bounds["west"] + bounds["east"]) / 2.0},
        {"lat": bounds["north"] + lat_margin, "lon": (bounds["west"] + bounds["east"]) / 2.0},
        {"lat": (bounds["south"] + bounds["north"]) / 2.0, "lon": bounds["west"] - lon_margin},
        {"lat": (bounds["south"] + bounds["north"]) / 2.0, "lon": bounds["east"] + lon_margin},
    ]


def try_alternate_route(start, end, active_zones):
    blocking_zones = [zone for zone in active_zones if route_intersects_zone(start, end, zone, include_buffer=False)]
    for zone in blocking_zones:
        for candidate in build_detour_candidates(start, end, zone):
            route_points = [start, candidate, end]
            if all(segment_safe(a, b, active_zones) for a, b in zip(route_points, route_points[1:])):
                return route_points
    return None


def validate_route_plan(start, destination, return_destination=None, active_zones=None):
    zones = active_zones if active_zones is not None else list_zones(include_inactive=False)
    zones = [zone for zone in zones if zone.get("is_active", True)]

    for zone in zones:
        if point_inside_zone(destination, zone, include_buffer=False):
            return {
                "allowed": False,
                "error": "Dispatch blocked: the destination is inside an active no-fly zone.",
                "route_points": [start, destination],
                "warnings": [],
            }
        if point_inside_zone(start, zone, include_buffer=False):
            return {
                "allowed": False,
                "error": "Dispatch blocked: the drone is starting inside an active no-fly zone.",
                "route_points": [start, destination],
                "warnings": [],
            }

    route_points = [start, destination]
    warnings = route_warning_messages(route_points, zones)
    rerouted = False

    if not segment_safe(start, destination, zones):
        alternate = try_alternate_route(start, destination, zones)
        if not alternate:
            return {
                "allowed": False,
                "error": "Dispatch blocked: no safe route is available around the restricted area.",
                "route_points": route_points,
                "warnings": [],
            }
        route_points = alternate
        rerouted = True
        warnings = route_warning_messages(route_points, zones)

    if return_destination:
        for start_point, end_point in zip([return_destination] + route_points[-1:], [start]):
            if not segment_safe(start_point, end_point, zones):
                alternate_return = try_alternate_route(start_point, end_point, zones)
                if not alternate_return:
                    return {
                        "allowed": False,
                        "error": "Dispatch blocked: no safe return route is available around the restricted area.",
                        "route_points": route_points,
                        "warnings": warnings,
                    }

    return {
        "allowed": True,
        "route_points": route_points,
        "warnings": warnings,
        "rerouted": rerouted,
        "message": "Route recalculated to avoid restricted airspace." if rerouted else None,
    }


def validate_zone_payload(payload):
    shape_type = str(payload.get("shape_type") or "").strip().lower()
    if shape_type not in {"polygon", "rectangle", "circle"}:
        raise ValueError("Shape type must be polygon, rectangle, or circle.")

    geometry = payload.get("geometry") or {}
    if shape_type == "rectangle":
        south = float(geometry.get("south"))
        north = float(geometry.get("north"))
        west = float(geometry.get("west"))
        east = float(geometry.get("east"))
        if south > north or west > east:
            raise ValueError("Rectangle bounds are invalid.")
        normalized_geometry = {"south": south, "north": north, "west": west, "east": east}
    elif shape_type == "circle":
        center = geometry.get("center") or {}
        radius_meters = float(geometry.get("radius_meters"))
        if radius_meters <= 0:
            raise ValueError("Circle radius must be greater than zero.")
        normalized_geometry = {
            "center": {"lat": float(center.get("lat")), "lon": float(center.get("lon"))},
            "radius_meters": radius_meters,
        }
    else:
        points = geometry.get("points") or []
        if len(points) < 3:
            raise ValueError("Polygon zones require at least three points.")
        normalized_geometry = {
            "points": [{"lat": float(point["lat"]), "lon": float(point["lon"])} for point in points]
        }

    return {
        "name": str(payload.get("name") or "").strip() or "No-Fly Zone",
        "description": str(payload.get("description") or "").strip(),
        "shape_type": shape_type,
        "geometry": normalized_geometry,
        "buffer_distance_meters": float(payload.get("buffer_distance_meters", DEFAULT_BUFFER_METERS)),
        "is_active": bool(payload.get("is_active", True)),
        "created_by": str(payload.get("created_by") or "operator").strip() or "operator",
    }
