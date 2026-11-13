#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

constexpr uint32_t SERIAL_BAUD = 115200;

constexpr uint8_t I2C_SDA_PIN = 8;
constexpr uint8_t I2C_SCL_PIN = 9;
constexpr uint8_t LCD_ADDRESS = 0x27;
constexpr uint8_t LCD_COLUMNS = 16;
constexpr uint8_t LCD_ROWS = 2;

constexpr uint8_t MOTOR_ENA_PIN = 5;
constexpr uint8_t MOTOR_IN1_PIN = 6;
constexpr uint8_t MOTOR_IN2_PIN = 7;
constexpr uint8_t SERVO_PIN = 10;

LiquidCrystal_I2C lcd(LCD_ADDRESS, LCD_COLUMNS, LCD_ROWS);

void setOutputsSafelyOff() {
  pinMode(MOTOR_IN1_PIN, OUTPUT);
  pinMode(MOTOR_IN2_PIN, OUTPUT);
  pinMode(MOTOR_ENA_PIN, OUTPUT);
  pinMode(SERVO_PIN, OUTPUT);

  digitalWrite(MOTOR_IN1_PIN, LOW);
  digitalWrite(MOTOR_IN2_PIN, LOW);
  digitalWrite(MOTOR_ENA_PIN, LOW);
  digitalWrite(SERVO_PIN, LOW);
  analogWrite(MOTOR_ENA_PIN, 0);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1000);

  setOutputsSafelyOff();
  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);

  lcd.init();
  lcd.backlight();
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("TEST STOPPED");
  lcd.setCursor(0, 1);
  lcd.print("Motor OFF");

  Serial.println("Safe idle firmware running.");
  Serial.println("Motor pins LOW, PWM 0, servo signal LOW.");
  Serial.println("LCD test counter stopped.");
}

void loop() {
  setOutputsSafelyOff();
  delay(1000);
}
