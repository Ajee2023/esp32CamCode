import math
import threading
import time

from flask import Flask, jsonify, render_template, request

from drone_control import (
    flight_progress,
    fly_to,
    get_multirotor_state,
    get_position,
    get_speed_multiplier,
    set_sim_weather,
    set_speed_multiplier,
    takeoff,
)
from no_fly_zones import (
    create_zone,
    delete_zone,
    ensure_database,
    get_zone,
    list_zones,
    set_zone_active,
    update_zone,
    validate_route_plan,
    validate_zone_payload,
)
from priority_service import calculate_priority, prioritize_dispatch_items, utc_now_iso

app = Flask(__name__)

state_lock = threading.Lock()

HOME_POINT = {"lat": 43.7315, "lon": -79.7624}
DRONE_ID = "DRONE-001"

nav_state = {"status": "idle", "message": ""}

zone_mission_state = {
    "status": "idle",
    "message": "No zone survey has been started.",
    "completed_waypoints": 0,
    "total_waypoints": 0,
}

bin_state = {
    "next_id": 1,
    "bins": [],
    "last_zone": None,
    "last_summary": None,
}

weather_state = {"rain": 0, "wind": 0, "snow": 0}
demo_state = {"speed_multiplier": get_speed_multiplier()}

feedback_state = {
    "cycle": 0,
    "stage": "repeat",
    "safe": True,
    "reason": "Awaiting weather feedback loop.",
    "last_reroute": None,
    "reroute_count": 0,
    "active_route": [],
    "sample_input": {
        "origin": dict(HOME_POINT),
        "destination": {"lat": 43.7365, "lon": -79.7545},
        "weather": dict(weather_state),
    },
}

flight_plan_state = {"route_points": [], "warnings": [], "message": ""}
mission_state = {"active_id": 0}


def set_nav_state(status=None, message=None):
    with state_lock:
        if status is not None:
            nav_state["status"] = status
        if message is not None:
            nav_state["message"] = message


def next_mission_id():
    with state_lock:
        mission_state["active_id"] += 1
        return mission_state["active_id"]


def get_active_mission_id():
    with state_lock:
        return mission_state["active_id"]


def is_active_mission(mission_id):
    with state_lock:
        return mission_state["active_id"] == mission_id


def reset_progress_state():
    flight_progress["distance_to_goal"] = 0
    flight_progress["progress_percent"] = 0
    flight_progress["target_lat"] = None
    flight_progress["target_lon"] = None
    update_route([])
    update_flight_plan(route_points=[], warnings=[], message="")


def get_nav_status():
    with state_lock:
        return nav_state["status"]


def update_flight_plan(route_points=None, warnings=None, message=None):
    with state_lock:
        if route_points is not None:
            flight_plan_state["route_points"] = [dict(point) for point in route_points]
        if warnings is not None:
            flight_plan_state["warnings"] = list(warnings)
        if message is not None:
            flight_plan_state["message"] = message


def get_flight_plan_snapshot():
    with state_lock:
        return {
            "route_points": [dict(point) for point in flight_plan_state["route_points"]],
            "warnings": list(flight_plan_state["warnings"]),
            "message": flight_plan_state["message"],
        }


def get_bins_snapshot():
    with state_lock:
        return [dict(item) for item in bin_state["bins"]]


def get_dispatch_snapshot():
    with state_lock:
        return {
            "bins": [dict(item) for item in bin_state["bins"]],
            "last_zone": dict(bin_state["last_zone"]) if bin_state["last_zone"] else None,
            "last_summary": dict(bin_state["last_summary"]) if bin_state["last_summary"] else None,
            "mission": dict(zone_mission_state),
        }


def find_bin_by_id(bin_id):
    for item in bin_state["bins"]:
        if item["id"] == bin_id:
            return item
    return None


def update_bin_condition_fields(bin_item):
    is_overflowing = bool(bin_item.get("is_overflowing", False))
    bin_item["condition"] = "overflowing" if is_overflowing else "standard"
    bin_item.setdefault("last_pickup_at", "")
    bin_item.setdefault("request_created_at", utc_now_iso())
    bin_item.setdefault("location_importance", "")
    bin_item.setdefault("inspection_confidence", 0.9)
    bin_item.setdefault("loose_garbage_detected", False)
    bin_item.setdefault("has_open_request", False)
    bin_item.setdefault("request_id", f"BIN-{bin_item.get('id', 'UNKNOWN')}")
    bin_item.setdefault("open_request_id", None)
    bin_item.update(calculate_priority(bin_item))
    return bin_item


def point_in_zone(bin_item, zone):
    return zone["south"] <= bin_item["lat"] <= zone["north"] and zone["west"] <= bin_item["lon"] <= zone["east"]


