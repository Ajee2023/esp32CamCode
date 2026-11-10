# ESP32 Vision and Smart Waste Sorter Projects

A collection of hardware and software projects built around the ESP32 family: an ESP32-CAM image server, an ESP32-S3 smart waste sorter with an AI powered dashboard, an Arduino UNO Q motor feeder, and a drone inspection simulator for overflowing bins.

## What is in this repo

| Folder / file | What it is |
| --- | --- |
| `esp32CamCamera_2025.ino` | ESP32-CAM sketch that serves a JPEG at `/capture` over Wi-Fi |
| `CameraWebServer_copy_20260706225403/` | Stock Espressif CameraWebServer example (live stream and camera settings page) |
| `server.js`, `package.json` | Original Node.js vision server: grabs an image from the ESP32-CAM, labels it with Google Cloud Vision, and picks a bin |
| `hackathon 2027/` | The smart waste sorter: ESP32-S3 firmware, test sketches, and the web dashboard |
| `ContinuousFeederUNOQ/` | Forward only DC motor feeder on Arduino UNO Q with a web interface |
| `DroneSim/` (and `DroneSim.zip`) | Flask and AirSim drone simulator that dispatches drones to inspect smart bins |
| `test.py` | Minimal AirSim connection test (arm, take off, land) |
| `*.txt`, `*.err`, `*.log` | Serial monitor captures from hardware testing |

## 1. ESP32-CAM image server

`esp32CamCamera_2025.ino` starts a Wi-Fi web server on the ESP32-CAM. Open `/` for a status message or `/capture` to get the latest JPEG. The camera pin map is set for the AI Thinker ESP32-CAM.

**Setup**

1. Open the sketch in Arduino IDE with the ESP32 board package installed.
2. Enter your Wi-Fi name and password near the top of the file.
3. Select the AI Thinker ESP32-CAM board, upload, and open Serial Monitor at 115200 baud to read the IP address.

`CameraWebServer_copy_20260706225403/` is the standard Espressif example if you want a live stream and the full settings page. Pick your camera model in `board_config.h`.

## 2. Original vision server (`server.js`)

A Node.js (Express 5) app that:

1. Pulls a frame from the ESP32-CAM (`POST /capture`) or accepts an upload (`POST /upload`).
2. Sends it to Google Cloud Vision for labels.
3. Decides between a trash bin and a valuables bin (wire and cable labels go to valuables).
4. Tells the ESP32 to move the servo with `GET /sort?bin=...`.

```bash
npm install
export GOOGLE_APPLICATION_CREDENTIALS=/path/to/your-key.json
export ESP32_HOST=http://<esp32-cam-ip>/
npm start        # http://localhost:3000
```

Other routes: `/latest.json` and `/latest.jpg` return the most recent result.

## 3. Smart waste sorter (`hackathon 2027/`)

The sorter classifies an item as **Garbage**, **Recycle**, or **Organic**, shows progress on an LCD, spins a sorting wheel, and opens a servo lid.

```
Laptop camera (browser)
        |
        v
Dashboard (Node.js, port 3400) --> Gemini  (primary classifier)
        |                      --> Edge Impulse (backup when Gemini confidence is low)
        v
ESP32-S3 over Wi-Fi: /lcd, /motor, /lid
        |
        +--> 16x2 I2C LCD   +--> motor driver and wheel   +--> servo lid
```

### Subfolders

| Folder | Purpose |
| --- | --- |
| `WasteSorterDashboard/` | Node.js dashboard, classification logic, SQLite summary store |
| `ESP32S3_WebControl/` | Firmware the dashboard talks to (LCD, wheel motor, lid servo, voltage telemetry) |
| `esp32S3SmartWasteSorter/` | Earlier standalone sorter firmware (uses ArduinoJson and ESP32Servo) |
| `ESP32S3_SafeIdle/` | Boots with every output off, for safe first power up |
| `ESP32S3_LCD_SafeTest/` | LCD only test (tries I2C address 0x27, falls back to 0x3F) |
| `ESP32S3_MotorRotationSafeTest/` | Low risk motor rotation test |
| `app.ino` | Early sorter sketch |

### ESP32-S3 pin map (`ESP32S3_WebControl`)

| Function | GPIO |
| --- | --- |
| LCD SDA / SCL | 8 / 9 |
| Motor ENA / IN1 / IN2 / STBY | 5 / 6 / 7 / 4 |
| Lid servo | 10 |
| Voltage sense: motor supply / servo / logic | 1 / 2 / 3 |

### Firmware (PlatformIO)

Each firmware folder has a `platformio.ini` targeting `esp32-s3-devkitc-1` (Arduino framework, 115200 baud). The upload and monitor port is set to `COM4`, so change it to match your machine.

```bash
cd "hackathon 2027/ESP32S3_WebControl"
pio run -t upload
pio device monitor
```

Recommended bring up order: `ESP32S3_SafeIdle`, then `ESP32S3_LCD_SafeTest`, then `ESP32S3_MotorRotationSafeTest`, then `ESP32S3_WebControl`.

