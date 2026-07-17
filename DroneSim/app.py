import math
import threading
import time

from flask import Flask, jsonify, request, render_template

from drone_control import (
    get_position,
    set_sim_weather,
    takeoff,
    fly_to,
    flight_progress,
    set_speed_multiplier,
    get_speed_multiplier,
)

app = Flask(__name__)

state_lock = threading.Lock()

nav_state = {
    "status": "idle",
    "message": "",
}

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

weather_state = {
    "rain": 0,
    "wind": 0,
    "snow": 0,
}

demo_state = {
    "speed_multiplier": get_speed_multiplier(),
}

feedback_state = {
    "cycle": 0,
    "stage": "repeat",
    "safe": True,
    "reason": "Awaiting weather feedback loop.",
    "last_reroute": None,
    "reroute_count": 0,
    "active_route": [],
    "sample_input": {
        "origin": {"lat": 43.7315, "lon": -79.7624},
        "destination": {"lat": 43.7365, "lon": -79.7545},
        "weather": dict(weather_state),
    },
}


def set_nav_state(status=None, message=None):
    with state_lock:
        if status is not None:
            nav_state["status"] = status
        if message is not None:
            nav_state["message"] = message


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
    bin_item["priority"] = "high" if is_overflowing else "normal"
    bin_item["priority_score"] = 90 if is_overflowing else 30
    return bin_item