def zone_center(zone):
    return {
        "lat": (zone["south"] + zone["north"]) / 2.0,
        "lon": (zone["west"] + zone["east"]) / 2.0,
    }


def build_pending_zone_summary(zone, message=None):
    return {
        "zone": zone,
        "total_bins": 0,
        "counts": {"green": 0, "blue": 0, "garbage": 0},
        "addresses": [],
        "dispatch_message": message or "Zone selected. Fly the drone across the full zone to collect dispatch details.",
        "survey_complete": False,
    }


def store_zone_summary(summary):
    with state_lock:
        bin_state["last_zone"] = dict(summary["zone"])
        bin_state["last_summary"] = dict(summary)


def set_zone_mission_state(status=None, message=None, completed_waypoints=None, total_waypoints=None):
    with state_lock:
        if status is not None:
            zone_mission_state["status"] = status
        if message is not None:
            zone_mission_state["message"] = message
        if completed_waypoints is not None:
            zone_mission_state["completed_waypoints"] = completed_waypoints
        if total_waypoints is not None:
            zone_mission_state["total_waypoints"] = total_waypoints


def summarize_zone(zone, survey_complete=True):
    bins_in_zone = [item for item in get_bins_snapshot() if point_in_zone(item, zone)]
    prioritized_bins = prioritize_dispatch_items(bins_in_zone)
    counts = {"green": 0, "blue": 0, "garbage": 0}
    addresses = []

    for item in prioritized_bins:
        counts[item["bin_type"]] = counts.get(item["bin_type"], 0) + 1
        addresses.append(
            {
                "id": item["id"],
                "bin_type": item["bin_type"],
                "label": item.get("label") or item["bin_type"].title(),
                "condition": item.get("condition", "standard"),
                "priority": item.get("priority_level", "NORMAL"),
                "priority_score": item.get("priority_score", 30),
                "request_id": item.get("request_id"),
                "open_request_id": item.get("open_request_id"),
                "has_open_request": bool(item.get("has_open_request", False)),
                "duplicate_open_request": bool(item.get("duplicate_open_request", False)),
                "location_importance": item.get("location_importance", "residential"),
                "inspection_confidence": item.get("inspection_confidence", 0.9),
                "loose_garbage_detected": bool(item.get("loose_garbage_detected", False)),
                "priority_factors": item.get("priority_factors", {}),
                "request_created_at": item.get("request_created_at", ""),
                "last_pickup_at": item.get("last_pickup_at", ""),
                "lat": item["lat"],
                "lon": item["lon"],
                "address": item.get("address") or "Address pending",
            }
        )

    summary = {
        "zone": zone,
        "total_bins": len(prioritized_bins),
        "counts": counts,
        "addresses": addresses,
        "dispatch_message": (
            f"Dispatch summary ready for {len(prioritized_bins)} bin(s) in the selected zone."
            if prioritized_bins
            else "No waste bins found inside the selected zone."
        ),
        "survey_complete": survey_complete,
        "priority_formula": (
            "Base priority starts at NORMAL for standard bins and HIGH for overflowing bins, "
            "then adjusts for bin category, time since last pickup, request age, location importance, "
            "existing unresolved request, inspection confidence, and loose garbage detection."
        ),
    }
    store_zone_summary(summary)
    return summary


def set_feedback_stage(stage, **updates):
    with state_lock:
        feedback_state["stage"] = stage
        for key, value in updates.items():
            feedback_state[key] = value


def get_weather_snapshot():
    with state_lock:
        return dict(weather_state)


def get_demo_snapshot():
    with state_lock:
        return dict(demo_state)


def map_drone_status(status):
    mapping = {
        "idle": "IDLE",
        "flying": "EN_ROUTE",
        "surveying": "INSPECTING",
        "arrived": "IDLE",
        "taking_off": "EN_ROUTE",
        "error": "ERROR",
    }
    return mapping.get(str(status or "").lower(), "IDLE")


def get_current_assignment_id():
    status = get_nav_status()
    if status == "surveying":
        return "ZONE-SURVEY"
    target_lat = flight_progress.get("target_lat")
    target_lon = flight_progress.get("target_lon")
    if status == "flying" and target_lat is not None and target_lon is not None:
        return f"NAV-{target_lat:.4f}-{target_lon:.4f}"
    if status == "taking_off":
        return "TAKEOFF"
    return None


def get_battery_level():
    try:
        state = get_multirotor_state()
    except Exception:
        return None

    for attr in ("battery_remaining", "battery_level"):
        value = getattr(state, attr, None)
        if value is None:
            continue
        try:
            value = float(value)
        except (TypeError, ValueError):
            continue
        if 0.0 <= value <= 1.0:
            return round(value * 100, 1)
        if 0.0 <= value <= 100.0:
            return round(value, 1)
    return None


