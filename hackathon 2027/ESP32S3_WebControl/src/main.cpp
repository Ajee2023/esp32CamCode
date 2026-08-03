#include <Arduino.h>
#include <WiFi.h>
#include <WebServer.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

constexpr uint32_t SERIAL_BAUD = 115200;

const char *const WIFI_SSID = "LAPTOP-3M5IFCCV 9274";
const char *const WIFI_PASSWORD = "iR921)16";

constexpr uint8_t LCD_I2C_ADDRESS = 0x27;
constexpr uint8_t LCD_COLUMNS = 16;
constexpr uint8_t LCD_ROWS = 2;
constexpr uint8_t LCD_SDA_PIN = 8;
constexpr uint8_t LCD_SCL_PIN = 9;

constexpr uint8_t MOTOR_ENA_PIN = 5;
constexpr uint8_t MOTOR_IN1_PIN = 6;
constexpr uint8_t MOTOR_IN2_PIN = 7;
constexpr uint8_t MOTOR_STBY_PIN = 4;
constexpr uint8_t SERVO_PIN = 10;

constexpr uint8_t MOTOR_PWM_CHANNEL = 0;
constexpr uint16_t MOTOR_PWM_FREQUENCY_HZ = 1000;
constexpr uint8_t MOTOR_PWM_RESOLUTION_BITS = 8;
constexpr uint8_t MOTOR_DEFAULT_SPEED = 255;
constexpr uint8_t MOTOR_MAX_SPEED = 255;
constexpr uint16_t MOTOR_DEFAULT_RUN_MS = 800;
constexpr uint16_t MOTOR_MAX_RUN_MS = 5000;
constexpr uint16_t MOTOR_RAW_MIN_RUN_MS = 30;
constexpr uint8_t MOTOR_START_BOOST_SPEED = 255;
constexpr uint8_t MOTOR_START_BOOST_THRESHOLD = 245;
constexpr uint16_t MOTOR_START_BOOST_MS = 350;

constexpr uint8_t VOLTAGE_VM_PIN = 1;
constexpr uint8_t VOLTAGE_SERVO_PIN = 2;
constexpr uint8_t VOLTAGE_LOGIC_PIN = 3;
constexpr float VOLTAGE_DIVIDER_RATIO = 4.0f;  // 30k top resistor, 10k bottom resistor.
constexpr uint8_t VOLTAGE_SAMPLE_COUNT = 16;
constexpr uint16_t VOLTAGE_ADC_SATURATED_MV = 3000;

constexpr uint8_t SERVO_PWM_CHANNEL = 1;
constexpr uint16_t SERVO_PWM_FREQUENCY = 50;
constexpr uint8_t SERVO_PWM_RESOLUTION_BITS = 14;
constexpr uint16_t SERVO_MIN_PULSE_US = 500;
constexpr uint16_t SERVO_MAX_PULSE_US = 2400;
constexpr uint8_t SERVO_CLOSED_DEGREES = 0;
constexpr uint8_t SERVO_OPEN_DEGREES = 90;
constexpr uint16_t SERVO_OPEN_HOLD_MS = 2000;
constexpr uint16_t SERVO_SETTLE_MS = 500;

LiquidCrystal_I2C lcd(LCD_I2C_ADDRESS, LCD_COLUMNS, LCD_ROWS);
WebServer server(80);

String currentLine1 = "Web Control";
String currentLine2 = "Ready";
bool motorRunning = false;
bool motorPwmAttached = false;
uint32_t motorStopAt = 0;
uint32_t motorBoostUntil = 0;
bool motorBoostActive = false;
uint16_t activeMotorDurationMs = 0;
uint8_t activeMotorSpeed = 0;
String activeMotorDirection = "stop";

void showLcd(const String &line1, const String &line2);

void attachMotorPwm() {
  if (!motorPwmAttached) {
    ledcAttachPin(MOTOR_ENA_PIN, MOTOR_PWM_CHANNEL);
    motorPwmAttached = true;
  }
}

void detachMotorPwm() {
  if (motorPwmAttached) {
    ledcWrite(MOTOR_PWM_CHANNEL, 0);
    ledcDetachPin(MOTOR_ENA_PIN);
    motorPwmAttached = false;
  }
  pinMode(MOTOR_ENA_PIN, OUTPUT);
  digitalWrite(MOTOR_ENA_PIN, LOW);
}

