#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

constexpr uint32_t SERIAL_BAUD = 115200;

constexpr uint8_t MOTOR_ENA_PIN = 5;
constexpr uint8_t MOTOR_IN1_PIN = 6;
constexpr uint8_t MOTOR_IN2_PIN = 7;
constexpr uint8_t MOTOR_STBY_PIN = 4;

constexpr uint8_t I2C_SDA_PIN = 8;
constexpr uint8_t I2C_SCL_PIN = 9;
constexpr uint8_t LCD_ADDRESS = 0x27;
constexpr uint8_t LCD_COLUMNS = 16;
constexpr uint8_t LCD_ROWS = 2;

constexpr uint8_t PWM_CHANNEL = 0;
constexpr uint16_t PWM_FREQUENCY_HZ = 1000;
constexpr uint8_t PWM_RESOLUTION_BITS = 8;
constexpr uint8_t LOW_SPEED_DUTY = 100;     // 0-255. Start low for safety.
constexpr uint8_t MEDIUM_SPEED_DUTY = 160;  // Still conservative, but easier to see.

constexpr uint32_t BOOT_SAFETY_WAIT_MS = 5000;
constexpr uint32_t ROTATION_MS = 1200;
constexpr uint32_t STOP_BETWEEN_MS = 2000;

LiquidCrystal_I2C lcd(LCD_ADDRESS, LCD_COLUMNS, LCD_ROWS);

void printLcdLine(uint8_t row, const String &message) {
  String line = message;
  if (line.length() > LCD_COLUMNS) {
    line = line.substring(0, LCD_COLUMNS);
  }
  while (line.length() < LCD_COLUMNS) {
    line += ' ';
  }

  lcd.setCursor(0, row);
  lcd.print(line);
}

void showStatus(const String &line1, const String &line2 = "") {
  printLcdLine(0, line1);
  printLcdLine(1, line2);

  Serial.print(line1);
  if (line2.length() > 0) {
    Serial.print(" - ");
    Serial.print(line2);
  }
  Serial.println();
}

void motorOff() {
  ledcWrite(PWM_CHANNEL, 0);
  digitalWrite(MOTOR_IN1_PIN, LOW);
  digitalWrite(MOTOR_IN2_PIN, LOW);
  digitalWrite(MOTOR_STBY_PIN, HIGH);

  Serial.println("Pins: STBY=HIGH, PWM=0, IN1=LOW, IN2=LOW");
}

void motorClockwise(uint8_t duty) {
  digitalWrite(MOTOR_STBY_PIN, HIGH);
  digitalWrite(MOTOR_IN1_PIN, HIGH);
  digitalWrite(MOTOR_IN2_PIN, LOW);
  ledcWrite(PWM_CHANNEL, duty);

  Serial.print("Pins: ENA PWM=");
  Serial.print(duty);
  Serial.println(", IN1=HIGH, IN2=LOW");
}

void motorCounterClockwise(uint8_t duty) {
  digitalWrite(MOTOR_STBY_PIN, HIGH);
  digitalWrite(MOTOR_IN1_PIN, LOW);
  digitalWrite(MOTOR_IN2_PIN, HIGH);
  ledcWrite(PWM_CHANNEL, duty);

  Serial.print("Pins: ENA PWM=");
  Serial.print(duty);
  Serial.println(", IN1=LOW, IN2=HIGH");
}

void waitWithCountdown(uint32_t totalMs) {
  uint32_t remainingSeconds = totalMs / 1000;

  while (remainingSeconds > 0) {
    showStatus("Motor Test Ready", "Start in " + String(remainingSeconds));
    delay(1000);
    remainingSeconds--;
  }
}

void runMotorTestOnce() {
  showStatus("Motor Test Ready", "Motor OFF");
  Serial.println("Safety wait: motor will remain OFF for 5 seconds.");
  waitWithCountdown(BOOT_SAFETY_WAIT_MS);

  showStatus("Clockwise", "PWM 100");
  Serial.print("PWM duty: ");
  Serial.println(LOW_SPEED_DUTY);
  motorClockwise(LOW_SPEED_DUTY);
  delay(ROTATION_MS);

  motorOff();
  showStatus("Stopped", "2 sec pause");
  delay(STOP_BETWEEN_MS);

  showStatus("Clockwise", "PWM 160");
  Serial.print("PWM duty: ");
  Serial.println(MEDIUM_SPEED_DUTY);
  motorClockwise(MEDIUM_SPEED_DUTY);
  delay(ROTATION_MS);

  motorOff();
  showStatus("Stopped", "2 sec pause");
  delay(STOP_BETWEEN_MS);

  showStatus("Counter-clockwise", "PWM 160");
  Serial.print("PWM duty: ");
  Serial.println(MEDIUM_SPEED_DUTY);
  motorCounterClockwise(MEDIUM_SPEED_DUTY);
  delay(ROTATION_MS);

  motorOff();
  showStatus("Test Complete", "Motor OFF");
  Serial.println("Motor rotation test complete. Motor is OFF.");
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1000);

  pinMode(MOTOR_IN1_PIN, OUTPUT);
  pinMode(MOTOR_IN2_PIN, OUTPUT);
  pinMode(MOTOR_ENA_PIN, OUTPUT);
  pinMode(MOTOR_STBY_PIN, OUTPUT);
  digitalWrite(MOTOR_STBY_PIN, HIGH);

  ledcSetup(PWM_CHANNEL, PWM_FREQUENCY_HZ, PWM_RESOLUTION_BITS);
  ledcAttachPin(MOTOR_ENA_PIN, PWM_CHANNEL);
  motorOff();

  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  lcd.init();
  lcd.backlight();
  lcd.clear();

  Serial.println();
  Serial.println("ESP32-S3 L298N safe motor rotation test");
  Serial.println("Motor is OFF during setup. PWM duty is 0.");
  Serial.println("No movement starts until after the 5 second safety wait.");

  runMotorTestOnce();
}

void loop() {
  motorOff();
  delay(1000);
}
