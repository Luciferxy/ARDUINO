#include <Wire.h>
#include <LiquidCrystal_I2C.h>
#include <DHT.h>

// Pins
#define DHTPIN 7
#define DHTTYPE DHT11
#define LDR_PIN A0
#define ROOM_LIGHT_PIN 4
#define ALERT_PIN 5
#define AC_LED_PIN 6
#define BUZZER_PIN 8
#define BUILTIN_LED 13

// Hysteresis thresholds for light control (< 50 is pitch dark)
const int LDR_DARK_THRESHOLD = 50;
const int LDR_LIGHT_THRESHOLD = 70;

// Initialize sensors and actuators
DHT dht(DHTPIN, DHTTYPE);
LiquidCrystal_I2C lcd(0x27, 16, 2);

// State variables
float currentTemp = 25.0;
float currentHum = 50.0;
int currentLdr = 500;
bool lightState = false;
bool acLedState = false;
bool alertLedState = false;
bool autoLightEnabled = true;
bool isDark = false;

// Non-blocking loop timers
unsigned long lastTelemetry = 0;
unsigned long lastDhtCheck = 0;
unsigned long lastLdrCheck = 0;

// Multi-page LCD Message Paging Engine
char msgPages[6][2][17];        // Up to 6 pages, 2 lines of max 16 chars each
int totalPages = 0;
int currentPage = 0;
unsigned long lastPageFlip = 0;
const unsigned long PAGE_DURATION = 3200; // 3.2 seconds per page
bool isPagingMessage = false;

// Single-page LCD Override timer
unsigned long lcdOverrideUntil = 0;
bool isLcdOverridden = false;

// Universal Buzzer Tone helper (supports both passive and active buzzers)
void chirpBuzzer(int freq, int durationMs) {
  if (freq > 0) {
    tone(BUZZER_PIN, freq);
  }
  digitalWrite(BUZZER_PIN, HIGH);
  delay(durationMs);
  noTone(BUZZER_PIN);
  digitalWrite(BUZZER_PIN, LOW);
}

// Futuristic AI Startup Melody (4-note ascending boot chime)
void playAiStartupSound() {
  int melody[] = {523, 659, 784, 1046}; // C5, E5, G5, C6
  int durations[] = {90, 90, 90, 200};
  for (int i = 0; i < 4; i++) {
    tone(BUZZER_PIN, melody[i]);
    digitalWrite(BUZZER_PIN, HIGH);
    delay(durations[i]);
    noTone(BUZZER_PIN);
    digitalWrite(BUZZER_PIN, LOW);
    delay(25);
  }
}

// Renders the active page of a multi-page message
void renderCurrentPage() {
  if (totalPages == 0) return;
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(msgPages[currentPage][0]);
  lcd.setCursor(0, 1);
  lcd.print(msgPages[currentPage][1]);
}

// Word-wraps full text into 16-character lines and packs them into 2-line pages
void prepareMessagePages(String text) {
  text.trim();
  int textLen = text.length();
  if (textLen == 0) return;

  // Clear existing pages
  totalPages = 0;
  currentPage = 0;
  for (int p = 0; p < 6; p++) {
    for (int l = 0; l < 2; l++) {
      memset(msgPages[p][l], 0, 17);
    }
  }

  int currentLine = 0;
  int currentPos = 0;

  while (currentPos < textLen && currentLine < 12) {
    // Skip leading spaces
    while (currentPos < textLen && text.charAt(currentPos) == ' ') {
      currentPos++;
    }
    if (currentPos >= textLen) break;

    int lineLen = 0;
    int lastSpace = -1;

    // Look ahead up to 16 characters
    for (int i = 0; i < 16 && (currentPos + i) < textLen; i++) {
      if (text.charAt(currentPos + i) == ' ') {
        lastSpace = i;
      }
      lineLen = i + 1;
    }

    int charsToTake = lineLen;
    // If reached 16 chars and next char isn't a space, break on the last space to prevent word slicing
    if (lineLen == 16 && (currentPos + 16) < textLen && text.charAt(currentPos + 16) != ' ') {
      if (lastSpace > 0) {
        charsToTake = lastSpace;
      }
    }

    int pageIdx = currentLine / 2;
    int lineIdx = currentLine % 2;

    for (int j = 0; j < charsToTake; j++) {
      msgPages[pageIdx][lineIdx][j] = text.charAt(currentPos + j);
    }
    msgPages[pageIdx][lineIdx][charsToTake] = '\0';

    currentPos += charsToTake;
    currentLine++;
  }

  totalPages = (currentLine + 1) / 2;
  if (totalPages == 0 && currentLine > 0) totalPages = 1;

  isPagingMessage = true;
  isLcdOverridden = false;
  lastPageFlip = millis();

  // Show Page 1 immediately
  renderCurrentPage();
  // Soft audio pip signaling incoming AI message
  chirpBuzzer(2600, 40);
}

