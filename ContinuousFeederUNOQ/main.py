"""UNO Q Linux-side web server for the continuous feeder."""

import json
import mimetypes
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

try:
    from arduino.app_utils import Bridge
    SIMULATION_MODE = False
except ImportError:
    SIMULATION_MODE = True

    class LocalMockBridge:
        """Laptop-only simulation of the MCU's authoritative motor state."""

        def __init__(self):
            self.state = "Stopped"
            self.percent = 50
            self.pwm = 0
            self.wifi_connected = True

        def call(self, method, *args):
            if method == "feeder/status":
                return json.dumps(self._status())
            if method == "feeder/start":
                if self.state == "Emergency Stopped":
                    return False
                self.state = "Running"
                self._apply_pwm()
                return True
            if method == "feeder/stop":
                if self.state != "Emergency Stopped":
                    self.state = "Stopped"
                self.pwm = 0
                return True
            if method == "feeder/emergencyStop":
                self.state = "Emergency Stopped"
                self.pwm = 0
                return True
            if method == "feeder/resetEmergencyStop":
                if self.state != "Emergency Stopped":
                    return False
                self.state = "Stopped"
                self.pwm = 0
                return True
            if method == "feeder/setSpeed":
                percent = int(args[0])
                if percent < 0 or percent > 100:
                    return -1
                self.percent = percent
                self._apply_pwm()
                return self.pwm
            if method == "feeder/setWifiStatus":
                self.wifi_connected = bool(args[0])
                return self.wifi_connected
            raise ValueError(f"Unknown simulated Bridge method: {method}")

        def _apply_pwm(self):
            self.pwm = round(self.percent * 255 / 100) if self.state == "Running" else 0

        def _status(self):
            return {
                "state": self.state,
                "running": self.state == "Running",
                "emergencyStopped": self.state == "Emergency Stopped",
                "percent": self.percent,
                "pwm": self.pwm,
            }

    Bridge = LocalMockBridge()


HOST = "0.0.0.0"
PORT = 8000
WEB_ROOT = Path(__file__).resolve().parent / "web"
BRIDGE_LOCK = threading.Lock()
STOP_EVENT = threading.Event()

ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/styles.css": ("styles.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
}


def bridge_call(method, *args):
    """Serialize Bridge access so simultaneous browsers cannot race RPC."""
    with BRIDGE_LOCK:
        return Bridge.call(method, *args)


def feeder_status():
    raw = bridge_call("feeder/status")
    if isinstance(raw, bytes):
        raw = raw.decode("utf-8")
    return json.loads(str(raw))


def wifi_link_is_up():
    """Return True when a Linux wireless interface has an active carrier."""
    if SIMULATION_MODE:
        return True

    network_root = Path("/sys/class/net")
    try:
        wireless_interfaces = [
            interface
            for interface in network_root.iterdir()
            if interface.name.startswith(("wl", "wlan"))
        ]
        for interface in wireless_interfaces:
            operstate = (interface / "operstate").read_text(encoding="ascii").strip()
            carrier_file = interface / "carrier"
            carrier = (
                carrier_file.read_text(encoding="ascii").strip()
                if carrier_file.exists()
                else "1"
            )
            if operstate == "up" and carrier == "1":
                return True
    except OSError as error:
        print(f"Unable to inspect Wi-Fi interface: {error}")
    return False


def wifi_led_reporter():
    """Refresh the MCU RGB LED without blocking the HTTP server."""
    previous_state = None
    while not STOP_EVENT.is_set():
        connected = wifi_link_is_up()
        try:
            bridge_call("feeder/setWifiStatus", connected)
            if connected != previous_state:
                print(f"Wi-Fi LED: {'green/connected' if connected else 'red/disconnected'}")
                previous_state = connected
        except Exception as error:
            print(f"Unable to update Wi-Fi LED: {error}")
        STOP_EVENT.wait(2.0)


class FeederRequestHandler(BaseHTTPRequestHandler):
    server_version = "UNOQFeeder/1.0"

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/status":
            self._send_bridge_status()
            return

        asset = ASSETS.get(parsed.path)
        if asset is None:
            self._send_json(404, {"message": "Not found"})
            return

        filename, content_type = asset
        try:
            body = (WEB_ROOT / filename).read_bytes()
        except OSError:
            self._send_json(500, {"message": "Web asset is unavailable"})
            return
        self._send_bytes(200, body, content_type)

    def do_POST(self):
        parsed = urlparse(self.path)
        routes = {
            "/api/start": ("feeder/start", "Feeder started"),
            "/api/stop": ("feeder/stop", "Feeder stopped"),
            "/api/emergency-stop": (
                "feeder/emergencyStop",
                "Emergency stop latched",
            ),
            "/api/reset-estop": (
                "feeder/resetEmergencyStop",
                "Emergency stop reset; feeder remains stopped",
            ),
        }

        if parsed.path == "/api/speed":
            self._set_speed(parse_qs(parsed.query))
            return

        route = routes.get(parsed.path)
        if route is None:
            self._send_json(404, {"message": "Not found"})
            return

        method, message = route
        try:
            accepted = bool(bridge_call(method))
            if not accepted:
                failure = (
                    "Reset emergency stop before starting"
                    if parsed.path == "/api/start"
                    else "Emergency stop is not active"
                )
                self._send_status_with_message(409, failure)
                return
            self._send_status_with_message(200, message)
        except Exception as error:
            self._bridge_error(error)

    def _set_speed(self, query):
        values = query.get("percent", [])
        try:
            percent = int(values[0])
        except (IndexError, TypeError, ValueError):
            self._send_json(400, {"message": "Speed must be an integer from 0 to 100"})
            return

        if percent < 0 or percent > 100:
            self._send_json(400, {"message": "Speed must be an integer from 0 to 100"})
            return

        try:
            result = int(bridge_call("feeder/setSpeed", percent))
            if result < 0:
                self._send_json(400, {"message": "Speed was rejected by the MCU"})
                return
            self._send_status_with_message(200, "Speed updated")
        except Exception as error:
            self._bridge_error(error)

    def _send_bridge_status(self):
        try:
            status = feeder_status()
            status["wifiConnected"] = wifi_link_is_up()
            status["simulationMode"] = SIMULATION_MODE
            self._send_json(200, status)
        except Exception as error:
            self._bridge_error(error)

    def _send_status_with_message(self, code, message):
        status = feeder_status()
        status["wifiConnected"] = wifi_link_is_up()
        status["simulationMode"] = SIMULATION_MODE
        status["message"] = message
        self._send_json(code, status)

    def _bridge_error(self, error):
        print(f"Bridge error: {error}")
        self._send_json(503, {"message": "Motor controller is unavailable"})

    def _send_json(self, code, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self._send_bytes(code, body, "application/json; charset=utf-8")

    def _send_bytes(self, code, body, content_type):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format_string, *args):
        print(f"{self.client_address[0]} - {format_string % args}")


def main():
    mimetypes.add_type("application/javascript", ".js")
    server = ThreadingHTTPServer((HOST, PORT), FeederRequestHandler)
    if SIMULATION_MODE:
        print("SIMULATION MODE: no Arduino or physical motor is connected")
    print(f"Continuous Feeder web server listening on http://{HOST}:{PORT}")
    led_thread = threading.Thread(target=wifi_led_reporter, daemon=True)
    led_thread.start()
    try:
        server.serve_forever(poll_interval=0.25)
    except KeyboardInterrupt:
        pass
    finally:
        STOP_EVENT.set()
        try:
            bridge_call("feeder/setWifiStatus", False)
        except Exception:
            pass
        server.server_close()


if __name__ == "__main__":
    main()
