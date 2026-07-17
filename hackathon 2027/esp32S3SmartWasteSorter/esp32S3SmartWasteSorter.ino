/*
  ESP32-S3 Smart Waste Sorter

  Required Arduino libraries:
  - ArduinoJson
  - LiquidCrystal_I2C
  - ESP32Servo

  The ESP32-CAM should expose a JPEG endpoint such as:
    http://<camera-ip>/capture
*/

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <WiFiClient.h>
#include <WiFiClientSecure.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <ESP32Servo.h>
#include <ArduinoJson.h>
#include "esp_heap_caps.h"
#include "mbedtls/base64.h"

// -------------------- User configuration --------------------
const char *const WIFI_SSID = "LAPTOP-3M5IFCCV 9274";
const char *const WIFI_PASSWORD = "iR921)16";

const char *const ESP32_CAM_CAPTURE_URL = " http://192.168.137.74/capture";

const char *const GEMINI_API_KEY = "AQ.Ab8RN6IJQKSx71vVY7BwBrdgKLBKtCRjqwdUtcxavZvI5zODmw";
const char *const GEMINI_MODEL = "gemini-2.5-flash-lite";

// Test mode: capture image, classify it, and display the result without moving hardware.
const bool CLASSIFICATION_ONLY_TEST = true;

// Keep the camera JPEG small enough for embedded RAM. QVGA/VGA is ideal.
const size_t MAX_IMAGE_BYTES = 180 * 1024;

// LCD
const uint8_t LCD_I2C_ADDRESS = 0x27;
const uint8_t LCD_COLUMNS = 16;
const uint8_t LCD_ROWS = 2;
const uint8_t LCD_SDA_PIN = 8;
const uint8_t LCD_SCL_PIN = 9;

// L298N motor driver
const uint8_t MOTOR_ENA_PIN = 5;
const uint8_t MOTOR_IN1_PIN = 6;
const uint8_t MOTOR_IN2_PIN = 7;
const uint8_t MOTOR_PWM_DUTY = 210;  // 0-255

// Timed routing calibration. Tune these for your mechanical bin spacing.
const uint32_t GARBAGE_MOTOR_MS = 0;      // Home/default position.
const uint32_t RECYCLE_MOTOR_MS = 900;    // X milliseconds.
const uint32_t ORGANIC_MOTOR_MS = 1600;   // Y milliseconds.
const uint32_t MOTOR_SETTLE_MS = 250;
const bool RETURN_HOME_AFTER_DROP = true;

// Servo gate
const uint8_t SERVO_PIN = 10;
const int SERVO_CLOSED_DEGREES = 0;
const int SERVO_OPEN_DEGREES = 90;
const uint16_t SERVO_MIN_US = 500;
const uint16_t SERVO_MAX_US = 2400;
const uint32_t SERVO_OPEN_MS = 2000;
const uint32_t SERVO_SETTLE_MS = 500;

// Timing
const uint32_t WIFI_CONNECT_TIMEOUT_MS = 20000;
const uint32_t CAMERA_HTTP_TIMEOUT_MS = 10000;
const uint32_t GEMINI_HTTP_TIMEOUT_MS = 30000;
const uint32_t ERROR_RETRY_MS = 5000;
const uint32_t SORT_CYCLE_PAUSE_MS = 3000;
const uint32_t CLASSIFICATION_DISPLAY_MS = 1500;

const char *const GEMINI_PROMPT =
  "Classify the waste item in this image for a smart trash sorter. "
  "Choose exactly one category: Garbage, Recycle, Organic. "
  "Return only JSON with a category field.";

enum WasteCategory : uint8_t {
  CATEGORY_GARBAGE,
  CATEGORY_RECYCLE,
  CATEGORY_ORGANIC
};

LiquidCrystal_I2C lcd(LCD_I2C_ADDRESS, LCD_COLUMNS, LCD_ROWS);
Servo gateServo;

void *allocateBytes(size_t bytes) {
  void *ptr = heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
  if (ptr == nullptr) {
    ptr = heap_caps_malloc(bytes, MALLOC_CAP_8BIT);
  }
  return ptr;
}

