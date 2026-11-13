"""Arduino UNO Q App Lab backend for the continuous feeder."""

import json
import socket
import threading

from arduino.app_utils import *
from arduino.app_bricks.web_ui import WebUI


ui = WebUI()
stop_event = threading.Event()


def network_route_available():
    """Check that the App has a usable non-loopback network route."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as probe:
            probe.settimeout(1.0)
            probe.connect(("1.1.1.1", 53))
            return not probe.getsockname()[0].startswith("127.")
    except OSError:
        return False


def get_feeder_status():
    raw = Bridge.call("feeder/status")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    status = json.loads(str(raw))
    status["wifiConnected"] = network_route_available()
    status["simulationMode"] = False
    return status


def send_status(client=None, message=None, error=None):
    try:
        status = get_feeder_status()
        if message:
            status["message"] = message
        if error:
            status["error"] = error
        ui.send_message("feeder_status", status, client)
    except Exception as exception:
        ui.send_message(
            "feeder_error",
            {"message": f"Motor controller is unavailable: {exception}"},
            client,
        )


def on_get_status(client, data):
    send_status(client)


def on_start(client, data):
    try:
        if not bool(Bridge.call("feeder/start")):
            send_status(client, error="Reset emergency stop before starting")
            return
        send_status(message="Feeder started")
    except Exception as exception:
        send_status(client, error=str(exception))


def on_stop(client, data):
    try:
        Bridge.call("feeder/stop")
        send_status(message="Feeder stopped")
    except Exception as exception:
        send_status(client, error=str(exception))


def on_emergency_stop(client, data):
    try:
        Bridge.call("feeder/emergencyStop")
        send_status(message="Emergency stop latched")
    except Exception as exception:
        send_status(client, error=str(exception))


def on_reset_emergency_stop(client, data):
    try:
        if not bool(Bridge.call("feeder/resetEmergencyStop")):
            send_status(client, error="Emergency stop is not active")
            return
        send_status(message="Emergency stop reset; feeder remains stopped")
    except Exception as exception:
        send_status(client, error=str(exception))


def on_set_speed(client, data):
    try:
        percent = int(data.get("percent"))
    except (AttributeError, TypeError, ValueError):
        send_status(client, error="Speed must be an integer from 0 to 100")
        return

    if percent < 0 or percent > 100:
        send_status(client, error="Speed must be an integer from 0 to 100")
        return

    try:
        result = int(Bridge.call("feeder/setSpeed", percent))
        if result < 0:
            send_status(client, error="Speed was rejected by the MCU")
            return
        send_status(message="Speed updated")
    except Exception as exception:
        send_status(client, error=str(exception))


def wifi_led_heartbeat():
    """Keep the MCU Wi-Fi indicator synchronized with Linux networking."""
    previous_state = None
    while not stop_event.is_set():
        connected = network_route_available()
        try:
            Bridge.call("feeder/setWifiStatus", connected)
            if connected != previous_state:
                print(f"Wi-Fi LED: {'green/connected' if connected else 'red/disconnected'}")
                previous_state = connected
        except Exception as exception:
            print(f"Unable to update Wi-Fi LED: {exception}")
        stop_event.wait(2.0)


ui.on_message("get_status", on_get_status)
ui.on_message("start_feeder", on_start)
ui.on_message("stop_feeder", on_stop)
ui.on_message("emergency_stop", on_emergency_stop)
ui.on_message("reset_emergency_stop", on_reset_emergency_stop)
ui.on_message("set_speed", on_set_speed)

threading.Thread(target=wifi_led_heartbeat, daemon=True).start()

print("Continuous Feeder Web UI ready on port 7000")
App.run()