def build_drone_snapshot():
    with state_lock:
        raw_status = nav_state["status"]
        message = nav_state["message"]
    return {
        "id": DRONE_ID,
        "status": raw_status,
        "status_label": map_drone_status(raw_status),
        "assignment_id": get_current_assignment_id(),
        "battery_level": get_battery_level(),
        "message": message,
    }


def apply_weather_state(rain, wind, snow):
    applied = set_sim_weather(rain, wind, snow)
    with state_lock:
        weather_state.update(applied)
    return applied


def apply_speed_state(multiplier):
    applied = set_speed_multiplier(multiplier)
    with state_lock:
        demo_state["speed_multiplier"] = applied["multiplier"]
    return applied


def update_route(points):
    with state_lock:
        feedback_state["active_route"] = [dict(point) for point in points]


def current_route_start():
    try:
        position = get_position()
        return {"lat": position["lat"], "lon": position["lon"]}
    except Exception:
        return dict(HOME_POINT)


def validate_zone_survey_plan(zone):
    current_point = current_route_start()
    center = zone_center(zone)
    first_leg = prepare_route_validation(current_point, center)
    if not first_leg["allowed"]:
        return first_leg

    survey_waypoints = generate_zone_scan_waypoints(zone)
    if not survey_waypoints:
        return first_leg

    cursor = current_point
    collected_route = [dict(current_point)]
    collected_warnings = list(first_leg.get("warnings", []))
    rerouted = bool(first_leg.get("rerouted"))

    for waypoint in survey_waypoints:
        leg = validate_route_plan(cursor, waypoint, return_destination=HOME_POINT)
        if not leg["allowed"]:
            return {
                "allowed": False,
                "error": "Dispatch blocked: the selected zone overlaps restricted airspace and cannot be surveyed safely.",
                "details": leg["error"],
                "route_points": collected_route,
                "warnings": collected_warnings,
            }
        rerouted = rerouted or bool(leg.get("rerouted"))
        collected_warnings.extend(leg.get("warnings", []))
        for point in leg["route_points"][1:]:
            collected_route.append(dict(point))
        cursor = {"lat": waypoint["lat"], "lon": waypoint["lon"]}

    return {
        "allowed": True,
        "route_points": first_leg["route_points"],
        "warnings": list(dict.fromkeys(collected_warnings)),
        "rerouted": rerouted,
        "message": first_leg.get("message"),
    }


def generate_zone_scan_waypoints(zone):
    south = zone["south"]
    north = zone["north"]
    west = zone["west"]
    east = zone["east"]
    lat_span = max(0.0, north - south)
    lon_span = max(0.0, east - west)
    if lat_span == 0.0 and lon_span == 0.0:
        return [zone_center(zone)]

    horizontal_scan = lon_span >= lat_span
    lane_spacing = 0.00012
    waypoints = []
    if horizontal_scan:
        lane_count = max(2, int(math.ceil(lat_span / lane_spacing)) + 1)
        for index in range(lane_count):
            ratio = index / (lane_count - 1) if lane_count > 1 else 0.5
            lat = south + lat_span * ratio
            if index % 2 == 0:
                waypoints.append({"lat": lat, "lon": west})
                waypoints.append({"lat": lat, "lon": east})
            else:
                waypoints.append({"lat": lat, "lon": east})
                waypoints.append({"lat": lat, "lon": west})
    else:
        lane_count = max(2, int(math.ceil(lon_span / lane_spacing)) + 1)
        for index in range(lane_count):
            ratio = index / (lane_count - 1) if lane_count > 1 else 0.5
            lon = west + lon_span * ratio
            if index % 2 == 0:
                waypoints.append({"lat": south, "lon": lon})
                waypoints.append({"lat": north, "lon": lon})
            else:
                waypoints.append({"lat": north, "lon": lon})
                waypoints.append({"lat": south, "lon": lon})
    return waypoints


def build_weather_reroute(current_pos, destination, weather):
    dx = destination["lon"] - current_pos["lon"]
    dy = destination["lat"] - current_pos["lat"]
    length = math.hypot(dx, dy)
    if length == 0:
        return {"lat": destination["lat"], "lon": destination["lon"]}

    severity = max(weather["rain"], weather["wind"], weather["snow"])
    offset_scale = 0.0025 * (severity / 100.0)
    perp_x = -dy / length
    perp_y = dx / length
    midpoint_lat = current_pos["lat"] + (destination["lat"] - current_pos["lat"]) * 0.55
    midpoint_lon = current_pos["lon"] + (destination["lon"] - current_pos["lon"]) * 0.55
    return {
        "lat": midpoint_lat + perp_y * offset_scale,
        "lon": midpoint_lon + perp_x * offset_scale,
    }