void printLcdLine(uint8_t row, const String &value) {
  String line = value;
  if (line.length() > LCD_COLUMNS) {
    line = line.substring(0, LCD_COLUMNS);
  }
  while (line.length() < LCD_COLUMNS) {
    line += ' ';
  }

  lcd.setCursor(0, row);
  lcd.print(line);
}

void showLcd(const String &line1, const String &line2 = "") {
  printLcdLine(0, line1);
  printLcdLine(1, line2);
}

const char *categoryLabel(WasteCategory category) {
  switch (category) {
    case CATEGORY_RECYCLE:
      return "Recycle";
    case CATEGORY_ORGANIC:
      return "Organic";
    case CATEGORY_GARBAGE:
    default:
      return "Garbage";
  }
}

uint32_t motorDurationFor(WasteCategory category) {
  switch (category) {
    case CATEGORY_RECYCLE:
      return RECYCLE_MOTOR_MS;
    case CATEGORY_ORGANIC:
      return ORGANIC_MOTOR_MS;
    case CATEGORY_GARBAGE:
    default:
      return GARBAGE_MOTOR_MS;
  }
}

void stopMotor() {
  analogWrite(MOTOR_ENA_PIN, 0);
  digitalWrite(MOTOR_IN1_PIN, LOW);
  digitalWrite(MOTOR_IN2_PIN, LOW);
}

void runMotorForward(uint32_t durationMs) {
  if (durationMs == 0) {
    stopMotor();
    return;
  }

  digitalWrite(MOTOR_IN1_PIN, HIGH);
  digitalWrite(MOTOR_IN2_PIN, LOW);
  analogWrite(MOTOR_ENA_PIN, MOTOR_PWM_DUTY);
  delay(durationMs);
  stopMotor();
  delay(MOTOR_SETTLE_MS);
}

void runMotorReverse(uint32_t durationMs) {
  if (durationMs == 0) {
    stopMotor();
    return;
  }

  digitalWrite(MOTOR_IN1_PIN, LOW);
  digitalWrite(MOTOR_IN2_PIN, HIGH);
  analogWrite(MOTOR_ENA_PIN, MOTOR_PWM_DUTY);
  delay(durationMs);
  stopMotor();
  delay(MOTOR_SETTLE_MS);
}

void moveToBin(WasteCategory category) {
  showLcd("Routing", categoryLabel(category));
  runMotorForward(motorDurationFor(category));
}

void returnHomeFromBin(WasteCategory category) {
  if (!RETURN_HOME_AFTER_DROP) {
    return;
  }

  uint32_t durationMs = motorDurationFor(category);
  if (durationMs == 0) {
    return;
  }

  showLcd("Returning", "Home");
  runMotorReverse(durationMs);
}

void openAndCloseGate() {
  showLcd("Dropping", "Please wait");
  gateServo.write(SERVO_OPEN_DEGREES);
  delay(SERVO_OPEN_MS);
  gateServo.write(SERVO_CLOSED_DEGREES);
  delay(SERVO_SETTLE_MS);
}

void showReady() {
  showLcd("Ready", "Waiting image");
}

void showError(const String &line1, const String &details) {
  showLcd(line1, details);
  Serial.print(line1);
  Serial.print(": ");
  Serial.println(details);
}

bool connectToWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  Serial.print("Connecting to Wi-Fi");
  showLcd("WiFi", "Connecting");

  uint32_t startedAt = millis();
  while (WiFi.status() != WL_CONNECTED &&
         millis() - startedAt < WIFI_CONNECT_TIMEOUT_MS) {
    delay(250);
    Serial.print(".");
  }
  Serial.println();

  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }

  Serial.print("Wi-Fi connected. IP: ");
  Serial.println(WiFi.localIP());
  showLcd("WiFi connected", WiFi.localIP().toString());
  delay(1000);
  return true;
}

bool ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return true;
  }

  WiFi.disconnect();
  return connectToWiFi();
}