void motorOff() {
  motorRunning = false;
  activeMotorDirection = "stop";
  motorBoostActive = false;
  motorBoostUntil = 0;
  activeMotorDurationMs = 0;
  activeMotorSpeed = 0;
  if (motorPwmAttached) {
    ledcWrite(MOTOR_PWM_CHANNEL, 0);
  } else {
    digitalWrite(MOTOR_ENA_PIN, LOW);
  }
  digitalWrite(MOTOR_IN1_PIN, LOW);
  digitalWrite(MOTOR_IN2_PIN, LOW);
  digitalWrite(MOTOR_STBY_PIN, LOW);
}

void setupMotor() {
  pinMode(MOTOR_ENA_PIN, OUTPUT);
  pinMode(MOTOR_IN1_PIN, OUTPUT);
  pinMode(MOTOR_IN2_PIN, OUTPUT);
  pinMode(MOTOR_STBY_PIN, OUTPUT);

  digitalWrite(MOTOR_ENA_PIN, LOW);
  digitalWrite(MOTOR_IN1_PIN, LOW);
  digitalWrite(MOTOR_IN2_PIN, LOW);
  digitalWrite(MOTOR_STBY_PIN, LOW);

  ledcSetup(MOTOR_PWM_CHANNEL, MOTOR_PWM_FREQUENCY_HZ, MOTOR_PWM_RESOLUTION_BITS);
  attachMotorPwm();
  motorOff();
}

void motorForward(uint8_t speed) {
  attachMotorPwm();
  digitalWrite(MOTOR_STBY_PIN, HIGH);
  digitalWrite(MOTOR_IN1_PIN, HIGH);
  digitalWrite(MOTOR_IN2_PIN, LOW);
  ledcWrite(MOTOR_PWM_CHANNEL, speed);
}

void motorReverse(uint8_t speed) {
  attachMotorPwm();
  digitalWrite(MOTOR_STBY_PIN, HIGH);
  digitalWrite(MOTOR_IN1_PIN, LOW);
  digitalWrite(MOTOR_IN2_PIN, HIGH);
  ledcWrite(MOTOR_PWM_CHANNEL, speed);
}

void runMotorPulse(const String &direction, uint16_t durationMs, uint8_t speed) {
  speed = constrain(speed, 0, MOTOR_MAX_SPEED);
  durationMs = constrain(durationMs, 100, MOTOR_MAX_RUN_MS);

  if (speed == 0) {
    motorOff();
    showLcd("Wheel", "Stopped");
    return;
  }

  motorOff();
  activeMotorDirection = direction;
  activeMotorDurationMs = durationMs;
  activeMotorSpeed = speed;
  motorStopAt = millis() + durationMs;
  motorBoostActive = speed < MOTOR_START_BOOST_THRESHOLD && durationMs > MOTOR_START_BOOST_MS;
  motorBoostUntil = motorBoostActive ? millis() + MOTOR_START_BOOST_MS : 0;
  motorRunning = true;

  const uint8_t startSpeed = motorBoostActive ? MOTOR_START_BOOST_SPEED : speed;
  const bool reverseDirection = direction == "reverse" || direction == "ccw" || direction == "counterclockwise";

  if (reverseDirection) {
    Serial.print("Wheel reverse, speed ");
    Serial.print(speed);
    Serial.print(", ms ");
    Serial.print(durationMs);
    if (motorBoostActive) {
      Serial.print(", boost ");
      Serial.print(MOTOR_START_BOOST_SPEED);
      Serial.print(" for ");
      Serial.print(MOTOR_START_BOOST_MS);
      Serial.print("ms");
    }
    Serial.println();
    showLcd("Wheel Reverse", String(durationMs) + "ms");
    motorReverse(startSpeed);
  } else {
    Serial.print("Wheel forward, speed ");
    Serial.print(speed);
    Serial.print(", ms ");
    Serial.print(durationMs);
    if (motorBoostActive) {
      Serial.print(", boost ");
      Serial.print(MOTOR_START_BOOST_SPEED);
      Serial.print(" for ");
      Serial.print(MOTOR_START_BOOST_MS);
      Serial.print("ms");
    }
    Serial.println();
    showLcd("Wheel Forward", String(durationMs) + "ms");
    motorForward(startSpeed);
  }
}

void updateMotor() {
  if (!motorRunning) {
    return;
  }

  if (motorBoostActive && (int32_t)(millis() - motorBoostUntil) >= 0) {
    motorBoostActive = false;
    ledcWrite(MOTOR_PWM_CHANNEL, activeMotorSpeed);
    Serial.print("Wheel boost complete, PWM now ");
    Serial.println(activeMotorSpeed);
  }

  if ((int32_t)(millis() - motorStopAt) >= 0) {
    motorOff();
    Serial.println("Wheel stopped.");
    showLcd("Wheel", "Stopped");
  }
}