def weather_feedback_hook(context):
    with state_lock:
        feedback_state["cycle"] += 1
        cycle = feedback_state["cycle"]

    set_feedback_stage("sense", reason="Reading user-set weather and current drone position.")
    weather = get_weather_snapshot()
    current_position = context["position"]
    destination = context["destination"]
    time.sleep(0.15)

    set_feedback_stage("analyze", weather=weather, reason="Checking whether rain, wind, or snow exceeds 50%.")
    unsafe = any(value > 50 for value in weather.values())
    time.sleep(0.15)

    if unsafe:
        reroute = build_weather_reroute(current_position, destination, weather)
        route_points = [
            {"lat": current_position["lat"], "lon": current_position["lon"]},
            reroute,
            {"lat": destination["lat"], "lon": destination["lon"]},
        ]
        update_route(route_points)
        with state_lock:
            feedback_state["reroute_count"] += 1
            reroute_count = feedback_state["reroute_count"]
        set_feedback_stage(
            "act",
            safe=False,
            weather=weather,
            last_reroute=reroute,
            reroute_count=reroute_count,
            reason="Conditions are not safe for flight",
        )
        result = {"safe": False, "message": "Conditions are not safe for flight", "reroute": reroute}
    else:
        route_points = [
            {"lat": current_position["lat"], "lon": current_position["lon"]},
            {"lat": destination["lat"], "lon": destination["lon"]},
        ]
        update_route(route_points)
        set_feedback_stage(
            "act",
            safe=True,
            weather=weather,
            reason="Weather is within safe limits. Continue on current path.",
        )
        result = {"safe": True, "message": "Weather safe. Continue on current path.", "clear_weather_route": True}

    time.sleep(0.15)
    set_feedback_stage(
        "repeat",
        weather=weather,
        reason=f"Feedback loop cycle {cycle} complete. Waiting for next user/weather update.",
    )
    return result


def prepare_route_validation(start_point, destination_point):
    validation = validate_route_plan(start_point, destination_point, return_destination=HOME_POINT)
    update_flight_plan(
        route_points=validation.get("route_points", [start_point, destination_point]),
        warnings=validation.get("warnings", []),
        message=validation.get("message") or validation.get("error") or "",
    )
    return validation


def revalidate_active_route():
    status = get_nav_status()
    if status not in {"flying", "surveying"}:
        return
    plan = get_flight_plan_snapshot()
    route_points = plan["route_points"]
    if len(route_points) < 2:
        return
    validation = prepare_route_validation(route_points[0], route_points[-1])
    if validation["allowed"]:
        update_route(validation["route_points"])
        if validation.get("message"):
            set_nav_state(status, validation["message"])
        elif validation.get("warnings"):
            set_nav_state(status, validation["warnings"][0])
    else:
        reset_progress_state()
        set_nav_state("error", validation["error"])


def execute_route(route_points, transit_status, completion_message, mission_id):
    if len(route_points) < 2:
        raise ValueError("A route requires at least two points.")

    update_route(route_points)
    update_flight_plan(route_points=route_points)
    with state_lock:
        feedback_state["cycle"] = 0
        feedback_state["stage"] = "sense"
        feedback_state["safe"] = True
        feedback_state["reason"] = "Feedback loop armed for this route."
        feedback_state["last_reroute"] = None
        feedback_state["reroute_count"] = 0
        feedback_state["sample_input"] = {
            "origin": {"lat": route_points[0]["lat"], "lon": route_points[0]["lon"]},
            "destination": {"lat": route_points[-1]["lat"], "lon": route_points[-1]["lon"]},
            "weather": dict(weather_state),
        }

    for index, waypoint in enumerate(route_points[1:], start=1):
        if not is_active_mission(mission_id):
            return
        set_nav_state(
            transit_status,
            f"{transit_status.title()} via waypoint {index}/{len(route_points) - 1} at {waypoint['lat']:.5f}, {waypoint['lon']:.5f}",
        )
        with state_lock:
            feedback_state["sample_input"]["destination"] = {"lat": waypoint["lat"], "lon": waypoint["lon"]}
        fly_to(waypoint["lat"], waypoint["lon"], feedback_hook=weather_feedback_hook)
    if not is_active_mission(mission_id):
        return
    set_nav_state("arrived", completion_message)


