# Drone Sim

A browser-based drone simulation and waste collection planning project for Hackathon 2026. It connects a Flask web application to an AirSim multirotor simulator so an operator can fly to map destinations, survey an area, inspect waste bins, and organize collection requests.

## What it does

- Controls simulated drone takeoff and navigation, with live position and flight progress.
- Supports manual destinations and zone survey missions.
- Attempts obstacle recovery and detours in the simulator, and validates planned routes against configurable no-fly zones.
- Lets the operator simulate rain, wind, and snow and adjust the demo speed. A feedback loop evaluates conditions and updates mission behavior.
- Tracks waste bins, overflow information, inspection images, and manual review status.
- Scores collection priorities using factors such as overflow, waiting time, location importance, surrounding garbage, and inspection confidence.
- Supports truck assignment, dispatch progress, and collection history.
- Stores no-fly zones, inspection records, and dispatch data in SQLite.

## How it works

The map interface sends commands to `app.py`. The Flask backend validates the request and uses `drone_control.py` to communicate with AirSim. Separate modules manage restricted areas, inspection imagery, and collection priority.

Inspection imagery can use Mapillary street images when `MAPILLARY_ACCESS_TOKEN` is configured; otherwise the inspection pipeline uses its simulation provider. These images and simulated inspection results are for demonstrating the workflow and should not be treated as live aerial observations or verified waste detection.

## Main files

| File or folder | Purpose |
| --- | --- |
| `app.py` | Flask routes, mission state, bin management, and dispatch workflow |
| `drone_control.py` | AirSim connection, movement, position, weather, and flight progress |
| `no_fly_zones.py` | Restricted-zone storage and route validation |
| `inspection_pipeline.py` | Inspection image providers, storage, and review workflow |
| `priority_service.py` | Collection urgency scores and dispatch ordering |
| `templates/index.html` | Browser dashboard and map interface |
| `data/` | SQLite database and inspection assets |
| `tests/` | Existing tests for routes, inspections, priority, and dispatch |

## Running the demo

Use a Python environment with Flask and the AirSim Python client installed. A running AirSim multirotor environment with its RPC API available on port `41451` is required for flight controls. Connection settings are in `drone_control.py`.

From this folder, start the web application:

```powershell
python app.py
```

Open `http://127.0.0.1:5000`. Start the simulator and enter its running/Play mode before issuing flight commands. The GPS-to-simulator mapping uses a fixed home reference in the code, so displayed map coordinates are an approximate demo mapping.

## Original presentation notes

The notes below are retained from the original README. They include earlier demo ideas and future plans; the project overview above describes the current code.

Slide 1
- Problem statement: Drone Project, AI Integration, Feedback looping, Gamifying
- Hardward-In-The-Loop
- Software-In-The-Loop
- Human-In-The-Loop

Slide 2
Challenges faced and approaches taken to overcome:
- Unable to establish AirSim API connectivity ...
- Unable to avoid obstecles after a certain altitude...
- 500 Status code connecting to Open AI...

Slide 3
Functionalities introduced:
- Autonomous navigation avoiding obstecles
- Feedback looping capabilities
    - During wind gust
    - During rainy conditions
- Interactive progress
- Gamifying
- Prompting address
- Normalizing prompt to directions using LLM

Slide 4
- Future considerations for practical use:
    - Expand to multi-drone running concurrently.
    - Embedd AirSim view under single page for ease of maneuvering.
    - Integrating to weather api for real-time weather forcast.
    - Integrating to google api for address lookup.
    - Alternate path re-routing where weather permitting. 
- Demo
    - Point A (start) to B (target):
        - Drag and drop to target destination.
        - Prompting Address.
    - Obstecle re-routing.
    - Simulate weather condition, show case safety measures.
    - Gamifying.