void runMotorRawPulse(const String &direction, uint16_t durationMs) {
  durationMs = constrain(durationMs, MOTOR_RAW_MIN_RUN_MS, 1000);
  motorOff();
  detachMotorPwm();

  const bool reverseDirection = direction == "reverse" || direction == "ccw" || direction == "counterclockwise";
  digitalWrite(MOTOR_STBY_PIN, HIGH);
  if (reverseDirection) {
    digitalWrite(MOTOR_IN1_PIN, LOW);
    digitalWrite(MOTOR_IN2_PIN, HIGH);
  } else {
    digitalWrite(MOTOR_IN1_PIN, HIGH);
    digitalWrite(MOTOR_IN2_PIN, LOW);
  }

  Serial.print("RAW wheel ");
  Serial.print(reverseDirection ? "reverse" : "forward");
  Serial.print(", ms ");
  Serial.println(durationMs);
  showLcd(reverseDirection ? "Raw Reverse" : "Raw Forward", String(durationMs) + "ms");

  digitalWrite(MOTOR_ENA_PIN, HIGH);
  delay(durationMs);
  digitalWrite(MOTOR_ENA_PIN, LOW);
  motorOff();
  attachMotorPwm();
  motorOff();
  showLcd("Raw Wheel", "Stopped");
}

void writeServoAngle(uint8_t angle) {
  const uint16_t pulseUs = map(angle, 0, 180, SERVO_MIN_PULSE_US, SERVO_MAX_PULSE_US);
  const uint32_t maxDuty = (1UL << SERVO_PWM_RESOLUTION_BITS) - 1;
  const uint32_t duty = (pulseUs * maxDuty) / 20000UL;
  ledcWrite(SERVO_PWM_CHANNEL, duty);
}

void setupServo() {
  ledcSetup(SERVO_PWM_CHANNEL, SERVO_PWM_FREQUENCY, SERVO_PWM_RESOLUTION_BITS);
  ledcAttachPin(SERVO_PIN, SERVO_PWM_CHANNEL);
  writeServoAngle(SERVO_CLOSED_DEGREES);
}

void resetLcdBus() {
  Wire.end();
  delay(100);
  Wire.begin(LCD_SDA_PIN, LCD_SCL_PIN);
  delay(100);
  lcd.init();
  lcd.backlight();
  lcd.clear();
  delay(50);
}

void setupVoltageMonitor() {
  pinMode(VOLTAGE_VM_PIN, INPUT_PULLDOWN);
  pinMode(VOLTAGE_SERVO_PIN, INPUT_PULLDOWN);
  pinMode(VOLTAGE_LOGIC_PIN, INPUT_PULLDOWN);

  analogReadResolution(12);
  analogSetPinAttenuation(VOLTAGE_VM_PIN, ADC_11db);
  analogSetPinAttenuation(VOLTAGE_SERVO_PIN, ADC_11db);
  analogSetPinAttenuation(VOLTAGE_LOGIC_PIN, ADC_11db);
}

uint32_t readAdcMilliVolts(uint8_t pin) {
  uint32_t total = 0;
  for (uint8_t i = 0; i < VOLTAGE_SAMPLE_COUNT; i++) {
    total += analogReadMilliVolts(pin);
    delayMicroseconds(250);
  }
  return total / VOLTAGE_SAMPLE_COUNT;
}

String voltageChannelJson(const char *id, const char *label, uint8_t pin) {
  const uint32_t adcMilliVolts = readAdcMilliVolts(pin);
  const bool saturated = adcMilliVolts >= VOLTAGE_ADC_SATURATED_MV;
  const float volts = (adcMilliVolts / 1000.0f) * VOLTAGE_DIVIDER_RATIO;

  String json = "{";
  json += "\"id\":\"" + String(id) + "\",";
  json += "\"label\":\"" + String(label) + "\",";
  json += "\"pin\":" + String(pin) + ",";
  json += "\"dividerRatio\":" + String(VOLTAGE_DIVIDER_RATIO, 2) + ",";
  json += "\"adcMilliVolts\":" + String(adcMilliVolts) + ",";
  json += "\"status\":\"" + String(saturated ? "saturated" : "ok") + "\",";
  json += "\"volts\":" + String(volts, 2);
  json += "}";
  return json;
}

