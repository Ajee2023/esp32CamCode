# Arduino UNO Q Continuous Feeder

A forward-only, continuous DC gear-motor controller for Arduino UNO Q and the
TB6612FNG. The real-time STM32 MCU owns motor safety; the Debian MPU hosts the
responsive website over the UNO Q's built-in Wi-Fi. No sensors are used.

## Important UNO Q networking note

UNO Q is a dual-processor board. In the released **Arduino UNO Q Board 0.56.0**
package, the MCU core does not include `WiFi.h` or `WiFiServer`; Wi-Fi belongs to
the Linux MPU. A direct `WiFiServer` sketch therefore does not compile for this
board. This project uses Arduino's supported Router Bridge instead:

```text
Browser <--Wi-Fi/HTTP--> main.py on Debian <--Bridge RPC--> .ino on STM32 MCU
```

This still hosts the page entirely on the Arduino UNO Q and keeps HTTP work away
from deterministic motor control. It also creates the right boundary for later
camera, AI, database, MQTT, and WebSocket work.

## Folder structure

```text
ContinuousFeederUNOQ/
|-- ContinuousFeederUNOQ.ino  Motor, safety state, and Bridge RPC API
|-- main.py                   Wi-Fi HTTP server and API-to-Bridge adapter
|-- web/
|   |-- index.html            Responsive operator interface
|   |-- styles.css            Interface styling
|   `-- app.js                Fetch API controls and live status polling
`-- README.md                 Wiring, functions, setup, and expansion notes
```

## Wiring

| Arduino UNO Q | TB6612FNG | Purpose |
| --- | --- | --- |
| D5 | PWMA | Motor A PWM speed |
| D7 | AIN1 | Forward direction, HIGH while running |
| D8 | AIN2 | Forward direction, LOW while running |
| D9 | STBY | Driver enable, HIGH only while running |
| 3V3 | VCC | Driver logic supply |
| GND | GND | Shared logic and motor-supply ground |
| External 6-12 V positive | VM | Motor supply |
| Motor leads | AO1, AO2 | Motor output |

Connect external supply negative, UNO Q GND, and driver GND together. Never
power the motor from an UNO Q power pin. Size the supply, driver cooling,
wiring, and protection for the motor **stall current**. Fit the VM bulk
capacitor recommended by the TB6612FNG board or IC manufacturer. A full ASCII
diagram is at the top of the `.ino` file.

## Run with Arduino App Lab

1. Install current Arduino App Lab and connect/configure the UNO Q. During board
   setup, join it to the Wi-Fi network that operator devices will use.
2. Create a blank UNO Q App.
3. Use `ContinuousFeederUNOQ.ino` as the App's Arduino sketch and `main.py` as
   its Python program. Add the `web` folder beside `main.py` with all three web
   files unchanged.
4. Click **Run**. App Lab compiles/flashes the MCU sketch, deploys the Python
   program and assets, and starts both processors.
5. In the App Lab console, find
   `Continuous Feeder web server listening on http://0.0.0.0:8000`.
6. Find the UNO Q IP address in App Lab's board/network details, then open
   `http://<UNO-Q-IP>:8000` from a device on the same network.

The motor remains disabled after boot. Select a speed and press **Start**.
Stop writes PWM zero and disables STBY. Emergency Stop does the same and
latches the emergency state. **Reset E-stop** only clears the latch; it never
restarts the motor.

### Arduino IDE compile check

The MCU portion can also be verified in Arduino IDE 2.x:

1. Install **Arduino UNO Q Board** from Boards Manager.
2. Install **Arduino_RouterBridge** from Library Manager if App Lab has not
   already installed it.
3. Select **Arduino UNO Q**, open the `.ino`, and click **Verify**.

Running the complete web application is an App Lab workflow because `main.py`
runs on the UNO Q's Linux processor.

## Laptop simulation

To test the complete interface without an UNO Q, run this from the project
folder:

```powershell
python main.py
```

Then open `http://127.0.0.1:8000`. If the Arduino App Lab Python package is not
installed, `main.py` automatically uses an in-memory motor simulation and the
connection indicator reads **Simulation**. The real HTTP routes, validation,
status polling, Start/Stop behavior, speed mapping, and E-stop latch are used.
No GPIO changes or physical motor control occur in this mode.

## Operator controls

- **Start** sets AIN1 HIGH, AIN2 LOW, STBY HIGH, and applies selected PWM.
- **Stop** writes PWM zero first, disables STBY, and clears both direction pins.
- **Speed** accepts only integer percentages 0-100 and maps them to PWM 0-255.
- **Emergency Stop** removes drive and prevents Start until explicitly reset.
- **Connection status** uses one-second API polling and a request timeout.
  The website distinguishes `Wi-Fi Connected`, `Wi-Fi Disconnected`, and
  `Connection Lost` when the browser cannot reach the UNO Q.