def begin_navigation(lat, lon, route_points=None, message_prefix="Navigating to"):
    lat = float(lat)
    lon = float(lon)
    current_origin = get_position()
    base_route = route_points or [
        {"lat": current_origin["lat"], "lon": current_origin["lon"]},
        {"lat": lat, "lon": lon},
    ]
    mission_id = next_mission_id()
    reset_progress_state()
    set_nav_state("flying", f"{message_prefix} {lat:.5f}, {lon:.5f}")

    def nav_thread():
        try:
            execute_route(base_route, "flying", "Destination reached.", mission_id)
        except Exception as exc:
            print(f"Navigation error: {exc}")
            reset_progress_state()
            set_nav_state("error", f"Navigation error: {exc}")

    threading.Thread(target=nav_thread, daemon=True).start()


def begin_zone_survey(zone):
    waypoints = generate_zone_scan_waypoints(zone)
    mission_id = next_mission_id()
    reset_progress_state()
    set_zone_mission_state(
        status="in_progress",
        message=f"Survey mission started with {len(waypoints)} scan waypoint(s).",
        completed_waypoints=0,
        total_waypoints=len(waypoints),
    )
    store_zone_summary(
        build_pending_zone_summary(
            zone,
            message="Drone is surveying the selected zone. Dispatch summary will appear after full coverage.",
        )
    )
    current_origin = get_position()
    survey_route = [{"lat": current_origin["lat"], "lon": current_origin["lon"]}] + [dict(point) for point in waypoints]
    update_route(survey_route)
    update_flight_plan(route_points=survey_route)
    first_target = waypoints[0] if waypoints else zone_center(zone)
    set_nav_state("surveying", "Surveying selected zone.")

    with state_lock:
        feedback_state["cycle"] = 0
        feedback_state["stage"] = "sense"
        feedback_state["safe"] = True
        feedback_state["reason"] = "Feedback loop armed for this zone survey."
        feedback_state["last_reroute"] = None
        feedback_state["reroute_count"] = 0
        feedback_state["sample_input"] = {
            "origin": {"lat": current_origin["lat"], "lon": current_origin["lon"]},
            "destination": {"lat": first_target["lat"], "lon": first_target["lon"]},
            "weather": dict(weather_state),
        }

    def survey_thread():
        try:
            total_waypoints = len(waypoints)
            for index, point in enumerate(waypoints, start=1):
                if not is_active_mission(mission_id):
                    return
                set_nav_state(
                    "surveying",
                    f"Surveying zone waypoint {index}/{total_waypoints} at {point['lat']:.5f}, {point['lon']:.5f}",
                )
                position = get_position()
                start_point = {"lat": position["lat"], "lon": position["lon"]}
                validation = prepare_route_validation(start_point, {"lat": point["lat"], "lon": point["lon"]})
                if not validation["allowed"]:
                    raise RuntimeError(validation["error"])
                route_points = validation["route_points"]
                update_route(route_points + [dict(item) for item in waypoints[index:]])
                with state_lock:
                    feedback_state["sample_input"]["destination"] = {"lat": point["lat"], "lon": point["lon"]}
                for segment_end in route_points[1:]:
                    if not is_active_mission(mission_id):
                        return
                    fly_to(segment_end["lat"], segment_end["lon"], feedback_hook=weather_feedback_hook)
                set_zone_mission_state(
                    status="in_progress",
                    message=f"Completed {index} of {total_waypoints} survey waypoint(s).",
                    completed_waypoints=index,
                    total_waypoints=total_waypoints,
                )

            final_summary = summarize_zone(zone, survey_complete=True)
            set_zone_mission_state(
                status="completed",
                message="Zone survey complete. Dispatch summary is ready.",
                completed_waypoints=total_waypoints,
                total_waypoints=total_waypoints,
            )
            if not is_active_mission(mission_id):
                return
            set_nav_state("arrived", final_summary["dispatch_message"])
        except Exception as exc:
            pending_summary = build_pending_zone_summary(
                zone,
                message="Zone survey did not complete. Dispatch summary is still pending.",
            )
            store_zone_summary(pending_summary)
            set_zone_mission_state(status="error", message=f"Zone survey error: {exc}")
            print(f"Zone survey error: {exc}")
            reset_progress_state()
            set_nav_state("error", f"Zone survey error: {exc}")

    threading.Thread(target=survey_thread, daemon=True).start()


ensure_database()
try:
    apply_weather_state(0, 0, 0)
except Exception:
    pass


@app.route("/")
def home():
    return render_template("index.html")


