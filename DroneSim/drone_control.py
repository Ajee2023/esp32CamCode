import math
import threading
import time

import airsim


AIRSIM_HOST = ""
AIRSIM_PORT = 41451
AIRSIM_TIMEOUT_SECONDS = 3
AIRSIM_PING_INTERVAL_SECONDS = 2.0


def create_client():
    """Build a fresh AirSim RPC client with the configured timeout."""
    return airsim.MultirotorClient(
        ip=AIRSIM_HOST,
        port=AIRSIM_PORT,
        timeout_value=AIRSIM_TIMEOUT_SECONDS,
    )


client = create_client()
client_lock = threading.RLock()
last_connection_check = 0.0

HOME_LAT = 43.7315
HOME_LON = -79.7624

# PROGRESS TRACKING (for UI updates)
flight_progress = {
    "distance_to_goal": 0,
    "progress_percent": 0,
    "target_lat": None,
    "target_lon": None,
}

# ============ CONFIGURATION ============
MAX_SPEED = 8.0
ALTITUDE_TARGET = -10.0
ARRIVAL_THRESHOLD = 2.0
ALTITUDE_LAYER_STEP = 4.0
ALTITUDE_CLIMB_RETRY_INTERVAL = 2
STUCK_PROGRESS_EPSILON = 0.2
STUCK_ITERATIONS = 12
COLLISION_COOLDOWN = 4
DETOUR_STEP = 10.0
DETOUR_FORWARD_BIAS = 6.0
WAYPOINT_REACHED_FACTOR = 1.75
MAX_DETOUR_DISTANCE = 18.0
MAX_WAYPOINTS = 2
MAX_DISTANCE_BACKTRACK_FACTOR = 1.35
FEEDBACK_LOOP_INTERVAL_SECONDS = 3.0


# ============ HELPER: GPS <-> SIMULATION ============
def gps_to_sim(lat, lon):
    """Convert GPS coordinates to simulation coordinates."""
    x = (lon - HOME_LON) * 100000
    y = (lat - HOME_LAT) * 100000
    z = ALTITUDE_TARGET
    return x, y, z


def sim_to_gps(x, y):
    """Convert simulation coordinates to GPS."""
    lat = HOME_LAT + (y / 100000)
    lon = HOME_LON + (x / 100000)
    return lat, lon


# ============ POSITION & STATE ============
def airsim_call(fn):
    """Serialize access to the shared AirSim client."""
    with client_lock:
        return fn(client)


def reset_client():
    """Recreate the RPC client if the simulator connection goes stale."""
    global client
    with client_lock:
        client = create_client()
        return client


def ensure_connection(force=False):
    """Fail fast when the AirSim RPC server is not responding."""
    global client
    global last_connection_check

    with client_lock:
        now = time.time()
        if not force and now - last_connection_check < AIRSIM_PING_INTERVAL_SECONDS:
            return client

        errors = []
        for attempt in range(2):
            current_client = client if attempt == 0 else create_client()
            try:
                if not current_client.ping():
                    raise RuntimeError("AirSim RPC server did not acknowledge ping.")

                if attempt == 1:
                    client = current_client

                last_connection_check = now
                return client
            except Exception as exc:
                errors.append(f"attempt {attempt + 1}: {exc}")

        raise RuntimeError(
            "AirSim RPC server is reachable on port 41451 but not responding to API calls. "
            "Restart the simulator or press Play in Unreal/Blocks, then try again. "
            f"Details: {' | '.join(errors)}"
        )


def get_position():
    """Get current drone position in GPS and simulation coordinates."""
    ensure_connection()
    state = airsim_call(lambda c: c.getMultirotorState())
    pos = state.kinematics_estimated.position
    lat, lon = sim_to_gps(pos.x_val, pos.y_val)

    return {
        "lat": lat,
        "lon": lon,
        "x": pos.x_val,
        "y": pos.y_val,
        "z": pos.z_val,
    }


def get_multirotor_state():
    """Fetch full drone state."""
    ensure_connection()
    return airsim_call(lambda c: c.getMultirotorState())


def get_current_yaw():
    """Get current drone heading in degrees (0-360)."""
    ensure_connection()
    state = airsim_call(lambda c: c.getMultirotorState())
    orientation = state.kinematics_estimated.orientation

    q = orientation
    yaw_rad = math.atan2(
        2 * (q.w_val * q.z_val + q.x_val * q.y_val),
        1 - 2 * (q.y_val ** 2 + q.z_val ** 2),
    )
    yaw_deg = math.degrees(yaw_rad)

    if yaw_deg < 0:
        yaw_deg += 360

    return yaw_deg


