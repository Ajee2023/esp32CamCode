#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

constexpr uint32_t SERIAL_BAUD = 115200;

constexpr uint8_t LCD_I2C_ADDRESS = 0x27;
constexpr uint8_t LCD_COLUMNS = 16;
constexpr uint8_t LCD_ROWS = 2;
constexpr uint8_t LCD_SDA_PIN = 8;
constexpr uint8_t LCD_SCL_PIN = 9;

// Reuses the old motor-control signal pins for A4988.
// A4988 ENABLE is active LOW: HIGH = disabled/safe, LOW = enabled.
constexpr uint8_t STEPPER_STEP_PIN = 5;
constexpr uint8_t STEPPER_DIR_PIN = 6;
constexpr uint8_t STEPPER_ENABLE_PIN = 7;
constexpr bool STEPPER_ENABLE_ACTIVE_LOW = true;

constexpr uint8_t SERVO_PIN = 10;
constexpr uint8_t SERVO_PWM_CHANNEL = 1;
constexpr uint16_t SERVO_PWM_FREQUENCY = 50;
constexpr uint8_t SERVO_PWM_RESOLUTION_BITS = 14;
constexpr uint16_t SERVO_MIN_PULSE_US = 500;
constexpr uint16_t SERVO_MAX_PULSE_US = 2400;
constexpr uint8_t SERVO_CLOSED_DEGREES = 0;
constexpr uint8_t SERVO_OPEN_DEGREES = 90;
constexpr uint16_t SERVO_OPEN_HOLD_MS = 1200;

constexpr uint16_t SAFE_TEST_STEPS = 25;
constexpr uint16_t LONG_TEST_STEPS = 400;
constexpr uint16_t STEP_PULSE_HIGH_US = 4;
constexpr uint16_t STEP_INTERVAL_US = 12000;  // Larger number = slower stepper movement.
constexpr uint16_t BETWEEN_TEST_DELAY_MS = 1000;

LiquidCrystal_I2C lcd(LCD_I2C_ADDRESS, LCD_COLUMNS, LCD_ROWS);
String serialLine;

void showLcd(const String &line1, const String &line2) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(line1.substring(0, LCD_COLUMNS));
  lcd.setCursor(0, 1);
  lcd.print(line2.substring(0, LCD_COLUMNS));
}

void printHelp() {
  Serial.println();
  Serial.println(F("A4988 Component Test Commands:"));
  Serial.println(F("  help       - show this list"));
  Serial.println(F("  lcd        - scan I2C and refresh LCD"));
  Serial.println(F("  servo      - open servo to 90, then close to 0"));
  Serial.println(F("  enable     - enable A4988 outputs"));
  Serial.println(F("  disable    - disable A4988 outputs"));
  Serial.println(F("  cw         - 25 slow clockwise steps"));
  Serial.println(F("  ccw        - 25 slow counter-clockwise steps"));
  Serial.println(F("  longcw     - 400 very slow clockwise steps"));
  Serial.println(F("  longccw    - 400 very slow counter-clockwise steps"));
  Serial.println(F("  both       - 25 CW, pause, 25 CCW"));
  Serial.println(F("  off        - disable A4988 and keep STEP LOW"));
  Serial.println();
  Serial.println(F("Nothing moves automatically at boot."));
}

void safeStepperOff() {
  digitalWrite(STEPPER_STEP_PIN, LOW);
  digitalWrite(STEPPER_DIR_PIN, LOW);
  digitalWrite(STEPPER_ENABLE_PIN, STEPPER_ENABLE_ACTIVE_LOW ? HIGH : LOW);
}

void enableStepper() {
  digitalWrite(STEPPER_ENABLE_PIN, STEPPER_ENABLE_ACTIVE_LOW ? LOW : HIGH);
}

void disableStepper() {
  safeStepperOff();
}

void scanI2c() {
  byte count = 0;
  Serial.println(F("Scanning I2C bus..."));
  for (byte address = 1; address < 127; address++) {
    Wire.beginTransmission(address);
    if (Wire.endTransmission() == 0) {
      Serial.print(F("I2C device found at 0x"));
      if (address < 16) {
        Serial.print('0');
      }
      Serial.println(address, HEX);
      count++;
      delay(5);
    }
  }
  if (count == 0) {
    Serial.println(F("No I2C devices found. Check SDA=8, SCL=9, VCC, GND."));
    showLcd("I2C scan", "No devices");
  } else {
    Serial.print(F("I2C devices found: "));
    Serial.println(count);
    showLcd("LCD OK", "Addr 0x27?");
  }
}

