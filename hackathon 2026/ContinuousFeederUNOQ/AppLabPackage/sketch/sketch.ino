/*
  Arduino UNO Q continuous feeder controller

  TB6612FNG wiring (channel A):

    UNO Q             TB6612FNG                 Motor power
    -----             ----------                -----------
    D5 (PWM)  ------> PWMA
    D7        ------> AIN1
    D8        ------> AIN2
    D9        ------> STBY
    3V3       ------> VCC
    GND       ------> GND  <------------------- Supply negative (-)
                      VM   <------------------- Supply positive (+), 6-12 V
                      AO1  -------------------- Motor terminal 1
                      AO2  -------------------- Motor terminal 2

  IMPORTANT:
  - The UNO Q and motor supply MUST share ground.
  - Never power the motor from an UNO Q 3V3/5V pin.
  - Add bulk decoupling near VM/GND as recommended by the driver datasheet.
  - Use a supply and driver cooling arrangement rated for motor stall current.

  UNO Q architecture note:
  The released Arduino Zephyr core does not expose the MPU Wi-Fi as WiFiServer
  to MCU sketches. The Debian MPU hosts the website (main.py) and calls these
  motor functions through the official Arduino Router Bridge.
*/

#include <Arduino.h>
#include <Arduino_RouterBridge.h>

namespace Pins {
constexpr uint8_t kPwm = 5;
constexpr uint8_t kAin1 = 7;
constexpr uint8_t kAin2 = 8;
constexpr uint8_t kStandby = 9;
}  // namespace Pins

namespace Limits {
constexpr int kMinPercent = 0;
constexpr int kMaxPercent = 100;
constexpr uint8_t kDefaultPercent = 50;
constexpr uint16_t kMaxPwm = 255;
constexpr unsigned long kStatusIntervalMs = 1000;
}  // namespace Limits

enum class MotorState : uint8_t { kStopped, kRunning, kEmergencyStopped };

struct FeederStatus {
  MotorState state = MotorState::kStopped;
  uint8_t percent = Limits::kDefaultPercent;
  uint8_t pwm = 0;
};

FeederStatus feeder;
unsigned long lastStatusUpdateMs = 0;
unsigned long lastWifiReportMs = 0;
bool wifiConnected = false;

// Required project functions.
void setupMotor();
bool startMotor();
void stopMotor();
void setMotorSpeed(int percent);
void handleWebsite();
void updateStatus();
void setupWifiLed();
bool setWifiStatus(bool connected);
void updateWifiLed();

// Safety and Bridge API functions.
void emergencyStopMotor();
bool resetEmergencyStop();
bool apiStartMotor();
bool apiStopMotor();
bool apiEmergencyStop();
bool apiResetEmergencyStop();
int apiSetMotorSpeed(int percent);
String apiGetStatus();
const char *motorStateText();
bool registerBridgeApi();

void setup() {
  setupMotor();  // Establish safe outputs before starting communications.
  setupWifiLed();

  Serial.begin(115200);
  Serial.println();
  Serial.println(F("UNO Q Continuous Feeder"));
  Serial.println(F("Motor is safely stopped. Starting Bridge..."));

  if (!Bridge.begin()) {
    Serial.println(F("Bridge startup failed; motor remains stopped."));
    return;
  }

  if (!registerBridgeApi()) {
    Serial.println(F("One or more feeder API methods failed to register."));
  } else {
    Serial.println(F("Feeder API ready. Start main.py on the UNO Q MPU."));
  }
}

void loop() {
  handleWebsite();
  updateWifiLed();
  updateStatus();

  // Future non-blocking MCU modules belong here, for example:
  // updateCounter(); updateConveyor(); updateSortingServos();
}

void setupMotor() {
  // Preload GPIO latches LOW before enabling outputs to prevent a startup pulse.
  digitalWrite(Pins::kStandby, LOW);
  digitalWrite(Pins::kAin1, LOW);
  digitalWrite(Pins::kAin2, LOW);

  pinMode(Pins::kPwm, OUTPUT);
  pinMode(Pins::kAin1, OUTPUT);
  pinMode(Pins::kAin2, OUTPUT);
  pinMode(Pins::kStandby, OUTPUT);
  analogWrite(Pins::kPwm, 0);

  feeder.state = MotorState::kStopped;
  feeder.percent = Limits::kDefaultPercent;
  feeder.pwm = 0;
}

bool startMotor() {
  if (feeder.state == MotorState::kEmergencyStopped) {
    return false;  // A latched E-stop always requires an explicit reset.
  }

  digitalWrite(Pins::kAin1, HIGH);
  digitalWrite(Pins::kAin2, LOW);
  digitalWrite(Pins::kStandby, HIGH);
  feeder.state = MotorState::kRunning;
  setMotorSpeed(feeder.percent);
  return true;
}

void stopMotor() {
  analogWrite(Pins::kPwm, 0);
  digitalWrite(Pins::kStandby, LOW);
  digitalWrite(Pins::kAin1, LOW);
  digitalWrite(Pins::kAin2, LOW);
  feeder.pwm = 0;

  if (feeder.state != MotorState::kEmergencyStopped) {
    feeder.state = MotorState::kStopped;
  }
}

