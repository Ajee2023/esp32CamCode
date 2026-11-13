# ESP32-S3 A4988 Component Test

Safe command-driven wiring test for LCD, servo, and A4988 stepper driver.

## Pins

ESP32-S3 to LCD:
- GPIO 8 -> LCD SDA
- GPIO 9 -> LCD SCL
- 3.3V or 5V -> LCD VCC, depending your LCD backpack
- GND -> LCD GND

ESP32-S3 to servo:
- GPIO 10 -> servo signal
- External 5V -> servo V+
- GND common with ESP32-S3

ESP32-S3 to A4988:
- GPIO 5 -> STEP
- GPIO 6 -> DIR
- GPIO 7 -> ENABLE
- 3.3V -> VDD
- GND -> GND near VDD
- RESET and SLEEP tied together, then to 3.3V
- MS1/MS2/MS3 can be left unconnected for full-step mode

Motor supply to A4988:
- External motor supply + -> VMOT
- External motor supply - -> GND near VMOT
- Add a large capacitor across VMOT/GND near driver, commonly 47uF or larger
- Motor coil A -> 1A/1B
- Motor coil B -> 2A/2B

Important: ESP32-S3 GND, A4988 logic GND, and motor-supply GND must be common.

## Serial commands

Open Serial Monitor at 115200 baud.

- `lcd` checks I2C LCD
- `servo` opens then closes lid servo
- `cw` moves 25 slow clockwise steps
- `ccw` moves 25 slow counter-clockwise steps
- `both` moves 25 CW then 25 CCW
- `longcw` moves 100 slow clockwise steps
- `longccw` moves 100 slow counter-clockwise steps
- `off` disables the A4988 outputs

The stepper never moves automatically at boot.