uint32_t servoDutyForDegrees(uint8_t degrees) {
  const uint16_t pulseUs = map(degrees, 0, 180, SERVO_MIN_PULSE_US, SERVO_MAX_PULSE_US);
  const uint32_t maxDuty = (1UL << SERVO_PWM_RESOLUTION_BITS) - 1;
  return (uint32_t)pulseUs * maxDuty / 20000UL;
}

void writeServoDegrees(uint8_t degrees) {
  ledcWrite(SERVO_PWM_CHANNEL, servoDutyForDegrees(degrees));
}

void testServo() {
  Serial.println(F("Servo test: open 90 degrees."));
  showLcd("Servo Test", "Open 90");
  writeServoDegrees(SERVO_OPEN_DEGREES);
  delay(SERVO_OPEN_HOLD_MS);
  Serial.println(F("Servo test: close 0 degrees."));
  showLcd("Servo Test", "Close 0");
  writeServoDegrees(SERVO_CLOSED_DEGREES);
  delay(500);
  showLcd("Servo Test", "Complete");
}

void stepMotor(bool clockwise, uint16_t steps, const String &label) {
  Serial.print(label);
  Serial.print(F(": enabling A4988, steps="));
  Serial.println(steps);
  showLcd("Stepper", label);

  digitalWrite(STEPPER_DIR_PIN, clockwise ? HIGH : LOW);
  enableStepper();
  delay(20);

  for (uint16_t i = 0; i < steps; i++) {
    digitalWrite(STEPPER_STEP_PIN, HIGH);
    delayMicroseconds(STEP_PULSE_HIGH_US);
    digitalWrite(STEPPER_STEP_PIN, LOW);
    delayMicroseconds(STEP_INTERVAL_US);
  }

  disableStepper();
  Serial.println(F("Stepper disabled after move."));
  showLcd("Stepper", "Disabled");
}

String normalizeCommand(String command) {
  command.trim();
  command.toLowerCase();
  return command;
}

void handleCommand(const String &command) {
  if (command.length() == 0) {
    return;
  }

  Serial.print(F("> "));
  Serial.println(command);

  if (command == "help" || command == "?") {
    printHelp();
  } else if (command == "lcd") {
    scanI2c();
  } else if (command == "servo") {
    testServo();
  } else if (command == "enable") {
    enableStepper();
    Serial.println(F("A4988 enabled. Use only briefly for test; driver may get warm."));
    showLcd("Stepper", "Enabled");
  } else if (command == "disable" || command == "off") {
    disableStepper();
    Serial.println(F("A4988 disabled."));
    showLcd("Stepper", "Disabled");
  } else if (command == "cw") {
    stepMotor(true, SAFE_TEST_STEPS, "CW 25");
  } else if (command == "ccw") {
    stepMotor(false, SAFE_TEST_STEPS, "CCW 25");
  } else if (command == "longcw") {
    stepMotor(true, LONG_TEST_STEPS, "CW 400 slow");
  } else if (command == "longccw") {
    stepMotor(false, LONG_TEST_STEPS, "CCW 400 slow");
  } else if (command == "both") {
    stepMotor(true, SAFE_TEST_STEPS, "CW 25");
    delay(BETWEEN_TEST_DELAY_MS);
    stepMotor(false, SAFE_TEST_STEPS, "CCW 25");
  } else {
    Serial.println(F("Unknown command. Type help."));
    showLcd("Unknown cmd", command);
  }
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(500);

  pinMode(STEPPER_STEP_PIN, OUTPUT);
  pinMode(STEPPER_DIR_PIN, OUTPUT);
  pinMode(STEPPER_ENABLE_PIN, OUTPUT);
  safeStepperOff();

  Wire.begin(LCD_SDA_PIN, LCD_SCL_PIN);
  lcd.init();
  lcd.backlight();

  ledcSetup(SERVO_PWM_CHANNEL, SERVO_PWM_FREQUENCY, SERVO_PWM_RESOLUTION_BITS);
  ledcAttachPin(SERVO_PIN, SERVO_PWM_CHANNEL);
  writeServoDegrees(SERVO_CLOSED_DEGREES);

  Serial.println(F("ESP32-S3 A4988 component test starting."));
  Serial.println(F("Stepper is disabled at boot. Type help for commands."));
  showLcd("A4988 Test", "Type help");
  scanI2c();
  printHelp();
}

void loop() {
  while (Serial.available() > 0) {
    char c = (char)Serial.read();
    if (c == '\n' || c == '\r') {
      String command = normalizeCommand(serialLine);
      serialLine = "";
      handleCommand(command);
    } else {
      serialLine += c;
      if (serialLine.length() > 48) {
        serialLine = "";
        Serial.println(F("Command too long; cleared."));
      }
    }
  }
}