bool fetchLatestImage(uint8_t *&image, size_t &imageLen, String &error) {
  image = nullptr;
  imageLen = 0;

  WiFiClient cameraClient;
  HTTPClient http;
  http.setTimeout(CAMERA_HTTP_TIMEOUT_MS);

  if (!http.begin(cameraClient, ESP32_CAM_CAPTURE_URL)) {
    error = "Bad URL";
    return false;
  }

  int httpCode = http.GET();
  if (httpCode != HTTP_CODE_OK) {
    error = "HTTP " + String(httpCode);
    http.end();
    return false;
  }

  int contentLength = http.getSize();
  bool knownLength = contentLength > 0;
  if (knownLength && (size_t)contentLength > MAX_IMAGE_BYTES) {
    error = "Image too big";
    http.end();
    return false;
  }

  size_t bufferSize = knownLength ? (size_t)contentLength : MAX_IMAGE_BYTES;
  image = static_cast<uint8_t *>(allocateBytes(bufferSize));
  if (image == nullptr) {
    error = "No RAM";
    http.end();
    return false;
  }

  WiFiClient *stream = http.getStreamPtr();
  uint32_t lastByteAt = millis();
  size_t expectedLength = knownLength ? static_cast<size_t>(contentLength) : 0;

  while (millis() - lastByteAt < CAMERA_HTTP_TIMEOUT_MS) {
    if (knownLength && imageLen >= expectedLength) {
      break;
    }

    size_t availableBytes = stream->available();
    if (availableBytes == 0) {
      if (!knownLength && !stream->connected()) {
        break;
      }
      delay(5);
      continue;
    }

    size_t remaining = bufferSize - imageLen;
    if (remaining == 0) {
      free(image);
      image = nullptr;
      imageLen = 0;
      error = "Image too big";
      http.end();
      return false;
    }

    size_t toRead = min(availableBytes, remaining);
    size_t bytesRead = stream->readBytes(image + imageLen, toRead);

    if (bytesRead > 0) {
      imageLen += bytesRead;
      lastByteAt = millis();
    }
  }

  http.end();

  if (knownLength && imageLen != expectedLength) {
    free(image);
    image = nullptr;
    imageLen = 0;
    error = "Read timeout";
    return false;
  }

  bool hasJpegStart = imageLen >= 4 && image[0] == 0xFF && image[1] == 0xD8;
  bool hasJpegEnd = imageLen >= 4 && image[imageLen - 2] == 0xFF && image[imageLen - 1] == 0xD9;
  if (!hasJpegStart || !hasJpegEnd) {
    free(image);
    image = nullptr;
    imageLen = 0;
    error = "Bad JPEG";
    return false;
  }

  Serial.print("Fetched JPEG bytes: ");
  Serial.println(imageLen);
  return true;
}

char *base64EncodeImage(const uint8_t *image, size_t imageLen, size_t &encodedLen) {
  encodedLen = 0;
  size_t requiredLen = 0;

  int rc = mbedtls_base64_encode(
    nullptr,
    0,
    &requiredLen,
    reinterpret_cast<const unsigned char *>(image),
    imageLen
  );

  if (rc != MBEDTLS_ERR_BASE64_BUFFER_TOO_SMALL && rc != 0) {
    return nullptr;
  }

  char *encoded = static_cast<char *>(allocateBytes(requiredLen + 1));
  if (encoded == nullptr) {
    return nullptr;
  }

  rc = mbedtls_base64_encode(
    reinterpret_cast<unsigned char *>(encoded),
    requiredLen,
    &encodedLen,
    reinterpret_cast<const unsigned char *>(image),
    imageLen
  );

  if (rc != 0) {
    free(encoded);
    encodedLen = 0;
    return nullptr;
  }

  encoded[encodedLen] = '\0';
  return encoded;
}

