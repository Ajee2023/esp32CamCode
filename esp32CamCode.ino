#include "esp_camera.h"
#include <WiFi.h>
#include <WebServer.h>
#include <ESP32Servo.h>

// Replace with your network credentials
const char *ssid = "BELL135";
const char *password = "94EF493123CA";

WebServer server(80);

// Servo settings
#define SERVO_PIN 2
Servo sorterServo;
const int ANGLE_NEUTRAL = 90;
const int ANGLE_TRASH = 30;
const int ANGLE_VALUABLES = 150;
const int TIP_DURATION_MS = 800;

void tipServo(String bin) {
  int target = (bin == "valuables") ? ANGLE_VALUABLES : ANGLE_TRASH;
  sorterServo.write(target);
  delay(TIP_DURATION_MS);
  sorterServo.write(ANGLE_NEUTRAL);
}

void handleSort() {
  if (!server.hasArg("bin")) {
    server.send(400, "application/json", "{\"error\":\"Missing bin parameter\"}");
    return;
  }
  String bin = server.arg("bin");
  if (!(bin == "trash" || bin == "valuables")) {
    server.send(400, "application/json", "{\"error\":\"Invalid bin\"}");
    return;
  }
  tipServo(bin);
  server.send(200, "application/json", String("{\"ok\":true,\"bin\":\"") + bin + "\"}");
}

void handleRoot() {
  server.send(200, "text/plain", "ESP32-CAM is running.");
}

void handleCapture() {
  camera_fb_t *fb = NULL;

  // 🗑 Flush 3 old frames (tune this number if laggy)
  for (int i = 0; i < 3; i++) {
    fb = esp_camera_fb_get();
    if (fb) esp_camera_fb_return(fb);
  }

  // 📸 Grab the actual frame we’ll send
  fb = esp_camera_fb_get();
  if (!fb) {
    server.send(500, "text/plain", "Camera capture failed");
    return;
  }

  // ✅ Send newest JPEG
  server.send_P(200, "image/jpeg", (const char *)fb->buf, fb->len);

  // 🔄 Give buffer back
  esp_camera_fb_return(fb);
}

void handleStream() {
  server.send(200, "text/plain", "Stream not implemented yet");
}

void startCamera() {
  #define PWDN_GPIO_NUM  32
  #define RESET_GPIO_NUM -1
  #define XCLK_GPIO_NUM  0
  #define SIOD_GPIO_NUM  26
  #define SIOC_GPIO_NUM  27
  #define Y9_GPIO_NUM    35
  #define Y8_GPIO_NUM    34
  #define Y7_GPIO_NUM    39
  #define Y6_GPIO_NUM    36
  #define Y5_GPIO_NUM    21
  #define Y4_GPIO_NUM    19
  #define Y3_GPIO_NUM    18
  #define Y2_GPIO_NUM    5
  #define VSYNC_GPIO_NUM 25
  #define HREF_GPIO_NUM  23
  #define PCLK_GPIO_NUM  22
  // 4 for flash led or 33 for normal led
  #define LED_GPIO_NUM   4
  
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn     = PWDN_GPIO_NUM;
  config.pin_reset    = RESET_GPIO_NUM;
  config.xclk_freq_hz = 20000000;
  config.pixel_format = PIXFORMAT_JPEG;

  if (psramFound()) {
    config.frame_size = FRAMESIZE_SVGA;
    config.jpeg_quality = 12;
    config.fb_count = 2;
  } else {
    config.frame_size = FRAMESIZE_VGA;
    config.jpeg_quality = 15;
    config.fb_count = 1;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera init failed with error 0x%x\n", err);
    ESP.restart();
  }
}

void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(true);
  Serial.println();

  sorterServo.setPeriodHertz(50);
  sorterServo.attach(SERVO_PIN, 500, 2500);

  startCamera();

  WiFi.begin(ssid, password);
  WiFi.setSleep(false);
  Serial.print("WiFi connecting");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println("\nWiFi connected");

  server.on("/sort", handleSort);
  server.on("/", handleRoot);
  server.on("/capture", handleCapture);
  server.on("/stream", handleStream);

  server.begin();
  Serial.print("Camera Ready! Use 'http://");
  Serial.print(WiFi.localIP());
  Serial.println("' to connect");
}

void loop() {
  server.handleClient();
  delay(1);
}
