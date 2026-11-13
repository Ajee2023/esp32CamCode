# Sorter

A smart waste sorting prototype for Hackathon 2026. A browser camera captures an item, a Node.js server classifies it, and an ESP32-S3 controls the sorting wheel, lid servo, and LCD.

## What it does

- Captures an item using the computer's camera from the browser dashboard.
- Uses Gemini to classify the item as **Garbage**, **Recycle**, or **Organic**.
- Can consult Edge Impulse for low-confidence results, using a configured local endpoint or the Studio API.
- Shows classification progress and results on the dashboard and sends status text to the ESP32-S3 LCD.
- Coordinates wheel movement and lid opening for supported sorting results.
- Holds unknown items for manual review without rotating the wheel or opening the lid.
- Records classification summaries in SQLite, including separate model confidence scores.
- Provides manual hardware controls and voltage telemetry for setup and troubleshooting.

## How it works

1. The operator places an item in view and starts classification.
2. The browser uploads an image to `WasteSorterDashboard/server.mjs`.
3. The server requests a Gemini result and optionally checks Edge Impulse according to the confidence and decision settings.
4. For a supported result, the dashboard coordinates the ESP32-S3 wheel and lid controls and updates the LCD.
5. The result is added to the classification summary. Unknown results wait for operator review.

The normal browser flow uses the computer camera. An older ESP32-CAM capture route is also present in the server.

## Folder guide

| File or folder | Purpose |
| --- | --- |
| `WasteSorterDashboard/` | Node.js/Express server, browser UI, classification integrations, and history |
| `ESP32S3_WebControl/` | Firmware paired with the dashboard; exposes LCD, motor, lid, and telemetry HTTP routes |
| `esp32S3SmartWasteSorter/` | Separate integrated sorter firmware variant |
| `ESP32S3_A4988_ComponentTest/` | LCD, servo, and A4988 stepper component tests |
| `ESP32S3_LCD_SafeTest/` | Focused LCD test firmware |
| `ESP32S3_MotorRotationSafeTest/` | Focused motor rotation test firmware |
| `ESP32S3_SafeIdle/` | Idle firmware for keeping hardware outputs in a stopped state |

These firmware projects are alternatives for different wiring and test stages. Use the pin assignments and driver type from the firmware you actually upload.

## Starting the dashboard

Upload the `ESP32S3_WebControl` PlatformIO project to the matching ESP32-S3 hardware and configure its Wi-Fi settings for your network. From this folder, run:

```powershell
cd WasteSorterDashboard
$env:GEMINI_API_KEY = "your-gemini-api-key"
$env:CAMERA_SOURCE = "browser"
$env:ESP32_S3_CONTROL_URL = "http://<ESP32-S3-IP>"
npm install
npm start
```

Open `http://localhost:3400` and allow browser camera access. The server uses the Node.js SQLite API, so the installed Node.js version must support `node:sqlite`.

For optional Edge Impulse validation, configure `EDGE_IMPULSE_API_KEY` and `EDGE_IMPULSE_PROJECT_ID`, or set `EDGE_IMPULSE_OFFLINE_URL` for a local classifier. The default confidence threshold is 30; model disagreement handling is controlled by `EDGE_IMPULSE_DECISION_POLICY`.

See [the dashboard README](WasteSorterDashboard/README.md) for classification settings and [the A4988 component test README](ESP32S3_A4988_ComponentTest/README.md) for that specific wiring variant. Run the dashboard commands from the folder shown above; older nested documentation may contain a previous absolute project path.

## Hardware and demo limits

The prototype uses an ESP32-S3, a motor and matching driver for the sorting wheel, a lid servo, an I2C LCD, and a camera source. Wheel timing depends on the assembled mechanism and needs calibration before automatic sorting. Image classification can be uncertain, which is why unknown results remain available for review.