bool parseCategoryName(const String &value, WasteCategory &category) {
  String normalized = value;
  normalized.trim();
  normalized.replace("\"", "");
  normalized.toLowerCase();

  if (normalized == "garbage" || normalized.indexOf("garbage") >= 0 ||
      normalized.indexOf("trash") >= 0 || normalized.indexOf("landfill") >= 0) {
    category = CATEGORY_GARBAGE;
    return true;
  }

  if (normalized == "recycle" || normalized.indexOf("recycle") >= 0 ||
      normalized.indexOf("recyclable") >= 0) {
    category = CATEGORY_RECYCLE;
    return true;
  }

  if (normalized == "organic" || normalized.indexOf("organic") >= 0 ||
      normalized.indexOf("compost") >= 0 || normalized.indexOf("food") >= 0) {
    category = CATEGORY_ORGANIC;
    return true;
  }

  return false;
}

bool parseCategoryText(String text, WasteCategory &category) {
  text.trim();
  text.replace("```json", "");
  text.replace("```", "");
  text.trim();

  DynamicJsonDocument resultDoc(512);
  DeserializationError resultError = deserializeJson(resultDoc, text);
  if (!resultError) {
    const char *categoryValue = resultDoc["category"];
    if (categoryValue == nullptr) {
      categoryValue = resultDoc["Category"];
    }
    if (categoryValue == nullptr) {
      categoryValue = resultDoc["bin"];
    }
    if (categoryValue != nullptr && parseCategoryName(categoryValue, category)) {
      return true;
    }
  }

  return parseCategoryName(text, category);
}

bool parseGeminiResponse(const String &payload, WasteCategory &category) {
  DynamicJsonDocument responseDoc(12288);
  DeserializationError responseError = deserializeJson(responseDoc, payload);
  if (responseError) {
    return parseCategoryText(payload, category);
  }

  const char *candidateText = responseDoc["candidates"][0]["content"]["parts"][0]["text"];
  if (candidateText != nullptr && parseCategoryText(candidateText, category)) {
    return true;
  }

  const char *outputText = responseDoc["output_text"];
  if (outputText != nullptr && parseCategoryText(outputText, category)) {
    return true;
  }

  return false;
}

bool buildGeminiPayload(const char *base64Image,
                        size_t base64Len,
                        uint8_t *&payload,
                        size_t &payloadLen,
                        String &error) {
  String prefix =
    "{\"contents\":[{\"role\":\"user\",\"parts\":[{\"text\":\"";
  prefix += GEMINI_PROMPT;
  prefix += "\"},{\"inline_data\":{\"mime_type\":\"image/jpeg\",\"data\":\"";

  String suffix =
    "\"}}]}],\"generationConfig\":{\"temperature\":0,\"maxOutputTokens\":128,"
    "\"response_mime_type\":\"application/json\","
    "\"response_schema\":{\"type\":\"OBJECT\",\"properties\":{\"category\":"
    "{\"type\":\"STRING\",\"enum\":[\"Garbage\",\"Recycle\",\"Organic\"]}},"
    "\"required\":[\"category\"]}}}";

  payloadLen = prefix.length() + base64Len + suffix.length();
  payload = static_cast<uint8_t *>(allocateBytes(payloadLen));
  if (payload == nullptr) {
    error = "No RAM";
    payloadLen = 0;
    return false;
  }

  size_t offset = 0;
  memcpy(payload + offset, prefix.c_str(), prefix.length());
  offset += prefix.length();
  memcpy(payload + offset, base64Image, base64Len);
  offset += base64Len;
  memcpy(payload + offset, suffix.c_str(), suffix.length());

  return true;
}