@app.route("/position")
def position():
    try:
        pos = get_position()
    except Exception as exc:
        set_nav_state("error", f"Position error: {exc}")
        return jsonify(
            {
                "status": nav_state["status"],
                "message": nav_state["message"],
                "distance_to_goal": flight_progress.get("distance_to_goal", 0),
                "progress_percent": flight_progress.get("progress_percent", 0),
                "target_lat": flight_progress.get("target_lat"),
                "target_lon": flight_progress.get("target_lon"),
                "feedback": feedback_state,
                "weather": get_weather_snapshot(),
                "dispatch": get_dispatch_snapshot(),
                "demo": get_demo_snapshot(),
                "no_fly_zones": list_zones(include_inactive=True),
                "flight_plan": get_flight_plan_snapshot(),
            }
        ), 500

    with state_lock:
        pos["status"] = nav_state["status"]
        pos["message"] = nav_state["message"]
        pos["feedback"] = dict(feedback_state)

    pos["distance_to_goal"] = flight_progress.get("distance_to_goal", 0)
    pos["progress_percent"] = flight_progress.get("progress_percent", 0)
    pos["target_lat"] = flight_progress.get("target_lat")
    pos["target_lon"] = flight_progress.get("target_lon")
    pos["weather"] = get_weather_snapshot()
    pos["dispatch"] = get_dispatch_snapshot()
    pos["demo"] = get_demo_snapshot()
    pos["drone"] = build_drone_snapshot()
    pos["no_fly_zones"] = list_zones(include_inactive=True)
    pos["flight_plan"] = get_flight_plan_snapshot()
    return jsonify(pos)