The firmware exposes these HTTP endpoints: `/status`, `/voltage` (also `/telemetry`), `/lcd`, `/lcd-reset`, `/motor` (also `/wheel`), `/motor-raw`, and `/lid`.

### Dashboard

```bash
cd "hackathon 2027/WasteSorterDashboard"
npm install
cp .env.example .env     # then fill in your keys
npm start                # http://localhost:3400
```

The browser asks for camera permission. `localhost` is allowed for camera access.

**Key settings** (full list in `.env.example`)

| Variable | Meaning |
| --- | --- |
| `GEMINI_API_KEY`, `GEMINI_MODEL` | Gemini access and model |
| `ESP32_S3_CONTROL_URL` | IP address of the ESP32-S3 |
| `CAMERA_SOURCE` | `browser` (laptop camera) or the ESP32-CAM capture URL |
| `EDGE_IMPULSE_API_KEY`, `EDGE_IMPULSE_PROJECT_ID` | Edge Impulse Studio access |
| `EDGE_IMPULSE_MODE` | `auto`, using an offline endpoint first when `EDGE_IMPULSE_OFFLINE_URL` is set |
| `EDGE_IMPULSE_CONFIDENCE_THRESHOLD` | Edge Impulse is only used when Gemini confidence is at or below this value (default 30) |
| `EDGE_IMPULSE_DECISION_POLICY` | `gemini-primary` or `require-agreement` |
| `WHEEL_*` | Wheel speed, nudge counts, and direction tuning |

**How decisions work**

- Gemini and Edge Impulse scores are stored separately and never averaged.
- If the two disagree, `gemini-primary` lets Gemini win and `require-agreement` stops sorting.
- Unknown results are held for manual review: the wheel does not turn and the lid stays shut.
- Non-sorter labels from Edge Impulse can be uploaded as unlabeled training samples for review.

**API routes**: `/api/config`, `/api/latest`, `/api/classifications`, `/api/telemetry`, `/api/events`, `/api/classify-upload`, `/api/run-classification` (old ESP32-CAM route), `/api/lcd`, `/api/motor`, `/api/motor-raw`, `/api/lid`.

## 4. Continuous feeder (`ContinuousFeederUNOQ/`)

A forward only, continuous DC gear motor controller for the Arduino UNO Q and a TB6612FNG driver. The STM32 side owns motor safety while the Linux side hosts the web page, linked by Arduino's Router Bridge.

```
Browser <-- Wi-Fi / HTTP --> main.py (Debian) <-- Bridge RPC --> .ino (STM32)
```

Wiring: D5 to PWMA, D7 to AIN1, D8 to AIN2, D9 to STBY, 3V3 to VCC, shared GND, and an external 6 to 12 V supply on VM. Never power the motor from an UNO Q pin.

Run it from Arduino App Lab (see `ContinuousFeederUNOQ/README.md` and `AppLabPackage/`), or test the interface on a laptop with `python main.py` and open `http://127.0.0.1:8000`, which uses a motor simulation. The motor stays off after boot, and the emergency stop latches until reset. A browser button is not a safety rated E-stop, so add a hardwired one for real use.

## 5. DroneSim (`DroneSim/`)

A Flask web app that uses Microsoft AirSim to simulate drones dispatched to smart bins that report overflow. Features include:

- Autonomous navigation with obstacle avoidance and no fly zones
- Priority scoring and truck dispatch history
- Inspection image pipeline with manual review
- Weather and wind simulation with feedback looping
- Natural language prompts converted into directions with an LLM
- Gamified progress

Run it with AirSim already open:

```bash
cd DroneSim
pip install flask airsim
python app.py
```

Tests live in `DroneSim/tests/` (`python -m pytest DroneSim/tests`). Check `drone_control.py` for the AirSim host setting. Future ideas are listed in `DroneSim/readme.md`: multi-drone support, live weather and address APIs, and weather aware rerouting.

## Security: fix before sharing this repo

Several secrets are committed in plain text. Remove them and rotate them before this goes anywhere public.

- `vision-credentials.json` is a Google Cloud service account key. Delete the key in Google Cloud Console and create a new one.
- `esp32CamCamera_2025.ino` and `CameraWebServer_copy_20260706225403/*.ino` contain Wi-Fi passwords. Change those passwords, then move credentials into a `secrets.h` file that is git ignored.
- Keep API keys in `.env` only. The dashboard already ignores `.env`.
- Add the root `.gitignore` entries below. The repo also carries `.pio/` build output, `__pycache__/`, log files, SQLite databases, and about 10 MB of zip duplicates that do not belong in version control.

```gitignore
.env
vision-credentials.json
secrets.h
node_modules/
.pio/
__pycache__/
*.pyc
*.log
*.err
*.sqlite
```

## Tech used

ESP32-CAM, ESP32-S3, Arduino UNO Q, PlatformIO, Arduino IDE, Node.js and Express, Python and Flask, AirSim, Gemini, Edge Impulse, Google Cloud Vision, SQLite.
