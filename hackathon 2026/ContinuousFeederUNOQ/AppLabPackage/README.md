# Continuous Feeder App Lab Package

Import this folder as an Arduino App Lab project, or reproduce this exact
structure in a blank App. The `arduino:web_ui` Brick serves `assets/` on port
7000 and carries commands over WebSocket to `python/main.py`. The Python backend
uses Router Bridge RPC to control the MCU sketch.

After App Lab reports the App is running, open:

```text
http://<UNO-Q-IP>:7000
```

Keep motor power disconnected until the page reports `Stopped` and PWM `0`.
