# Waste Sorter Dashboard

Local web dashboard for the ESP32-S3 smart waste sorter.

## What It Does

- Uses the laptop-connected HD camera from the browser.
- Sends the captured image to Gemini for `Garbage`, `Recycle`, or `Organic` classification.
- Displays progress in the browser flow.
- Sends progress and result text to the ESP32-S3 LCD controller.
- Opens the servo lid after classification.
- Keeps motor control locked until calibration is ready.

The old ESP32-CAM capture route still exists at `/api/run-classification`, but the web button now uses the laptop camera and `/api/classify-upload`.

## Start

```powershell
cd "C:\Users\gopin\Downloads\Hackthon_Projects\hackathon 2027\WasteSorterDashboard"
$env:GEMINI_API_KEY="your-key"
$env:CAMERA_SOURCE="browser"
$env:ESP32_S3_CONTROL_URL="http://192.168.137.154"
npm install
npm start
```

Open:

```text
http://localhost:3400
```

The browser may ask for permission to use the HD camera. `localhost` is allowed for browser camera access.

## Required ESP32-S3 Firmware

Upload the matching `ESP32S3_WebControl` PlatformIO project. It exposes `/lcd` and `/lid` for the dashboard and keeps motor output locked.