# ============ TAKEOFF ============
def takeoff():
    """Takeoff and reach hovering state."""
    prepare_for_flight(force_takeoff=True)
    print("Drone ready to fly.")


def prepare_for_flight(force_takeoff=False):
    """Reconnect control surfaces and make sure the drone can accept commands."""
    ensure_connection()
    airsim_call(lambda c: c.confirmConnection())
    airsim_call(lambda c: c.enableApiControl(True))
    airsim_call(lambda c: c.armDisarm(True))

    state = get_multirotor_state()
    landed_state = getattr(state, "landed_state", None)
    is_landed = landed_state == airsim.LandedState.Landed

    pos = state.kinematics_estimated.position
    near_ground = pos.z_val > -1.0

    if force_takeoff or is_landed or near_ground:
        print("Taking off...")
        airsim_call(lambda c: c.takeoffAsync().join())
        time.sleep(0.2)

    airsim_call(lambda c: c.moveToZAsync(ALTITUDE_TARGET, 2.5).join())
    time.sleep(0.5)


# ============ VECTOR MATH ============
def normalize_vector(dx, dy):
    """Normalize direction vector to unit length."""
    length = math.sqrt(dx ** 2 + dy ** 2)

    if length == 0:
        return 0.0, 0.0

    return dx / length, dy / length


def vector_magnitude(dx, dy):
    """Get length of direction vector."""
    return math.sqrt(dx ** 2 + dy ** 2)


def limit_velocity(vx, vy, max_speed=MAX_SPEED):
    """Limit velocity to max speed while preserving direction."""
    speed = math.sqrt(vx ** 2 + vy ** 2)

    if speed > max_speed:
        scale = max_speed / speed
        vx *= scale
        vy *= scale

    return vx, vy


def clamp(value, min_value, max_value):
    """Clamp value into the provided numeric range."""
    return max(min_value, min(max_value, value))


def angular_distance_deg(angle_a, angle_b):
    """Smallest absolute difference between two headings."""
    return abs((angle_a - angle_b + 180) % 360 - 180)


def get_collision_state():
    """Fetch collision state safely from AirSim."""
    try:
        return airsim_call(lambda c: c.simGetCollisionInfo())
    except Exception:
        return None


def set_sim_weather(rain_percent, wind_percent, snow_percent):
    """
    Push shared weather values into AirSim so the simulator matches the webpage.
    Rain and snow map directly to AirSim weather parameters.
    Wind uses a simple horizontal vector whose magnitude grows with the slider.
    """
    ensure_connection()

    rain = clamp(float(rain_percent), 0.0, 100.0)
    wind = clamp(float(wind_percent), 0.0, 100.0)
    snow = clamp(float(snow_percent), 0.0, 100.0)

    weather_enabled = any(value > 0 for value in (rain, wind, snow))
    airsim_call(lambda c: c.simEnableWeather(weather_enabled))

    airsim_call(lambda c: c.simSetWeatherParameter(airsim.WeatherParameter.Rain, rain / 100.0))
    airsim_call(lambda c: c.simSetWeatherParameter(airsim.WeatherParameter.Snow, snow / 100.0))
    airsim_call(lambda c: c.simSetWeatherParameter(airsim.WeatherParameter.RoadSnow, snow / 100.0))

    # Map 0-100% wind to 0-12 m/s on the X axis for a visible simulator effect.
    wind_speed = (wind / 100.0) * 12.0
    airsim_call(lambda c: c.simSetWind(airsim.Vector3r(wind_speed, 0.0, 0.0)))

    return {
        "rain": int(round(rain)),
        "wind": int(round(wind)),
        "snow": int(round(snow)),
    }


def ensure_altitude(target_z, speed=2.5, tolerance=0.75):
    """Move back to the requested cruise altitude if needed."""
    pos = get_position()
    if abs(pos["z"] - target_z) <= tolerance:
        return

    airsim_call(lambda c: c.moveToZAsync(target_z, speed).join())
    time.sleep(0.1)


