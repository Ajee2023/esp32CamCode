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
constexpr uint8_t SERVO_PIN = 10;

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

void motorOff() {
  pinMode(MOTOR_ENA_PIN, OUTPUT);
  pinMode(MOTOR_IN1_PIN, OUTPUT);
  pinMode(MOTOR_IN2_PIN, OUTPUT);

  analogWrite(MOTOR_ENA_PIN, 0);
  digitalWrite(MOTOR_ENA_PIN, LOW);
  digitalWrite(MOTOR_IN1_PIN, LOW);
  digitalWrite(MOTOR_IN2_PIN, LOW);
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
  String json = "{";
  json += "\"ok\":true,";
  json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"line1\":\"" + currentLine1 + "\",";
  json += "\"line2\":\"" + currentLine2 + "\",";
  json += "\"motorLocked\":true,";
  json += "\"lidLocked\":false";
  json += "}";
  server.send(200, "application/json", json);
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

void handleLockedAction() {
  sendCorsHeaders();
  motorOff();
  server.send(423, "application/json", "{\"ok\":false,\"message\":\"Motor action is locked for calibration.\"}");
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
  server.on("/lcd", HTTP_OPTIONS, handleOptions);
  server.on("/lcd", HTTP_POST, handleLcd);
  server.on("/lcd", HTTP_GET, handleLcd);
  server.on("/motor", HTTP_OPTIONS, handleOptions);
  server.on("/motor", HTTP_POST, handleLockedAction);
  server.on("/lid", HTTP_OPTIONS, handleOptions);
  server.on("/lid", HTTP_POST, handleLid);
  server.on("/lid", HTTP_GET, handleLid);
  server.begin();
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1000);

  motorOff();
  setupServo();
  Wire.begin(LCD_SDA_PIN, LCD_SCL_PIN);
  lcd.init();
  lcd.backlight();
  showLcd("Web Control", "Booting");

  connectWifi();
  setupRoutes();
  Serial.println("HTTP control server started.");
}

void loop() {
  ensureWifi();
  server.handleClient();
  motorOff();
  delay(2);
}