- **UNO Q LED3** is green while the Linux Wi-Fi interface has an active link
  and red while Wi-Fi is disconnected or the web application has stopped.

A browser control is not a safety-rated physical E-stop. Where injury or damage
is possible, add a hard-wired, normally-closed emergency circuit that removes
motor energy independently of software, Wi-Fi, the MCU, and driver inputs.

## Function reference

### Arduino sketch

- `setupMotor()` preloads LOW values, configures output pins, writes PWM zero,
  and initializes the stopped state and default speed.
- `startMotor()` refuses a latched E-stop, selects forward drive, enables STBY,
  and immediately applies the requested speed.
- `stopMotor()` removes PWM, disables the bridge, clears direction inputs, and
  preserves the emergency latch when active.
- `setMotorSpeed()` clamps values to 0-100, maps them to 0-255, and applies PWM
  immediately only while running.
- `handleWebsite()` services pending MPU-to-MCU Bridge requests without parsing
  HTTP or waiting for a browser.
- `updateStatus()` uses `millis()` to print one-second diagnostics with no
  `delay()` calls.
- `setupWifiLed()` configures UNO Q RGB LED3 and starts it red while waiting
  for the Linux processor to confirm its wireless connection.
- `setWifiStatus()` is the Bridge callback that changes LED3 to green for a
  working Wi-Fi link or red when disconnected.
- `updateWifiLed()` turns LED3 red if the Linux heartbeat is missing for six
  seconds, preventing a stale connected indication after a service failure.
- `emergencyStopMotor()` removes drive and enters the latched emergency state.
- `resetEmergencyStop()` clears the latch into a stopped state only.
- `apiStartMotor()`, `apiStopMotor()`, `apiEmergencyStop()`,
  `apiResetEmergencyStop()`, `apiSetMotorSpeed()`, and `apiGetStatus()` are
  narrow RPC wrappers around motor ownership.
- `registerBridgeApi()` publishes those RPC methods to the Linux processor.
- `motorStateText()` converts the strongly typed state to display/API text.

### Python server

- `bridge_call()` serializes RPC calls from simultaneous browser requests.
- `feeder_status()` reads and parses the MCU's authoritative state.
- `wifi_link_is_up()` checks the UNO Q Linux wireless interface and carrier.
- `wifi_led_reporter()` reports link changes to the MCU every two seconds.
- `FeederRequestHandler` serves only known assets and explicit `/api/*` routes.
- `_set_speed()` validates the percentage again before calling the MCU.
- `_send_bridge_status()` and `_send_status_with_message()` build status JSON.
- `_send_json()` and `_send_bytes()` produce bounded HTTP responses with exact
  content lengths, no-cache behavior, and MIME sniffing disabled.
- `main()` binds the server to port 8000 and shuts it down cleanly.

## HTTP API

| Method | Path | Effect |
| --- | --- | --- |
| GET | `/api/status` | State, percent, PWM, and connection JSON |
| POST | `/api/start` | Start forward continuous feed |
| POST | `/api/stop` | Normal stop |
| POST | `/api/speed?percent=0..100` | Change requested speed immediately |
| POST | `/api/emergency-stop` | Stop and latch E-stop |
| POST | `/api/reset-estop` | Clear E-stop and remain stopped |

## Expansion design

Motor control, HTTP, and presentation are separate. Camera detection, AI,
database logging, MQTT, and WebSocket services belong in new Linux-side modules
and can call the same Bridge API. Time-critical counters, conveyor interlocks,
and sorting servo schedules belong in non-blocking MCU `update...()` functions
called from `loop()`. Add new HTTP routes without changing the TB6612FNG layer.

Current network-loss policy intentionally leaves a running feeder running, as
required for continuous operation. The page reports the loss. For unattended
machinery, add a configurable command watchdog if site policy requires the
feeder to stop when the web service or network disappears.

## Safety behavior

- Startup/reset: PWM 0, STBY LOW, AIN1 LOW, AIN2 LOW.
- Invalid speed: rejected in Python and rejected/clamped again on the MCU.
- Normal shutdown: PWM removed before driver standby.
- Emergency shutdown: same electrical sequence plus a software latch.
- E-stop reset: clears the latch but never causes automatic restart.
- Main loop: no `delay()` calls; the motor runs indefinitely in hardware PWM.