@app.route("/no-fly-zones", methods=["GET", "POST"])
def no_fly_zones_collection():
    if request.method == "GET":
        return jsonify({"zones": list_zones(include_inactive=True)})

    data = request.json or {}
    try:
        payload = validate_zone_payload(data)
    except (TypeError, ValueError, KeyError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    zone = create_zone(**payload)
    revalidate_active_route()
    return jsonify({"status": "ok", "zone": zone})


@app.route("/no-fly-zones/<int:zone_id>", methods=["PUT", "DELETE"])
def no_fly_zone_item(zone_id):
    existing = get_zone(zone_id)
    if not existing:
        return jsonify({"status": "error", "message": "No-fly zone not found."}), 404

    if request.method == "DELETE":
        delete_zone(zone_id)
        revalidate_active_route()
        return jsonify({"status": "ok"})

    data = request.json or {}
    merged = {
        "name": data.get("name", existing["name"]),
        "description": data.get("description", existing["description"]),
        "shape_type": data.get("shape_type", existing["shape_type"]),
        "geometry": data.get("geometry", existing["geometry"]),
        "buffer_distance_meters": data.get("buffer_distance_meters", existing["buffer_distance_meters"]),
        "is_active": data.get("is_active", existing["is_active"]),
        "created_by": existing["created_by"],
    }
    try:
        payload = validate_zone_payload(merged)
    except (TypeError, ValueError, KeyError) as exc:
        return jsonify({"status": "error", "message": str(exc)}), 400
    zone = update_zone(
        zone_id,
        payload["name"],
        payload["description"],
        payload["shape_type"],
        payload["geometry"],
        payload["buffer_distance_meters"],
        payload["is_active"],
    )
    revalidate_active_route()
    return jsonify({"status": "ok", "zone": zone})


@app.route("/no-fly-zones/<int:zone_id>/active", methods=["POST"])
def no_fly_zone_active(zone_id):
    data = request.json or {}
    if "is_active" not in data:
        return jsonify({"status": "error", "message": "is_active is required."}), 400
    zone = set_zone_active(zone_id, bool(data["is_active"]))
    if not zone:
        return jsonify({"status": "error", "message": "No-fly zone not found."}), 404
    revalidate_active_route()
    return jsonify({"status": "ok", "zone": zone})


@app.route("/no-fly-zones", methods=["DELETE"])
def clear_no_fly_zones():
    zones = list_zones(include_inactive=True)
    for zone in zones:
        delete_zone(zone["id"])
    next_mission_id()
    reset_progress_state()
    set_nav_state("idle", "All no-fly zones cleared.")
    return jsonify({"status": "ok", "deleted": len(zones)})


@app.route("/bins", methods=["GET", "POST", "DELETE"])
def bins():
    if request.method == "GET":
        return jsonify(get_dispatch_snapshot())

    if request.method == "DELETE":
        with state_lock:
            bin_state["bins"] = []
            bin_state["last_zone"] = None
            bin_state["last_summary"] = None
            bin_state["next_id"] = 1
            zone_mission_state["status"] = "idle"
            zone_mission_state["message"] = "No zone survey has been started."
            zone_mission_state["completed_waypoints"] = 0
            zone_mission_state["total_waypoints"] = 0
        return jsonify({"status": "ok"})

    data = request.json or {}
    try:
        lat = float(data.get("lat"))
        lon = float(data.get("lon"))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Valid bin coordinates are required."}), 400

    bin_type = str(data.get("bin_type", "")).strip().lower()
    if bin_type not in {"green", "blue", "garbage"}:
        return jsonify({"status": "error", "message": "Bin type must be green, blue, or garbage."}), 400

    with state_lock:
        bin_id = bin_state["next_id"]
        bin_state["next_id"] += 1
        bin_item = {
            "id": bin_id,
            "bin_type": bin_type,
            "is_overflowing": bool(data.get("is_overflowing", False)),
            "lat": lat,
            "lon": lon,
            "address": str(data.get("address") or "").strip() or "Address pending",
            "label": str(data.get("label") or "").strip() or f"{bin_type.title()} Bin",
            "last_pickup_at": str(data.get("last_pickup_at") or "").strip(),
            "request_created_at": str(data.get("request_created_at") or "").strip() or utc_now_iso(),
            "location_importance": str(data.get("location_importance") or "").strip().lower(),
            "inspection_confidence": data.get("inspection_confidence", 0.9),
            "loose_garbage_detected": bool(data.get("loose_garbage_detected", False)),
            "has_open_request": bool(data.get("has_open_request", False)),
            "request_id": str(data.get("request_id") or f"BIN-{bin_id}").strip(),
            "open_request_id": str(data.get("open_request_id") or "").strip() or None,
        }
        update_bin_condition_fields(bin_item)
        bin_state["bins"].append(bin_item)
    return jsonify({"status": "ok", "bin": bin_item})


@app.route("/bins/<int:bin_id>/overflow", methods=["POST"])
def toggle_bin_overflow(bin_id):
    data = request.json or {}
    if "is_overflowing" not in data:
        return jsonify({"status": "error", "message": "Overflow state is required."}), 400

    with state_lock:
        bin_item = find_bin_by_id(bin_id)
        if not bin_item:
            return jsonify({"status": "error", "message": "Bin not found."}), 404
        bin_item["is_overflowing"] = bool(data.get("is_overflowing"))
        update_bin_condition_fields(bin_item)
        updated_bin = dict(bin_item)

    return jsonify(
        {
            "status": "ok",
            "bin": updated_bin,
            "message": (
                f"{updated_bin['label']} marked as overflowing."
                if updated_bin["is_overflowing"]
                else f"{updated_bin['label']} restored to standard condition."
            ),
        }
    )


@app.route("/bins/<int:bin_id>", methods=["PUT"])
def update_bin(bin_id):
    data = request.json or {}
    with state_lock:
        bin_item = find_bin_by_id(bin_id)
        if not bin_item:
            return jsonify({"status": "error", "message": "Bin not found."}), 404
        if "address" in data:
            bin_item["address"] = str(data.get("address") or "").strip() or bin_item["address"]
        if "label" in data:
            bin_item["label"] = str(data.get("label") or "").strip() or bin_item["label"]
        if "last_pickup_at" in data:
            bin_item["last_pickup_at"] = str(data.get("last_pickup_at") or "").strip()
        if "request_created_at" in data:
            bin_item["request_created_at"] = str(data.get("request_created_at") or "").strip() or bin_item["request_created_at"]
        if "location_importance" in data:
            bin_item["location_importance"] = str(data.get("location_importance") or "").strip().lower()
        if "inspection_confidence" in data:
            bin_item["inspection_confidence"] = data.get("inspection_confidence", bin_item.get("inspection_confidence", 0.9))
        if "loose_garbage_detected" in data:
            bin_item["loose_garbage_detected"] = bool(data.get("loose_garbage_detected"))
        if "has_open_request" in data:
            bin_item["has_open_request"] = bool(data.get("has_open_request"))
        if "request_id" in data:
            bin_item["request_id"] = str(data.get("request_id") or "").strip() or bin_item["request_id"]
        if "open_request_id" in data:
            bin_item["open_request_id"] = str(data.get("open_request_id") or "").strip() or None
        update_bin_condition_fields(bin_item)
        updated_bin = dict(bin_item)
    return jsonify({"status": "ok", "bin": updated_bin})


@app.route("/zone-summary", methods=["POST"])
def zone_summary():
    data = request.json or {}
    try:
        zone = {
            "south": float(data.get("south")),
            "north": float(data.get("north")),
            "west": float(data.get("west")),
            "east": float(data.get("east")),
        }
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Zone bounds are required."}), 400

    if zone["south"] > zone["north"] or zone["west"] > zone["east"]:
        return jsonify({"status": "error", "message": "Zone bounds are invalid."}), 400

    summary = build_pending_zone_summary(zone)
    store_zone_summary(summary)
    set_zone_mission_state(
        status="ready",
        message="Zone saved. Start the survey flight to collect dispatch details.",
        completed_waypoints=0,
        total_waypoints=len(generate_zone_scan_waypoints(zone)),
    )
    return jsonify({"status": "ok", "summary": summary})


@app.route("/zone-fly", methods=["POST"])
def zone_fly():
    data = request.json or {}
    try:
        zone = {
            "south": float(data.get("south")),
            "north": float(data.get("north")),
            "west": float(data.get("west")),
            "east": float(data.get("east")),
        }
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Zone bounds are required."}), 400

    if zone["south"] > zone["north"] or zone["west"] > zone["east"]:
        return jsonify({"status": "error", "message": "Zone bounds are invalid."}), 400

    center = zone_center(zone)
    validation = validate_zone_survey_plan(zone)
    if not validation["allowed"]:
        next_mission_id()
        reset_progress_state()
        set_nav_state("error", validation["error"])
        set_zone_mission_state(status="error", message=validation.get("details") or validation["error"])
        return jsonify({"status": "error", "message": validation["error"]}), 409

    pending_summary = build_pending_zone_summary(
        zone,
        message="Drone dispatched. Survey is in progress and dispatch summary will appear after full zone coverage.",
    )
    store_zone_summary(pending_summary)
    begin_zone_survey(zone)
    return jsonify(
        {
            "status": "ok",
            "message": f"Drone dispatched to survey the zone starting near {center['lat']:.5f}, {center['lon']:.5f}.",
            "center": center,
            "summary": pending_summary,
            "route": validation["route_points"],
            "warnings": validation.get("warnings", []),
            "rerouted": validation.get("rerouted", False),
        }
    )


@app.route("/weather", methods=["GET", "POST"])
def weather():
    if request.method == "GET":
        return jsonify(get_weather_snapshot())

    data = request.json or {}
    try:
        rain = float(data.get("rain", 0))
        wind = float(data.get("wind", 0))
        snow = float(data.get("snow", 0))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Weather values must be numeric."}), 400

    try:
        applied = apply_weather_state(rain, wind, snow)
    except Exception as exc:
        return jsonify({"status": "error", "message": f"Failed to sync weather to AirSim: {exc}"}), 500

    set_feedback_stage(
        feedback_state["stage"],
        weather=applied,
        reason="Weather updated from user input and synced to AirSim.",
    )
    return jsonify({"status": "ok", "weather": applied})


@app.route("/speed", methods=["GET", "POST"])
def speed():
    if request.method == "GET":
        return jsonify(get_demo_snapshot())

    data = request.json or {}
    try:
        multiplier = float(data.get("multiplier", 1))
    except (TypeError, ValueError):
        return jsonify({"status": "error", "message": "Speed multiplier must be numeric."}), 400

    applied = apply_speed_state(multiplier)
    return jsonify({"status": "ok", "demo": applied})


@app.route("/start")
def start():
    next_mission_id()
    reset_progress_state()
    set_nav_state("taking_off", "Sending takeoff command to AirSim.")
    set_zone_mission_state(
        status="idle",
        message="No zone survey has been started.",
        completed_waypoints=0,
        total_waypoints=zone_mission_state["total_waypoints"],
    )
    try:
        takeoff()
        set_nav_state("idle", "Takeoff complete.")
        return {"status": "ok", "message": nav_state["message"]}
    except Exception as exc:
        set_nav_state("error", f"Takeoff failed: {exc}")
        return jsonify({"status": "error", "message": nav_state["message"]}), 500


@app.route("/goto", methods=["POST"])
def goto():
    data = request.json or {}
    lat = data.get("lat")
    lon = data.get("lon")
    if lat is None or lon is None:
        set_nav_state("error", "Missing destination coordinates.")
        return jsonify({"status": "error", "message": nav_state["message"]}), 400

    try:
        target_point = {"lat": float(lat), "lon": float(lon)}
    except (TypeError, ValueError):
        set_nav_state("error", "Destination coordinates must be numeric.")
        return jsonify({"status": "error", "message": nav_state["message"]}), 400

    set_zone_mission_state(
        status="idle",
        message="Manual flight active. Zone survey is not running.",
        completed_waypoints=0,
        total_waypoints=0,
    )
    validation = prepare_route_validation(current_route_start(), target_point)
    if not validation["allowed"]:
        next_mission_id()
        reset_progress_state()
        set_nav_state("error", validation["error"])
        return jsonify({"status": "error", "message": validation["error"]}), 409

    if validation.get("message"):
        set_nav_state("flying", validation["message"])
    elif validation.get("warnings"):
        set_nav_state("flying", validation["warnings"][0])
    begin_navigation(target_point["lat"], target_point["lon"], route_points=validation["route_points"])
    return jsonify(
        {
            "status": "ok",
            "message": nav_state["message"],
            "route": validation["route_points"],
            "warnings": validation.get("warnings", []),
            "rerouted": validation.get("rerouted", False),
        }
    )


if __name__ == "__main__":
    app.run(debug=False, use_reloader=False)
