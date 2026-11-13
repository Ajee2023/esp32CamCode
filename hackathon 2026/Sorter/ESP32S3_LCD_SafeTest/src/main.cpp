#include <Arduino.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

constexpr uint32_t SERIAL_BAUD = 115200;

constexpr uint8_t I2C_SDA_PIN = 8;
constexpr uint8_t I2C_SCL_PIN = 9;

constexpr uint8_t LCD_ADDRESS = 0x27;
constexpr uint8_t LCD_FALLBACK_ADDRESS = 0x3F;
constexpr uint8_t LCD_COLUMNS = 16;
constexpr uint8_t LCD_ROWS = 2;

constexpr uint8_t MOTOR_ENA_PIN = 5;
constexpr uint8_t MOTOR_IN1_PIN = 6;
constexpr uint8_t MOTOR_IN2_PIN = 7;
constexpr uint8_t SERVO_PIN = 10;

uint8_t detectedAddresses[16];
uint8_t detectedAddressCount = 0;
uint8_t activeLcdAddress = LCD_ADDRESS;
LiquidCrystal_I2C *lcd = nullptr;

void setMotorSafelyOff() {
  pinMode(MOTOR_IN1_PIN, OUTPUT);
  pinMode(MOTOR_IN2_PIN, OUTPUT);
  pinMode(MOTOR_ENA_PIN, OUTPUT);

  digitalWrite(MOTOR_IN1_PIN, LOW);
  digitalWrite(MOTOR_IN2_PIN, LOW);
  digitalWrite(MOTOR_ENA_PIN, LOW);
  analogWrite(MOTOR_ENA_PIN, 0);
}

void holdServoSignalLow() {
  pinMode(SERVO_PIN, OUTPUT);
  digitalWrite(SERVO_PIN, LOW);
}

void printHexAddress(uint8_t address) {
  Serial.print("0x");
  if (address < 16) {
    Serial.print('0');
  }
  Serial.print(address, HEX);
}

bool addressWasDetected(uint8_t address) {
  for (uint8_t index = 0; index < detectedAddressCount; index++) {
    if (detectedAddresses[index] == address) {
      return true;
    }
  }

  return false;
}

uint8_t chooseLcdAddress() {
  if (addressWasDetected(LCD_ADDRESS)) {
    return LCD_ADDRESS;
  }

  if (addressWasDetected(LCD_FALLBACK_ADDRESS)) {
    return LCD_FALLBACK_ADDRESS;
  }

  if (detectedAddressCount > 0) {
    return detectedAddresses[0];
  }

  return LCD_ADDRESS;
}

void scanI2CBus() {
  detectedAddressCount = 0;

  Serial.println();
  Serial.println("Scanning I2C bus...");

  for (uint8_t address = 1; address < 127; address++) {
    Wire.beginTransmission(address);
    uint8_t error = Wire.endTransmission();

    if (error == 0) {
      if (detectedAddressCount < sizeof(detectedAddresses)) {
        detectedAddresses[detectedAddressCount] = address;
        detectedAddressCount++;
      }

      Serial.print("I2C device found at ");
      printHexAddress(address);
      Serial.println();
    } else if (error == 4) {
      Serial.print("Unknown I2C error at ");
      printHexAddress(address);
      Serial.println();
    }
  }

  if (detectedAddressCount == 0) {
    Serial.println("No I2C devices found.");
    Serial.println("Check LCD VCC, GND, SDA=GPIO8, SCL=GPIO9, and common ground.");
  } else {
    Serial.print("I2C scan complete. Devices found: ");
    Serial.println(detectedAddressCount);
  }

  if (addressWasDetected(LCD_ADDRESS)) {
    Serial.println("LCD address 0x27 detected.");
  } else if (addressWasDetected(LCD_FALLBACK_ADDRESS)) {
    Serial.println("LCD address 0x3F detected.");
  } else {
    Serial.println("LCD address 0x27 was not detected.");
  }
}

void initializeLcd(uint8_t address) {
  activeLcdAddress = address;
  lcd = new LiquidCrystal_I2C(activeLcdAddress, LCD_COLUMNS, LCD_ROWS);

  Serial.print("Initializing LCD at ");
  printHexAddress(activeLcdAddress);
  Serial.println("...");

  lcd->init();
  lcd->backlight();
  delay(200);
  lcd->clear();
  lcd->setCursor(0, 0);
  lcd->print("LCD TEST");
  lcd->setCursor(0, 1);
  lcd->print("Addr ");
  lcd->print("0x");
  if (activeLcdAddress < 16) {
    lcd->print('0');
  }
  lcd->print(activeLcdAddress, HEX);

  Serial.println("If the LCD is lit but text is invisible, adjust the small contrast screw on the I2C backpack.");
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  delay(1000);

  Serial.println();
  Serial.println("ESP32-S3 safe hardware test");
  Serial.println("Motor test disabled. LCD test only.");

  setMotorSafelyOff();
  holdServoSignalLow();
  Serial.println("Motor pins set LOW and PWM set to 0.");
  Serial.println("Servo pin held LOW; servo is not attached or moved.");

  Wire.begin(I2C_SDA_PIN, I2C_SCL_PIN);
  Serial.println("I2C started with Wire.begin(8, 9).");

  scanI2CBus();

  initializeLcd(chooseLcdAddress());
}

void loop() {
  static uint32_t counter = 0;

  setMotorSafelyOff();

  if (lcd != nullptr) {
    lcd->setCursor(0, 0);
    lcd->print("LCD TEST        ");
    lcd->setCursor(0, 1);
    lcd->print("Count: ");
    lcd->print(counter);
    lcd->print("        ");
  }

  Serial.print("LCD TEST counter: ");
  Serial.println(counter);

  counter++;
  delay(1000);
}
