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