def point_in_zone(bin_item, zone):
    lat = bin_item["lat"]
    lon = bin_item["lon"]
    return zone["south"] <= lat <= zone["north"] and zone["west"] <= lon <= zone["east"]


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
        "dispatch_message": message or (
            "Zone selected. Fly the drone across the full zone to collect dispatch details."
        ),
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
    bins = get_bins_snapshot()
    bins_in_zone = [item for item in bins if point_in_zone(item, zone)]

    counts = {
        "green": 0,
        "blue": 0,
        "garbage": 0,
    }
    addresses = []

    for item in bins_in_zone:
        counts[item["bin_type"]] = counts.get(item["bin_type"], 0) + 1
        addresses.append({
            "id": item["id"],
            "bin_type": item["bin_type"],
            "label": item.get("label") or item["bin_type"].title(),
            "condition": item.get("condition", "standard"),
            "priority": item.get("priority", "normal"),
            "priority_score": item.get("priority_score", 30),
            "lat": item["lat"],
            "lon": item["lon"],
            "address": item.get("address") or "Address pending",
        })

    summary = {
        "zone": zone,
        "total_bins": len(bins_in_zone),
        "counts": counts,
        "addresses": addresses,
        "dispatch_message": (
            f"Dispatch summary ready for {len(bins_in_zone)} bin(s) in the selected zone."
            if bins_in_zone else
            "No waste bins found inside the selected zone."
        ),
        "survey_complete": survey_complete,
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
        feedback_state["active_route"] = points


def generate_zone_scan_waypoints(zone):
    south = zone["south"]
    north = zone["north"]
    west = zone["west"]
    east = zone["east"]

    lat_span = max(0.0, north - south)
    lon_span = max(0.0, east - west)

    if lat_span == 0.0 and lon_span == 0.0:
        center = zone_center(zone)
        return [center]

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
    current_lat = current_pos["lat"]
    current_lon = current_pos["lon"]
    dest_lat = destination["lat"]
    dest_lon = destination["lon"]

    dx = dest_lon - current_lon
    dy = dest_lat - current_lat
    length = math.hypot(dx, dy)
    if length == 0:
        return {"lat": dest_lat, "lon": dest_lon}

    severity = max(weather["rain"], weather["wind"], weather["snow"])
    offset_scale = 0.0025 * (severity / 100.0)

    perp_x = -dy / length
    perp_y = dx / length

    midpoint_lat = current_lat + (dest_lat - current_lat) * 0.55
    midpoint_lon = current_lon + (dest_lon - current_lon) * 0.55

    reroute_lat = midpoint_lat + perp_y * offset_scale
    reroute_lon = midpoint_lon + perp_x * offset_scale
    return {"lat": reroute_lat, "lon": reroute_lon}


def weather_feedback_hook(context):
    with state_lock:
        feedback_state["cycle"] += 1
        cycle = feedback_state["cycle"]

    set_feedback_stage("sense", reason="Reading user-set weather and current drone position.")
    weather = get_weather_snapshot()
    current_position = context["position"]
    destination = context["destination"]
    time.sleep(0.15)

    set_feedback_stage(
        "analyze",
        weather=weather,
        reason="Checking whether rain, wind, or snow exceeds 50%.",
    )
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
        result = {
            "safe": False,
            "message": "Conditions are not safe for flight",
            "reroute": reroute,
        }
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
        result = {
            "safe": True,
            "message": "Weather safe. Continue on current path.",
            "clear_weather_route": True,
        }

    time.sleep(0.15)
    set_feedback_stage(
        "repeat",
        weather=weather,
        reason=f"Feedback loop cycle {cycle} complete. Waiting for next user/weather update.",
    )
    return result


def begin_navigation(lat, lon, message_prefix="Navigating to"):
    lat = float(lat)
    lon = float(lon)

    set_nav_state("flying", f"{message_prefix} {lat:.5f}, {lon:.5f}")
    current_origin = get_position()
    update_route([
        {"lat": current_origin["lat"], "lon": current_origin["lon"]},
        {"lat": lat, "lon": lon},
    ])

    with state_lock:
        feedback_state["cycle"] = 0
        feedback_state["stage"] = "sense"
        feedback_state["safe"] = True
        feedback_state["reason"] = "Feedback loop armed for this route."
        feedback_state["last_reroute"] = None
        feedback_state["reroute_count"] = 0
        feedback_state["sample_input"] = {
            "origin": {"lat": current_origin["lat"], "lon": current_origin["lon"]},
            "destination": {"lat": lat, "lon": lon},
            "weather": dict(weather_state),
        }

    def nav_thread():
        try:
            fly_to(lat, lon, feedback_hook=weather_feedback_hook)
            set_nav_state("arrived", "Destination reached.")
        except Exception as e:
            print(f"Navigation error: {e}")
            set_nav_state("error", f"Navigation error: {e}")

    thread = threading.Thread(target=nav_thread, daemon=True)
    thread.start()


def begin_zone_survey(zone):
    waypoints = generate_zone_scan_waypoints(zone)
    set_zone_mission_state(
        status="in_progress",
        message=f"Survey mission started with {len(waypoints)} scan waypoint(s).",
        completed_waypoints=0,
        total_waypoints=len(waypoints),
    )
    store_zone_summary(build_pending_zone_summary(
        zone,
        message="Drone is surveying the selected zone. Dispatch summary will appear after full coverage.",
    ))

    first_target = waypoints[0] if waypoints else zone_center(zone)
    set_nav_state("surveying", "Surveying selected zone.")
    current_origin = get_position()
    update_route(
        [{"lat": current_origin["lat"], "lon": current_origin["lon"]}] +
        [dict(point) for point in waypoints]
    )

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
                set_nav_state(
                    "surveying",
                    f"Surveying zone waypoint {index}/{total_waypoints} at "
                    f"{point['lat']:.5f}, {point['lon']:.5f}",
                )
                pos = get_position()
                update_route(
                    [{"lat": pos["lat"], "lon": pos["lon"]}] +
                    [dict(point)] +
                    [dict(item) for item in waypoints[index:]]
                )
                with state_lock:
                    feedback_state["sample_input"]["destination"] = {
                        "lat": point["lat"],
                        "lon": point["lon"],
                    }
                fly_to(point["lat"], point["lon"], feedback_hook=weather_feedback_hook)
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
            set_nav_state("arrived", final_summary["dispatch_message"])
        except Exception as e:
            pending_summary = build_pending_zone_summary(
                zone,
                message="Zone survey did not complete. Dispatch summary is still pending.",
            )
            store_zone_summary(pending_summary)
            set_zone_mission_state(status="error", message=f"Zone survey error: {e}")
            print(f"Zone survey error: {e}")
            set_nav_state("error", f"Zone survey error: {e}")

    thread = threading.Thread(target=survey_thread, daemon=True)
    thread.start()


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
    except Exception as e:
        set_nav_state("error", f"Position error: {e}")
        return jsonify({
            "status": nav_state["status"],
            "message": nav_state["message"],
            "distance_to_goal": flight_progress.get("distance_to_goal", 0),
            "progress_percent": flight_progress.get("progress_percent", 0),
            "target_lat": flight_progress.get("target_lat", None),
            "target_lon": flight_progress.get("target_lon", None),
            "feedback": feedback_state,
            "weather": get_weather_snapshot(),
            "dispatch": get_dispatch_snapshot(),
            "demo": get_demo_snapshot(),
        }), 500

    with state_lock:
        pos["status"] = nav_state["status"]
        pos["message"] = nav_state["message"]
        pos["feedback"] = dict(feedback_state)

    pos["distance_to_goal"] = flight_progress.get("distance_to_goal", 0)
    pos["progress_percent"] = flight_progress.get("progress_percent", 0)
    pos["target_lat"] = flight_progress.get("target_lat", None)
    pos["target_lon"] = flight_progress.get("target_lon", None)
    pos["weather"] = get_weather_snapshot()
    pos["dispatch"] = get_dispatch_snapshot()
    pos["demo"] = get_demo_snapshot()

    return jsonify(pos)


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

    return jsonify({
        "status": "ok",
        "bin": updated_bin,
        "message": (
            f"{updated_bin['label']} marked as overflowing."
            if updated_bin["is_overflowing"]
            else f"{updated_bin['label']} restored to standard condition."
        ),
    })


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
    pending_summary = build_pending_zone_summary(
        zone,
        message="Drone dispatched. Survey is in progress and dispatch summary will appear after full zone coverage.",
    )
    store_zone_summary(pending_summary)
    begin_zone_survey(zone)
    return jsonify({
        "status": "ok",
        "message": (
            f"Drone dispatched to survey the zone starting near {center['lat']:.5f}, {center['lon']:.5f}."
        ),
        "center": center,
        "summary": pending_summary,
    })


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
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to sync weather to AirSim: {e}"}), 500

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
    except Exception as e:
        set_nav_state("error", f"Takeoff failed: {e}")
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
        set_zone_mission_state(
            status="idle",
            message="Manual flight active. Zone survey is not running.",
            completed_waypoints=0,
            total_waypoints=0,
        )
        begin_navigation(lat, lon)
    except (TypeError, ValueError):
        set_nav_state("error", "Destination coordinates must be numeric.")
        return jsonify({"status": "error", "message": nav_state["message"]}), 400

    return {"status": "ok", "message": nav_state["message"]}


if __name__ == "__main__":
    app.run(debug=False, use_reloader=False)