void setMotorSpeed(int percent) {
  const int safePercent = constrain(percent, Limits::kMinPercent,
                                    Limits::kMaxPercent);
  feeder.percent = static_cast<uint8_t>(safePercent);

  if (feeder.state != MotorState::kRunning) {
    feeder.pwm = 0;
    analogWrite(Pins::kPwm, 0);
    return;
  }

  const long mappedPwm = map(safePercent, Limits::kMinPercent,
                             Limits::kMaxPercent, 0, Limits::kMaxPwm);
  feeder.pwm = static_cast<uint8_t>(constrain(mappedPwm, 0, 255));
  analogWrite(Pins::kPwm, feeder.pwm);
}

void emergencyStopMotor() {
  // Remove PWM first, then disable the H-bridge. Keep the E-stop latched.
  analogWrite(Pins::kPwm, 0);
  digitalWrite(Pins::kStandby, LOW);
  digitalWrite(Pins::kAin1, LOW);
  digitalWrite(Pins::kAin2, LOW);
  feeder.pwm = 0;
  feeder.state = MotorState::kEmergencyStopped;
}

bool resetEmergencyStop() {
  if (feeder.state != MotorState::kEmergencyStopped) {
    return false;
  }

  // Reset only clears the latch. It never starts the motor automatically.
  feeder.state = MotorState::kStopped;
  stopMotor();
  return true;
}

void handleWebsite() {
  // Service pending MPU-to-MCU RPC work. This performs no HTTP parsing and
  // never waits for a browser; main.py owns HTTP on the Linux processor.
  Bridge.update();
}

void updateStatus() {
  const unsigned long now = millis();
  if (now - lastStatusUpdateMs < Limits::kStatusIntervalMs) {
    return;
  }

  lastStatusUpdateMs = now;
  Serial.print(F("Motor="));
  Serial.print(motorStateText());
  Serial.print(F(" requested="));
  Serial.print(feeder.percent);
  Serial.print(F("% pwm="));
  Serial.print(feeder.pwm);
  Serial.print(F(" wifi="));
  Serial.println(wifiConnected ? F("connected") : F("disconnected"));
}

void setupWifiLed() {
  pinMode(LED3_R, OUTPUT);
  pinMode(LED3_G, OUTPUT);
  pinMode(LED3_B, OUTPUT);

  // UNO Q RGB channels are active LOW. Begin red until the MPU confirms Wi-Fi.
  digitalWrite(LED3_R, LOW);
  digitalWrite(LED3_G, HIGH);
  digitalWrite(LED3_B, HIGH);
}

bool setWifiStatus(bool connected) {
  wifiConnected = connected;
  lastWifiReportMs = millis();

  // Green means connected; red means the wireless link is unavailable.
  digitalWrite(LED3_R, connected ? HIGH : LOW);
  digitalWrite(LED3_G, connected ? LOW : HIGH);
  digitalWrite(LED3_B, HIGH);
  return wifiConnected;
}

void updateWifiLed() {
  constexpr unsigned long kWifiHeartbeatTimeoutMs = 6000;
  if (wifiConnected && millis() - lastWifiReportMs >= kWifiHeartbeatTimeoutMs) {
    setWifiStatus(false);
  }
}

bool apiStartMotor() {
  return startMotor();
}

bool apiStopMotor() {
  stopMotor();
  return true;
}

bool apiEmergencyStop() {
  emergencyStopMotor();
  return true;
}

bool apiResetEmergencyStop() {
  return resetEmergencyStop();
}

int apiSetMotorSpeed(int percent) {
  if (percent < Limits::kMinPercent || percent > Limits::kMaxPercent) {
    return -1;
  }
  setMotorSpeed(percent);
  return feeder.pwm;
}

String apiGetStatus() {
  String json;
  json.reserve(128);
  json += F("{\"state\":\"");
  json += motorStateText();
  json += F("\",\"running\":");
  json += feeder.state == MotorState::kRunning ? F("true") : F("false");
  json += F(",\"emergencyStopped\":");
  json += feeder.state == MotorState::kEmergencyStopped ? F("true")
                                                        : F("false");
  json += F(",\"percent\":");
  json += feeder.percent;
  json += F(",\"pwm\":");
  json += feeder.pwm;
  json += '}';
  return json;
}

const char *motorStateText() {
  switch (feeder.state) {
    case MotorState::kRunning:
      return "Running";
    case MotorState::kEmergencyStopped:
      return "Emergency Stopped";
    default:
      return "Stopped";
  }
}

bool registerBridgeApi() {
  bool ok = true;
  ok &= Bridge.provide("feeder/start", apiStartMotor);
  ok &= Bridge.provide("feeder/stop", apiStopMotor);
  ok &= Bridge.provide("feeder/emergencyStop", apiEmergencyStop);
  ok &= Bridge.provide("feeder/resetEmergencyStop", apiResetEmergencyStop);
  ok &= Bridge.provide("feeder/setSpeed", apiSetMotorSpeed);
  ok &= Bridge.provide("feeder/status", apiGetStatus);
  ok &= Bridge.provide("feeder/setWifiStatus", setWifiStatus);
  return ok;
}