String voltageJson() {
  String json = "{";
  json += "\"ok\":true,";
  json += "\"divider\":\"30k_to_signal_10k_to_ground\",";
  json += "\"channels\":[";
  json += voltageChannelJson("motorVm", "Motor VM", VOLTAGE_VM_PIN);
  json += ",";
  json += voltageChannelJson("servo5v", "Servo / 5V", VOLTAGE_SERVO_PIN);
  json += ",";
  json += voltageChannelJson("logic3v3", "Logic 3.3V", VOLTAGE_LOGIC_PIN);
  json += "]";
  json += "}";
  return json;
}
String lcdText(const String &value) {
  String text = value;
  text.replace("\r", " ");
  text.replace("\n", " ");
  if (text.length() > LCD_COLUMNS) {
    text = text.substring(0, LCD_COLUMNS);
  }
  while (text.length() < LCD_COLUMNS) {
    text += ' ';
  }
  return text;
}

void showLcd(const String &line1, const String &line2) {
  currentLine1 = line1;
  currentLine2 = line2;

  lcd.setCursor(0, 0);
  lcd.print(lcdText(line1));
  lcd.setCursor(0, 1);
  lcd.print(lcdText(line2));

  Serial.print("LCD: ");
  Serial.print(line1);
  Serial.print(" | ");
  Serial.println(line2);
}

void sendCorsHeaders() {
  server.sendHeader("Access-Control-Allow-Origin", "*");
  server.sendHeader("Access-Control-Allow-Methods", "GET,POST,OPTIONS");
  server.sendHeader("Access-Control-Allow-Headers", "Content-Type");
}

void handleOptions() {
  sendCorsHeaders();
  server.send(204);
}

void handleStatus() {
  sendCorsHeaders();
  const uint32_t remainingMs = motorRunning && (int32_t)(motorStopAt - millis()) > 0
    ? motorStopAt - millis()
    : 0;

  String json = "{";
  json += "\"ok\":true,";
  json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"line1\":\"" + currentLine1 + "\",";
  json += "\"line2\":\"" + currentLine2 + "\",";
  json += "\"motorLocked\":" + String(motorRunning ? "true" : "false") + ",";
  json += "\"motorRunning\":" + String(motorRunning ? "true" : "false") + ",";
  json += "\"motorRemainingMs\":" + String(remainingMs) + ",";
  json += "\"motorDirection\":\"" + activeMotorDirection + "\",";
  json += "\"motorSpeed\":" + String(activeMotorSpeed) + ",";
  json += "\"lidLocked\":false";
  json += "}";
  server.send(200, "application/json", json);
}

void handleVoltage() {
  sendCorsHeaders();
  server.send(200, "application/json", voltageJson());
}

void handleLcd() {
  sendCorsHeaders();
  String line1 = server.arg("line1");
  String line2 = server.arg("line2");

  if (line1.length() == 0) {
    line1 = "Waste Sorter";
  }
  if (line2.length() == 0) {
    line2 = "Ready";
  }

  showLcd(line1, line2);
  server.send(200, "application/json", "{\"ok\":true}");
}

void handleLcdReset() {
  sendCorsHeaders();
  resetLcdBus();
  showLcd("Waste Sorter", "Ready");
  server.send(200, "application/json", "{\"ok\":true,\"message\":\"LCD reset\"}");
}

void handleMotorRaw() {
  sendCorsHeaders();

  String direction = server.arg("direction");
  direction.toLowerCase();
  if (direction.length() == 0) {
    direction = "forward";
  }

  uint16_t durationMs = 300;
  if (server.hasArg("ms")) {
    durationMs = static_cast<uint16_t>(server.arg("ms").toInt());
  }
  durationMs = constrain(durationMs, MOTOR_RAW_MIN_RUN_MS, 1000);

  if (direction == "stop") {
    motorOff();
    attachMotorPwm();
    motorOff();
    showLcd("Wheel", "Stopped");
    server.send(200, "application/json", "{\"ok\":true,\"message\":\"Raw motor stopped\"}");
    return;
  }

  runMotorRawPulse(direction, durationMs);

  String json = "{";
  json += "\"ok\":true,";
  json += "\"mode\":\"raw\",";
  json += "\"direction\":\"" + direction + "\",";
  json += "\"ms\":" + String(durationMs);
  json += "}";
  server.send(200, "application/json", json);
}