bool classifyWithGemini(const uint8_t *image,
                        size_t imageLen,
                        WasteCategory &category,
                        String &error) {
  if (String(GEMINI_API_KEY) == "YOUR_GEMINI_API_KEY") {
    error = "Set API key";
    return false;
  }

  size_t base64Len = 0;
  char *base64Image = base64EncodeImage(image, imageLen, base64Len);
  if (base64Image == nullptr) {
    error = "Base64 fail";
    return false;
  }

  uint8_t *payload = nullptr;
  size_t payloadLen = 0;
  if (!buildGeminiPayload(base64Image, base64Len, payload, payloadLen, error)) {
    free(base64Image);
    return false;
  }

  String endpoint = "https://generativelanguage.googleapis.com/v1beta/models/";
  endpoint += GEMINI_MODEL;
  endpoint += ":generateContent?key=";
  endpoint += GEMINI_API_KEY;

  WiFiClientSecure secureClient;
  secureClient.setInsecure();  // For prototypes. Use a root CA certificate for production.

  HTTPClient http;
  http.setTimeout(GEMINI_HTTP_TIMEOUT_MS);

  if (!http.begin(secureClient, endpoint)) {
    free(payload);
    free(base64Image);
    error = "API URL fail";
    return false;
  }

  http.addHeader("Content-Type", "application/json");
  int httpCode = http.POST(payload, payloadLen);

  free(payload);
  free(base64Image);

  String response = http.getString();
  http.end();

  if (httpCode < 200 || httpCode >= 300) {
    error = "HTTP " + String(httpCode);
    Serial.println("Gemini error response:");
    Serial.println(response);
    return false;
  }

  if (!parseGeminiResponse(response, category)) {
    error = "Bad result";
    Serial.println("Unparsed Gemini response:");
    Serial.println(response);
    return false;
  }

  return true;
}

void runSortCycle() {
  if (!ensureWiFi()) {
    showError("WiFi failed", "Retrying");
    delay(ERROR_RETRY_MS);
    return;
  }

  showLcd("Capturing", "ESP32-CAM");

  uint8_t *image = nullptr;
  size_t imageLen = 0;
  String error;
  if (!fetchLatestImage(image, imageLen, error)) {
    showError("Camera failed", error);
    delay(ERROR_RETRY_MS);
    return;
  }

  showLcd("Classifying", "Gemini");

  WasteCategory category = CATEGORY_GARBAGE;
  if (!classifyWithGemini(image, imageLen, category, error)) {
    free(image);
    stopMotor();
    if (CLASSIFICATION_ONLY_TEST) {
      digitalWrite(SERVO_PIN, LOW);
    } else {
      gateServo.write(SERVO_CLOSED_DEGREES);
    }
    showError("Gemini failed", error);
    delay(ERROR_RETRY_MS);
    return;
  }

  free(image);

  showLcd("Classified", categoryLabel(category));
  Serial.print("Classification: ");
  Serial.println(categoryLabel(category));
  delay(CLASSIFICATION_DISPLAY_MS);

  if (CLASSIFICATION_ONLY_TEST) {
    showLcd("AI RESULT", categoryLabel(category));
    Serial.println("Classification-only test complete. LCD is holding the result.");
    while (true) {
      stopMotor();
      digitalWrite(SERVO_PIN, LOW);
      delay(1000);
    }
  }

  moveToBin(category);
  openAndCloseGate();
  returnHomeFromBin(category);
  showReady();
  delay(SORT_CYCLE_PAUSE_MS);
}

void setup() {
  Serial.begin(115200);
  delay(1000);
  Serial.println("Booting ESP32-S3 Smart Waste Sorter");

  pinMode(MOTOR_ENA_PIN, OUTPUT);
  pinMode(MOTOR_IN1_PIN, OUTPUT);
  pinMode(MOTOR_IN2_PIN, OUTPUT);
  stopMotor();

  Wire.begin(LCD_SDA_PIN, LCD_SCL_PIN);
  lcd.init();
  lcd.backlight();
  showLcd("Waste Sorter", "Booting");

  if (CLASSIFICATION_ONLY_TEST) {
    pinMode(SERVO_PIN, OUTPUT);
    digitalWrite(SERVO_PIN, LOW);
    Serial.println("Classification-only test mode: motor and servo actions disabled.");
  } else {
    gateServo.setPeriodHertz(50);
    gateServo.attach(SERVO_PIN, SERVO_MIN_US, SERVO_MAX_US);
    gateServo.write(SERVO_CLOSED_DEGREES);
  }

  if (!connectToWiFi()) {
    showError("WiFi failed", "Check config");
    delay(ERROR_RETRY_MS);
  }

  showReady();
}

void loop() {
  runSortCycle();
}
