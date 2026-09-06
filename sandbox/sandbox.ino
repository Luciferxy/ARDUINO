#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <DHT.h>

// Initialize LCD
LiquidCrystal_I2C lcd(0x27, 16, 2);

// Initialize DHT11
DHT dht(7, DHT11);

// Pin definitions
const int roomLightPin = 4;
const int alertLedPin = 5;
const int acStatusLedPin = 6;
const int buzzerPin = 8;
const int ldrPin = A0;
const int builtinLedPin = 13;

void setup() {
  pinMode(roomLightPin, OUTPUT);
  pinMode(alertLedPin, OUTPUT);
  pinMode(acStatusLedPin, OUTPUT);
  pinMode(buzzerPin, OUTPUT);
  pinMode(builtinLedPin, OUTPUT);

  digitalWrite(roomLightPin, LOW);
  digitalWrite(alertLedPin, LOW);
  digitalWrite(acStatusLedPin, LOW);
  digitalWrite(builtinLedPin, LOW);

  lcd.init();
  lcd.backlight();
  lcd.setCursor(0, 0);
  lcd.print("Blinking Lights!");

  dht.begin();

  delay(1000);
}

void blinkLight(int pin, int times, int onTime, int offTime) {
  for (int i = 0; i < times; i++) {
    digitalWrite(pin, HIGH);
    delay(onTime);
    digitalWrite(pin, LOW);
    delay(offTime);
  }
}

void loop() {
  // Blink all available lights twice
  blinkLight(roomLightPin, 2, 500, 500);
  blinkLight(alertLedPin, 2, 500, 500);
  blinkLight(acStatusLedPin, 2, 500, 500);
  blinkLight(builtinLedPin, 2, 500, 500);

  // Brief buzzer chirp twice to confirm
  tone(buzzerPin, 1000, 200);
  delay(400);
  tone(buzzerPin, 1000, 200);
  delay(1000);
}