void parseSerialCommand(String cmd) {
  cmd.trim();
  String commandId = "";
  if (cmd.startsWith("CMD:")) {
    int idSep = cmd.indexOf(':', 4);
    if (idSep > 4) {
      commandId = cmd.substring(4, idSep);
      cmd = cmd.substring(idSep + 1);
      cmd.trim();
    }
  }

  // Full AI response paging command
  if (cmd.startsWith("MSG:")) {
    String message = cmd.substring(4);
    prepareMessagePages(message);
  }
  else if (cmd.startsWith("CLEAR_LCD") || cmd.startsWith("RESET_LCD")) {
    isPagingMessage = false;
    isLcdOverridden = false;
    totalPages = 0;
    currentPage = 0;
    lcd.clear();
  }
  else if (cmd.startsWith("SET_LCD:")) {
    String payload = cmd.substring(8);
    int firstSep = payload.indexOf('|');
    int secondSep = payload.indexOf('|', firstSep + 1);

    if (firstSep != -1) {
      String line0 = payload.substring(0, firstSep);
      String line1 = (secondSep != -1) ? payload.substring(firstSep + 1, secondSep) : payload.substring(firstSep + 1);
      int duration = (secondSep != -1) ? payload.substring(secondSep + 1).toInt() : 8000;

      isPagingMessage = false;
      lcd.clear();
      lcd.setCursor(0, 0);
      lcd.print(line0.substring(0, 16));
      lcd.setCursor(0, 1);
      lcd.print(line1.substring(0, 16));

      lcdOverrideUntil = millis() + duration;
      isLcdOverridden = true;
    }
  }
  else if (cmd.startsWith("AI_WAKE") || cmd.startsWith("STARTUP_SOUND")) {
    playAiStartupSound();
  }
  else if (cmd.startsWith("SET_LIGHT:")) {
    int state = cmd.substring(10).toInt();
    autoLightEnabled = false;
    lightState = (state == 1);
    digitalWrite(ROOM_LIGHT_PIN, lightState ? HIGH : LOW);
  }
  else if (cmd.startsWith("AUTO_LIGHT:")) {
    int state = cmd.substring(11).toInt();
    autoLightEnabled = (state == 1);
  }
  else if (cmd.startsWith("SET_AC_LED:")) {
    int state = cmd.substring(11).toInt();
    acLedState = (state == 1);
    digitalWrite(AC_LED_PIN, acLedState ? HIGH : LOW);
  }
  else if (cmd.startsWith("SET_ALERT:")) {
    int state = cmd.substring(10).toInt();
    alertLedState = (state == 1);
    digitalWrite(ALERT_PIN, alertLedState ? HIGH : LOW);
  }
  else if (cmd.startsWith("BEEP:")) {
    String payload = cmd.substring(5);
    int sep = payload.indexOf('|');
    int freq = (sep != -1) ? payload.substring(0, sep).toInt() : 2000;
    int duration = (sep != -1) ? payload.substring(sep + 1).toInt() : 250;
    chirpBuzzer(freq, duration);
  }
  if (commandId.length() > 0) {
    Serial.print("ACK:");
    Serial.println(commandId);
  }
}

void setup() {
  Serial.begin(115200);

  // Pin Configuration
  pinMode(ROOM_LIGHT_PIN, OUTPUT);
  pinMode(ALERT_PIN, OUTPUT);
  pinMode(AC_LED_PIN, OUTPUT);
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(BUILTIN_LED, OUTPUT);
  pinMode(LDR_PIN, INPUT);

  digitalWrite(ROOM_LIGHT_PIN, LOW);
  digitalWrite(ALERT_PIN, LOW);
  digitalWrite(AC_LED_PIN, LOW);
  digitalWrite(BUZZER_PIN, LOW);

  // Initialize LCD
  Wire.begin();
  lcd.init();
  lcd.backlight();
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("AI Brain Online");
  lcd.setCursor(0, 1);
  lcd.print("Sensors Active  ");

  // Initialize DHT11
  dht.begin();

  // Play AI Startup Sound
  playAiStartupSound();

  delay(1200);
  lcd.clear();
}