void handleMotor() {
  sendCorsHeaders();

  String direction = server.arg("direction");
  direction.toLowerCase();
  if (direction.length() == 0) {
    direction = "forward";
  }

  uint16_t durationMs = MOTOR_DEFAULT_RUN_MS;
  if (server.hasArg("ms")) {
    durationMs = static_cast<uint16_t>(server.arg("ms").toInt());
  }

  uint8_t speed = MOTOR_DEFAULT_SPEED;
  if (server.hasArg("speed")) {
    speed = static_cast<uint8_t>(server.arg("speed").toInt());
  }

  durationMs = constrain(durationMs, 100, MOTOR_MAX_RUN_MS);
  speed = constrain(speed, 0, MOTOR_MAX_SPEED);

  if (direction == "stop") {
    motorOff();
    showLcd("Wheel", "Stopped");
    server.send(200, "application/json", "{\"ok\":true,\"message\":\"Motor stopped\"}");
    return;
  }

  runMotorPulse(direction, durationMs, speed);

  String json = "{";
  json += "\"ok\":true,";
  json += "\"direction\":\"" + direction + "\",";
  json += "\"ms\":" + String(durationMs) + ",";
  json += "\"speed\":" + String(speed);
  json += "}";
  server.send(200, "application/json", json);
}

void handleLid() {
  sendCorsHeaders();
  motorOff();

  Serial.println("Opening lid servo.");
  showLcd("Lid", "Opening");
  writeServoAngle(SERVO_OPEN_DEGREES);
  delay(SERVO_OPEN_HOLD_MS);

  Serial.println("Closing lid servo.");
  showLcd("Lid", "Closing");
  writeServoAngle(SERVO_CLOSED_DEGREES);
  delay(SERVO_SETTLE_MS);

  showLcd("Lid", "Closed");
  server.send(200, "application/json", "{\"ok\":true,\"message\":\"Lid opened and closed\"}");
}

bool connectWifi() {
  if (WiFi.status() == WL_CONNECTED) {
    return true;
  }

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.disconnect();
  delay(200);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("Connecting to Wi-Fi");
  showLcd("WiFi", "Connecting");

  uint32_t startedAt = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startedAt < 20000) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() != WL_CONNECTED) {
    showLcd("WiFi failed", "Check hotspot");
    return false;
  }

  Serial.print("Web control IP: ");
  Serial.println(WiFi.localIP());
  showLcd("Web Control IP", WiFi.localIP().toString());
  return true;
}

void ensureWifi() {
  static uint32_t lastAttemptAt = 0;

  if (WiFi.status() == WL_CONNECTED) {
    return;
  }

  if (millis() - lastAttemptAt < 5000) {
    return;
  }

  lastAttemptAt = millis();
  motorOff();
  Serial.println("Wi-Fi disconnected. Retrying...");
  connectWifi();
}

void setupRoutes() {
  server.on("/", HTTP_GET, handleStatus);
  server.on("/status", HTTP_GET, handleStatus);
  server.on("/voltage", HTTP_OPTIONS, handleOptions);
  server.on("/voltage", HTTP_GET, handleVoltage);
  server.on("/telemetry", HTTP_OPTIONS, handleOptions);
  server.on("/telemetry", HTTP_GET, handleVoltage);
  server.on("/lcd", HTTP_OPTIONS, handleOptions);
  server.on("/lcd", HTTP_POST, handleLcd);
  server.on("/lcd", HTTP_GET, handleLcd);
  server.on("/lcd-reset", HTTP_OPTIONS, handleOptions);
  server.on("/lcd-reset", HTTP_POST, handleLcdReset);
  server.on("/lcd-reset", HTTP_GET, handleLcdReset);
  server.on("/motor", HTTP_OPTIONS, handleOptions);
  server.on("/motor", HTTP_POST, handleMotor);
  server.on("/motor", HTTP_GET, handleMotor);
  server.on("/wheel", HTTP_OPTIONS, handleOptions);
  server.on("/wheel", HTTP_POST, handleMotor);
  server.on("/wheel", HTTP_GET, handleMotor);
  server.on("/motor-raw", HTTP_OPTIONS, handleOptions);
  server.on("/motor-raw", HTTP_POST, handleMotorRaw);
  server.on("/motor-raw", HTTP_GET, handleMotorRaw);
  server.on("/wheel-raw", HTTP_OPTIONS, handleOptions);
  server.on("/wheel-raw", HTTP_POST, handleMotorRaw);
  server.on("/wheel-raw", HTTP_GET, handleMotorRaw);
  server.on("/lid", HTTP_OPTIONS, handleOptions);
  server.on("/lid", HTTP_POST, handleLid);
  server.on("/lid", HTTP_GET, handleLid);
  server.begin();
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1000);

  setupMotor();
  setupVoltageMonitor();
  setupServo();
  resetLcdBus();
  showLcd("Web Control", "Booting");

  connectWifi();
  setupRoutes();
  Serial.println("HTTP control server started.");
}

void loop() {
  ensureWifi();
  server.handleClient();
  updateMotor();
  delay(2);
}
