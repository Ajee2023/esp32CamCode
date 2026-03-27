import math
import threading
import time

from flask import Flask, jsonify, request, render_template

from drone_control import get_position, set_sim_weather, takeoff, fly_to, flight_progress

app = Flask(__name__)

state_lock = threading.Lock()

nav_state = {
    "status": "idle",
    "message": "",
}

weather_state = {
    "rain": 0,
    "wind": 0,
    "snow": 0,
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


def set_feedback_stage(stage, **updates):
    with state_lock:
        feedback_state["stage"] = stage
        for key, value in updates.items():
            feedback_state[key] = value


def get_weather_snapshot():
    with state_lock:
        return dict(weather_state)


def apply_weather_state(rain, wind, snow):
    applied = set_sim_weather(rain, wind, snow)
    with state_lock:
        weather_state.update(applied)
    return applied


def update_route(points):
    with state_lock:
        feedback_state["active_route"] = points


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
    """
    Real feedback loop:
    sense -> analyze -> act -> repeat
    The sensed weather comes from user-controlled sliders shared with AirSim.
    """
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


try:
    apply_weather_state(0, 0, 0)
except Exception:
    # AirSim may not be up when Flask imports the module; weather can sync later.
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

    return jsonify(pos)


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


@app.route("/start")
def start():
    set_nav_state("taking_off", "Sending takeoff command to AirSim.")
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

    set_nav_state("flying", f"Navigating to {lat:.5f}, {lon:.5f}")
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

    return {"status": "ok", "message": nav_state["message"]}


if __name__ == "__main__":
    app.run(debug=False, use_reloader=False)