def build_detour_waypoint(current_x, current_y, target_x, target_y, attempt_number):
    """
    Create a lateral bypass waypoint that alternates sides and grows outward.
    """
    dx = target_x - current_x
    dy = target_y - current_y
    forward_x, forward_y = normalize_vector(dx, dy)

    if forward_x == 0 and forward_y == 0:
        forward_x, forward_y = 1.0, 0.0

    side = -1.0 if attempt_number % 2 == 0 else 1.0
    ring = max(1, (attempt_number + 1) // 2)
    lateral_distance = DETOUR_STEP * ring

    lateral_x = -forward_y * side
    lateral_y = forward_x * side

    detour_x = current_x + lateral_x * lateral_distance + forward_x * DETOUR_FORWARD_BIAS
    detour_y = current_y + lateral_y * lateral_distance + forward_y * DETOUR_FORWARD_BIAS
    return detour_x, detour_y


def choose_detour_waypoint(current_x, current_y, active_target_x, active_target_y, goal_x, goal_y, attempt_number):
    """
    Pick a bounded detour that still trends back toward the goal.
    """
    candidates = []
    base_attempt = max(1, attempt_number)

    for local_attempt in (base_attempt, base_attempt + 1):
        waypoint_x, waypoint_y = build_detour_waypoint(
            current_x, current_y, active_target_x, active_target_y, local_attempt
        )

        offset = vector_magnitude(waypoint_x - current_x, waypoint_y - current_y)
        if offset > MAX_DETOUR_DISTANCE:
            scale = MAX_DETOUR_DISTANCE / offset
            waypoint_x = current_x + (waypoint_x - current_x) * scale
            waypoint_y = current_y + (waypoint_y - current_y) * scale

        goal_distance = vector_magnitude(goal_x - waypoint_x, goal_y - waypoint_y)
        target_distance = vector_magnitude(active_target_x - waypoint_x, active_target_y - waypoint_y)
        candidates.append((goal_distance, target_distance, waypoint_x, waypoint_y))

    candidates.sort(key=lambda item: (item[0], item[1]))
    _, _, best_x, best_y = candidates[0]
    return best_x, best_y


def is_new_waypoint(waypoints, candidate_x, candidate_y, min_gap=3.0):
    """Avoid stacking nearly identical emergency waypoints."""
    for waypoint_x, waypoint_y in waypoints:
        if vector_magnitude(candidate_x - waypoint_x, candidate_y - waypoint_y) < min_gap:
            return False
    return True


# ============ MOVEMENT ============
def rotate_to_heading(target_yaw, speed=25):
    """Rotate drone to face a specific heading."""
    current_yaw = get_current_yaw()
    diff = (target_yaw - current_yaw) % 360

    if diff > 180:
        diff -= 360

    if abs(diff) < 3:
        return

    print(f"Rotating from {current_yaw:.0f} deg to {target_yaw:.0f} deg")
    airsim_call(
        lambda c: c.rotateByYawRateAsync(
            speed if diff >= 0 else -speed,
            duration=abs(diff) / speed,
        ).join()
    )
    time.sleep(0.1)


def move_step(vx, vy, target_z, duration=0.25):
    """Move one incremental step in (vx, vy) while holding altitude."""
    vx, vy = limit_velocity(vx, vy, MAX_SPEED)

    pos = get_position()
    vz = clamp((target_z - pos["z"]) * 0.8, -2.0, 2.0)

    airsim_call(lambda c: c.moveByVelocityAsync(vx, vy, vz, duration=duration).join())
    time.sleep(0.05)


# ============ NAVIGATION ============
def fly_to_intelligent(lat, lon, feedback_hook=None):
    """
    Fly to target with collision-aware rerouting.

    The previous version relied on simLineTraceByDistance, which is not available
    in this AirSim environment. That meant obstacle detection silently failed and
    the drone kept trying to punch through objects. This planner instead reroutes
    when progress stalls or a collision is reported, and it can climb to higher
    cruise layers when lateral bypasses alone are not enough.
    """
    target_x, target_y, _ = gps_to_sim(lat, lon)
    prepare_for_flight(force_takeoff=False)
    pos = get_position()

    flight_progress["target_lat"] = lat
    flight_progress["target_lon"] = lon

    initial_dx = target_x - pos["x"]
    initial_dy = target_y - pos["y"]
    initial_distance = vector_magnitude(initial_dx, initial_dy)

    cruise_altitude = ALTITUDE_TARGET
    base_cruise_altitude = ALTITUDE_TARGET
    ensure_altitude(cruise_altitude)

    waypoints = []
    current_target = (target_x, target_y)

    print("\n" + "=" * 70)
    print("INTELLIGENT NAVIGATION READY")
    print("=" * 70)
    print(f"Target: GPS({lat:.4f}, {lon:.4f})")
    print(f"Start:  SIM({pos['x']:.1f}, {pos['y']:.1f})")
    print(f"Initial Distance: {initial_distance:.1f}m")
    print("Avoidance mode: collision-aware detours + adaptive altitude")
    print("=" * 70 + "\n")

    max_iterations = 3000
    iteration = 0
    attempts_stuck = 0
    last_distance = float("inf")
    detour_attempts = 0
    altitude_layer = 0
    collision_cooldown = 0
    last_feedback_time = 0.0

    while iteration < max_iterations:
        iteration += 1

        pos = get_position()
        ensure_altitude(cruise_altitude)
        pos = get_position()
        current_yaw = get_current_yaw()

        target_x_curr, target_y_curr = current_target
        dx = target_x_curr - pos["x"]
        dy = target_y_curr - pos["y"]
        distance_to_current_target = vector_magnitude(dx, dy)

        dx_to_goal = target_x - pos["x"]
        dy_to_goal = target_y - pos["y"]
        distance_to_goal = vector_magnitude(dx_to_goal, dy_to_goal)
        flight_progress["distance_to_goal"] = distance_to_goal
        if initial_distance > 0:
            progress = 100 * (1 - distance_to_goal / initial_distance)
            flight_progress["progress_percent"] = max(0, min(100, progress))

        # Feedback loop: sense -> analyze -> act -> repeat every few seconds.
        if feedback_hook and (time.time() - last_feedback_time) >= FEEDBACK_LOOP_INTERVAL_SECONDS:
            last_feedback_time = time.time()
            feedback_result = feedback_hook({
                "position": {
                    "lat": pos["lat"],
                    "lon": pos["lon"],
                    "x": pos["x"],
                    "y": pos["y"],
                    "z": pos["z"],
                },
                "destination": {
                    "lat": lat,
                    "lon": lon,
                    "x": target_x,
                    "y": target_y,
                },
                "current_target": {
                    "x": current_target[0],
                    "y": current_target[1],
                },
                "distance_to_goal": distance_to_goal,
                "progress_percent": flight_progress["progress_percent"],
            }) or {}

            if feedback_result.get("message"):
                print(feedback_result["message"])

            reroute = feedback_result.get("reroute")
            if feedback_result.get("safe", True):
                if waypoints and feedback_result.get("clear_weather_route", False):
                    waypoints = [(target_x, target_y)]
                    current_target = waypoints[0]
            elif reroute:
                reroute_x, reroute_y, _ = gps_to_sim(reroute["lat"], reroute["lon"])
                waypoints = [(reroute_x, reroute_y), (target_x, target_y)]
                current_target = waypoints[0]
                altitude_layer = 0
                cruise_altitude = base_cruise_altitude
                ensure_altitude(cruise_altitude)
                print(
                    f"Weather reroute engaged via GPS({reroute['lat']:.5f}, {reroute['lon']:.5f})"
                )

        if distance_to_goal < ARRIVAL_THRESHOLD:
            print("\n" + "=" * 70)
            print("REACHED DESTINATION")
            print("=" * 70)
            print(f"Final distance: {distance_to_goal:.2f}m")
            print(f"Total iterations: {iteration}")
            print(f"Detours used: {detour_attempts}")

            flight_progress["distance_to_goal"] = 0
            flight_progress["progress_percent"] = 100
            airsim_call(lambda c: c.moveByVelocityAsync(0, 0, 0, 1).join())
            break

        if waypoints and distance_to_current_target < ARRIVAL_THRESHOLD * WAYPOINT_REACHED_FACTOR:
            print(f"[{iteration}] Waypoint reached.")
            waypoints.pop(0)
            current_target = waypoints[0] if waypoints else (target_x, target_y)

            if not waypoints and cruise_altitude != base_cruise_altitude:
                altitude_layer = 0
                cruise_altitude = base_cruise_altitude
                print(f"[{iteration}] Returning to normal cruise altitude {abs(cruise_altitude):.1f}m.")
                ensure_altitude(cruise_altitude)
            continue

        collision = get_collision_state()
        has_collided = bool(collision and collision.has_collided)

        progress_made = last_distance - distance_to_goal
        if iteration > 1 and progress_made < STUCK_PROGRESS_EPSILON:
            attempts_stuck += 1
        else:
            attempts_stuck = 0
        last_distance = distance_to_goal

        if has_collided:
            collision_cooldown = COLLISION_COOLDOWN
            attempts_stuck = max(attempts_stuck, STUCK_ITERATIONS)
            print(f"[{iteration}] Collision detected. Replanning route.")
            airsim_call(lambda c: c.moveByVelocityAsync(0, 0, 0, 0.2).join())

        blocked = attempts_stuck >= STUCK_ITERATIONS or collision_cooldown > 0
        if blocked:
            detour_attempts += 1

            if detour_attempts >= ALTITUDE_CLIMB_RETRY_INTERVAL:
                altitude_layer += 1
                cruise_altitude = ALTITUDE_TARGET - altitude_layer * ALTITUDE_LAYER_STEP
                print(
                    f"[{iteration}] Raising cruise altitude to "
                    f"{abs(cruise_altitude):.1f}m for a cleaner corridor."
                )
                ensure_altitude(cruise_altitude)
                detour_attempts = 0

            active_target_x, active_target_y = current_target
            bypass_x, bypass_y = choose_detour_waypoint(
                pos["x"], pos["y"],
                active_target_x, active_target_y,
                target_x, target_y,
                detour_attempts
            )

            waypoints = [(bypass_x, bypass_y)]
            if vector_magnitude(bypass_x - target_x, bypass_y - target_y) > ARRIVAL_THRESHOLD:
                waypoints.append((target_x, target_y))

            if len(waypoints) > MAX_WAYPOINTS:
                waypoints = waypoints[:MAX_WAYPOINTS]

            current_target = waypoints[0] if waypoints else (target_x, target_y)
            print(
                f"[{iteration}] Detour {detour_attempts}: "
                f"waypoint ({current_target[0]:.1f}, {current_target[1]:.1f}), "
                f"altitude {abs(cruise_altitude):.1f}m"
            )

            attempts_stuck = 0
            collision_cooldown = max(0, collision_cooldown - 1)
            continue

        collision_cooldown = max(0, collision_cooldown - 1)

        if not waypoints and cruise_altitude != base_cruise_altitude:
            altitude_layer = 0
            cruise_altitude = base_cruise_altitude
            print(f"[{iteration}] Path recovered. Descending to normal cruise altitude {abs(cruise_altitude):.1f}m.")
            ensure_altitude(cruise_altitude)

        if distance_to_goal > initial_distance * MAX_DISTANCE_BACKTRACK_FACTOR:
            print(f"[{iteration}] Route is drifting too far from the goal. Resetting to a direct approach.")
            waypoints = []
            current_target = (target_x, target_y)
            altitude_layer = 0
            cruise_altitude = base_cruise_altitude
            ensure_altitude(cruise_altitude)
            attempts_stuck = 0

        target_x_curr, target_y_curr = current_target
        dx = target_x_curr - pos["x"]
        dy = target_y_curr - pos["y"]

        dx_norm, dy_norm = normalize_vector(dx, dy)
        desired_yaw = math.degrees(math.atan2(dy_norm, dx_norm))
        if desired_yaw < 0:
            desired_yaw += 360

        if angular_distance_deg(desired_yaw, current_yaw) > 8:
            rotate_to_heading(desired_yaw, speed=15)

        distance_to_target = vector_magnitude(
            current_target[0] - pos["x"],
            current_target[1] - pos["y"],
        )
        speed_factor = min(1.0, distance_to_target / 20.0)
        speed_factor = max(0.35, speed_factor)

        vx = dx_norm * MAX_SPEED * speed_factor
        vy = dy_norm * MAX_SPEED * speed_factor

        waypoint_str = f" | {len(waypoints)} waypoints" if waypoints else ""
        print(
            f"[{iteration}] MOVING | Dist: {distance_to_goal:.1f}m | "
            f"Altitude: {abs(cruise_altitude):.1f}m | "
            f"Speed: {speed_factor * 100:.0f}% | "
            f"Progress: {flight_progress['progress_percent']:.0f}%{waypoint_str}"
        )
        move_step(vx, vy, cruise_altitude, duration=0.2)

    if iteration >= max_iterations:
        print(f"\nNavigation timeout after {max_iterations} iterations")
        print(f"Final distance to goal: {distance_to_goal:.1f}m")
        airsim_call(lambda c: c.moveByVelocityAsync(0, 0, 0, 1).join())


# ============ LEGACY INTERFACE ============
def fly_to(lat, lon, feedback_hook=None):
    """Compatibility wrapper for the Flask app."""
    fly_to_intelligent(lat, lon, feedback_hook=feedback_hook)