void loop() {
  // 1. Process incoming Serial RPC Commands
  if (Serial.available() > 0) {
    String cmd = Serial.readStringUntil('\n');
    parseSerialCommand(cmd);
  }

  // 2. Handle Multi-Page LCD Message Paging Engine
  if (isPagingMessage) {
    if (millis() - lastPageFlip >= PAGE_DURATION) {
      lastPageFlip = millis();
      currentPage++;
      if (currentPage >= totalPages) {
        // Finished reading the entire message!
        isPagingMessage = false;
        lcd.clear();
      } else {
        renderCurrentPage();
        // Gentle tick audio indicator on page turn
        chirpBuzzer(2800, 20);
      }
    }
  }
  // 3. Clear single-page LCD override if expired
  else if (isLcdOverridden && millis() > lcdOverrideUntil) {
    isLcdOverridden = false;
    lcd.clear();
  }

  // 4. Read LDR Ambient Light (every 200ms) with LDR < 50 threshold & hysteresis
  if (millis() - lastLdrCheck >= 200) {
    lastLdrCheck = millis();
    currentLdr = analogRead(LDR_PIN);

    // Autonomous Light Control: Turn ON if LDR < 50, Turn OFF if LDR > 70
    if (autoLightEnabled) {
      if (currentLdr < LDR_DARK_THRESHOLD && !lightState) {
        lightState = true;
        digitalWrite(ROOM_LIGHT_PIN, HIGH);
        chirpBuzzer(2200, 70);
      } else if (currentLdr > LDR_LIGHT_THRESHOLD && lightState) {
        lightState = false;
        digitalWrite(ROOM_LIGHT_PIN, LOW);
      }
    }
    isDark = (currentLdr < LDR_DARK_THRESHOLD);
  }

  // 5. Read DHT11 Temperature & Humidity (every 1500ms)
  if (millis() - lastDhtCheck >= 1500) {
    lastDhtCheck = millis();
    float t = dht.readTemperature();
    float h = dht.readHumidity();

    if (!isnan(t) && t > -20.0 && t < 70.0) {
      currentTemp = t;
    }
    if (!isnan(h) && h >= 0.0 && h <= 100.0) {
      currentHum = h;
    }

    // Local comfort heuristic: AC recommended if warm/humid
    if (currentTemp >= 28.0 || (currentTemp >= 26.0 && currentHum >= 65.0)) {
      acLedState = true;
      digitalWrite(AC_LED_PIN, HIGH);
    } else {
      acLedState = false;
      digitalWrite(AC_LED_PIN, LOW);
    }

    // Update Default LCD Screen only if NOT showing an AI message or override
    if (!isPagingMessage && !isLcdOverridden) {
      lcd.setCursor(0, 0);
      char line0[17];
      snprintf(line0, sizeof(line0), "T:%2dC H:%2d%% L:%s", 
               (int)currentTemp, (int)currentHum, lightState ? "ON " : "OFF");
      lcd.print(line0);

      lcd.setCursor(0, 1);
      if (acLedState) {
        lcd.print("AI: Turn ON AC! ");
      } else if (isDark) {
        lcd.print("AI: Dark (L<50) ");
      } else {
        lcd.print("AI: Optimal Cond");
      }
    }
  }

  // 6. Emit Periodic JSON Telemetry over USB (every 1000ms)
  if (millis() - lastTelemetry >= 1000) {
    lastTelemetry = millis();
    Serial.print("{\"temp\":");
    Serial.print(currentTemp, 1);
    Serial.print(",\"hum\":");
    Serial.print(currentHum, 1);
    Serial.print(",\"ldr\":");
    Serial.print(currentLdr);
    Serial.print(",\"light\":");
    Serial.print(lightState ? 1 : 0);
    Serial.print(",\"ac_led\":");
    Serial.print(acLedState ? 1 : 0);
    Serial.print(",\"alert_led\":");
    Serial.print(alertLedState ? 1 : 0);
    Serial.print(",\"auto_light\":");
    Serial.print(autoLightEnabled ? 1 : 0);
    Serial.println("}");
  }
}
