# Waste Sorter Dashboard

Local web dashboard for the ESP32-S3 smart waste sorter.

## What It Does

- Captures a JPEG from the ESP32-CAM.
- Sends the image to Gemini for `Garbage`, `Recycle`, or `Organic` classification.
- Displays progress in the browser flow.
- Sends progress and result text to the ESP32-S3 LCD controller.
- Keeps motor and lid controls locked until calibration is ready.

## Start

```powershell
cd "C:\Users\gopin\Downloads\Hackthon_Projects\hackathon 2027\WasteSorterDashboard"
$env:GEMINI_API_KEY="your-key"
$env:CAMERA_CAPTURE_URL="http://192.168.137.156/capture"
$env:ESP32_S3_CONTROL_URL="http://192.168.137.108"
npm install
npm start
```

Open:

```text
http://localhost:3400
```

## Required ESP32-S3 Firmware

Upload the matching `ESP32S3_WebControl` PlatformIO project. It exposes `/lcd` for the dashboard and keeps motor/servo outputs off.
