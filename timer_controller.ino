/*
 * Robotics Tournament Timer Controller v2
 * ESP32 + MAX7219 (5x Seven Segment Displays)
 * 
 * Wiring:
 *   MAX7219 DIN  -> ESP32 GPIO 23 (MOSI)
 *   MAX7219 CLK  -> ESP32 GPIO 18 (SCK)
 *   MAX7219 CS   -> ESP32 GPIO 5
 *   Sensor (IR/Beam Break) -> ESP32 GPIO 4 (active LOW with internal pullup)
 *   Buzzer -> ESP32 GPIO 2
 * 
 * Serial Protocol (115200 baud):
 *   Commands FROM PC:
 *     READY          - Arm the timer (sensor can now trigger start)
 *     STOP           - Stop the timer  
 *     RESET          - Reset timer to 0
 *     START          - Manual start (spacebar backup)
 *     COUNTDOWN:XXX  - Set countdown duration to XXX seconds
 *     BEEP_START     - 3-2-1 beep sequence then start countdown
 *     DISPLAY:TEXT   - Show raw text on displays
 *     BRIGHTNESS:X   - Set brightness 0-15
 *     PING           - Connection test
 *   
 *   Messages TO PC:
 *     TIME:XXXXXXX   - Current time in milliseconds (sent every 50ms while running)
 *     FINAL:XXXXXXX  - Final time when stopped (ms) — this is the AUTHORITATIVE time
 *     TRIGGERED      - Sensor triggered start
 *     COUNTDOWN_END  - Countdown reached zero
 *     READY_ACK      - Timer armed acknowledgment
 *     PONG           - Reply to PING
 *     STATUS:xxxx    - Status updates (BEEP_3, BEEP_2, BEEP_1, BEEP_GO, RESET, DNF, etc.)
 */

#include <SPI.h>

#define MAX7219_CS    5
#define SENSOR_PIN    4
#define BUZZER_PIN    2

#define REG_NOOP      0x00
#define REG_DIGIT0    0x01
#define REG_DIGIT1    0x02
#define REG_DIGIT2    0x03
#define REG_DIGIT3    0x04
#define REG_DIGIT4    0x05
#define REG_DECODE    0x09
#define REG_INTENSITY 0x0A
#define REG_SCANLIMIT 0x0B
#define REG_SHUTDOWN  0x0C
#define REG_TEST      0x0F

// 7-segment encoding (no BCD decode): bits = DP A B C D E F G
const byte CHAR_MAP[] = {
  0b01111110, // 0
  0b00110000, // 1
  0b01101101, // 2
  0b01111001, // 3
  0b00110011, // 4
  0b01011011, // 5
  0b01011111, // 6
  0b01110000, // 7
  0b01111111, // 8
  0b01111011, // 9
};
const byte CHAR_DASH  = 0b00000001;
const byte CHAR_BLANK = 0b00000000;
const byte CHAR_D     = 0b00111101;
const byte CHAR_N     = 0b00010101;
const byte CHAR_F     = 0b01000111;

enum TimerState {
  STATE_IDLE,
  STATE_READY,
  STATE_RUNNING_UP,
  STATE_RUNNING_DOWN,
  STATE_BEEP_SEQUENCE,
  STATE_STOPPED,
  STATE_COUNTDOWN_END
};

volatile TimerState timerState = STATE_IDLE;
volatile unsigned long startTimeMs = 0;
volatile unsigned long elapsedMs = 0;
volatile unsigned long finalTimeMs = 0;
unsigned long countdownTotalMs = 180000;
unsigned long lastSerialSend = 0;
unsigned long lastDisplayUpdate = 0;

int beepStep = 0;
unsigned long beepStepTime = 0;

volatile unsigned long lastSensorTrigger = 0;
#define SENSOR_DEBOUNCE_MS 500

String serialBuffer = "";

void max7219Send(byte reg, byte data) {
  digitalWrite(MAX7219_CS, LOW);
  SPI.transfer(reg);
  SPI.transfer(data);
  digitalWrite(MAX7219_CS, HIGH);
}

void max7219Init() {
  max7219Send(REG_SHUTDOWN, 0x01);
  max7219Send(REG_TEST, 0x00);
  max7219Send(REG_DECODE, 0x00);
  max7219Send(REG_SCANLIMIT, 0x04);
  max7219Send(REG_INTENSITY, 0x08);
}

void displayClear() {
  for (int i = 1; i <= 5; i++) max7219Send(i, CHAR_BLANK);
}

void displayTime(unsigned long ms) {
  unsigned long totalSec = ms / 1000;
  unsigned long minutes = totalSec / 60;
  unsigned long seconds = totalSec % 60;
  unsigned long millis_part = ms % 1000;
  
  if (minutes == 0) {
    // SS.mmm
    byte d5 = (seconds / 10 == 0) ? CHAR_BLANK : CHAR_MAP[seconds / 10];
    byte d4 = CHAR_MAP[seconds % 10] | 0x80;
    byte d3 = CHAR_MAP[millis_part / 100];
    byte d2 = CHAR_MAP[(millis_part / 10) % 10];
    byte d1 = CHAR_MAP[millis_part % 10];
    max7219Send(REG_DIGIT4, d5);
    max7219Send(REG_DIGIT3, d4);
    max7219Send(REG_DIGIT2, d3);
    max7219Send(REG_DIGIT1, d2);
    max7219Send(REG_DIGIT0, d1);
  } else {
    // M:SS.mm
    byte d5 = CHAR_MAP[minutes % 10] | 0x80;
    byte d4 = CHAR_MAP[seconds / 10] | 0x80;
    byte d3 = CHAR_MAP[seconds % 10];
    byte d2 = CHAR_MAP[millis_part / 100] | 0x80;
    byte d1 = CHAR_MAP[(millis_part / 10) % 10];
    max7219Send(REG_DIGIT4, d5);
    max7219Send(REG_DIGIT3, d4);
    max7219Send(REG_DIGIT2, d3);
    max7219Send(REG_DIGIT1, d2);
    max7219Send(REG_DIGIT0, d1);
  }
}

void displayDashes() {
  for (int i = 1; i <= 5; i++) max7219Send(i, CHAR_DASH);
}

void displayDNF() {
  max7219Send(REG_DIGIT4, CHAR_D);
  max7219Send(REG_DIGIT3, CHAR_N);
  max7219Send(REG_DIGIT2, CHAR_F);
  max7219Send(REG_DIGIT1, CHAR_BLANK);
  max7219Send(REG_DIGIT0, CHAR_BLANK);
}

void beepShort() { tone(BUZZER_PIN, 2000, 200); }
void beepLong()  { tone(BUZZER_PIN, 3000, 800); }

void IRAM_ATTR sensorISR() {
  unsigned long now = millis();
  if (now - lastSensorTrigger < SENSOR_DEBOUNCE_MS) return;
  lastSensorTrigger = now;
  if (timerState == STATE_READY) {
    timerState = STATE_RUNNING_UP;
    startTimeMs = millis();
  }
}

void setup() {
  Serial.begin(115200);
  pinMode(MAX7219_CS, OUTPUT);
  digitalWrite(MAX7219_CS, HIGH);
  pinMode(SENSOR_PIN, INPUT_PULLUP);
  pinMode(BUZZER_PIN, OUTPUT);
  SPI.begin();
  SPI.setFrequency(1000000);
  max7219Init();
  displayDashes();
  attachInterrupt(digitalPinToInterrupt(SENSOR_PIN), sensorISR, FALLING);
  Serial.println("STATUS:BOOT_OK");
  Serial.println("PONG");
}

void loop() {
  unsigned long now = millis();

  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (serialBuffer.length() > 0) {
        processCommand(serialBuffer);
        serialBuffer = "";
      }
    } else {
      serialBuffer += c;
    }
  }

  switch (timerState) {
    case STATE_IDLE:
      break;

    case STATE_READY:
      if (now - lastDisplayUpdate > 200) {
        displayTime(0);
        lastDisplayUpdate = now;
      }
      break;

    case STATE_RUNNING_UP: {
      elapsedMs = now - startTimeMs;
      if (now - lastDisplayUpdate >= 10) {
        displayTime(elapsedMs);
        lastDisplayUpdate = now;
      }
      if (now - lastSerialSend >= 50) {
        Serial.print("TIME:");
        Serial.println(elapsedMs);
        lastSerialSend = now;
      }
      break;
    }

    case STATE_RUNNING_DOWN: {
      elapsedMs = now - startTimeMs;
      long remaining = (long)countdownTotalMs - (long)elapsedMs;
      if (remaining <= 0) {
        remaining = 0;
        timerState = STATE_COUNTDOWN_END;
        finalTimeMs = countdownTotalMs;
        displayTime(0);
        beepLong();
        Serial.println("COUNTDOWN_END");
        Serial.print("FINAL:");
        Serial.println(countdownTotalMs);
        break;
      }
      if (now - lastDisplayUpdate >= 10) {
        displayTime((unsigned long)remaining);
        lastDisplayUpdate = now;
      }
      if (now - lastSerialSend >= 50) {
        Serial.print("TIME:");
        Serial.println((unsigned long)remaining);
        lastSerialSend = now;
      }
      // Warning beeps
      if (remaining <= 10000 && remaining > 0) {
        static unsigned long lastBeepAt = 0;
        unsigned long interval = (remaining <= 3000) ? 500 : 1000;
        if (now - lastBeepAt >= interval) {
          beepShort();
          lastBeepAt = now;
        }
      }
      break;
    }

    case STATE_BEEP_SEQUENCE: {
      unsigned long elapsed = now - beepStepTime;
      if (beepStep == 0) {
        beepShort();
        Serial.println("STATUS:BEEP_3");
        beepStep = 1;
        beepStepTime = now;
      } else if (beepStep == 1 && elapsed >= 1000) {
        beepShort();
        Serial.println("STATUS:BEEP_2");
        beepStep = 2;
        beepStepTime = now;
      } else if (beepStep == 2 && elapsed >= 1000) {
        beepShort();
        Serial.println("STATUS:BEEP_1");
        beepStep = 3;
        beepStepTime = now;
      } else if (beepStep == 3 && elapsed >= 1000) {
        beepLong();
        Serial.println("STATUS:BEEP_GO");
        timerState = STATE_RUNNING_DOWN;
        startTimeMs = now;
        lastSerialSend = now;
        lastDisplayUpdate = now;
      }
      break;
    }

    case STATE_STOPPED:
    case STATE_COUNTDOWN_END:
      break;
  }
}

void processCommand(String cmd) {
  cmd.trim();
  cmd.toUpperCase();

  if (cmd == "PING") {
    Serial.println("PONG");
  } else if (cmd == "READY") {
    timerState = STATE_READY;
    elapsedMs = 0;
    displayTime(0);
    Serial.println("READY_ACK");
    beepShort();
  } else if (cmd == "START") {
    if (timerState == STATE_READY || timerState == STATE_IDLE) {
      timerState = STATE_RUNNING_UP;
      startTimeMs = millis();
      Serial.println("TRIGGERED");
      beepShort();
    }
  } else if (cmd == "STOP") {
    if (timerState == STATE_RUNNING_UP) {
      finalTimeMs = millis() - startTimeMs;
      timerState = STATE_STOPPED;
      displayTime(finalTimeMs);
      Serial.print("FINAL:");
      Serial.println(finalTimeMs);
      beepShort();
    } else if (timerState == STATE_RUNNING_DOWN) {
      elapsedMs = millis() - startTimeMs;
      long remaining = (long)countdownTotalMs - (long)elapsedMs;
      if (remaining < 0) remaining = 0;
      finalTimeMs = (unsigned long)remaining;
      timerState = STATE_STOPPED;
      displayTime(finalTimeMs);
      Serial.print("FINAL:");
      Serial.println(finalTimeMs);
      beepShort();
    } else if (timerState == STATE_BEEP_SEQUENCE) {
      timerState = STATE_IDLE;
      displayDashes();
      Serial.println("STATUS:CANCELLED");
    }
  } else if (cmd == "RESET") {
    timerState = STATE_IDLE;
    elapsedMs = 0;
    finalTimeMs = 0;
    displayDashes();
    Serial.println("STATUS:RESET");
  } else if (cmd.startsWith("COUNTDOWN:")) {
    unsigned long secs = cmd.substring(10).toInt();
    if (secs > 0) {
      countdownTotalMs = secs * 1000UL;
      displayTime(countdownTotalMs);
      Serial.print("STATUS:COUNTDOWN_SET:");
      Serial.println(secs);
    }
  } else if (cmd == "BEEP_START") {
    timerState = STATE_BEEP_SEQUENCE;
    beepStep = 0;
    beepStepTime = millis();
    displayTime(countdownTotalMs);
    Serial.println("STATUS:BEEP_STARTING");
  } else if (cmd.startsWith("BRIGHTNESS:")) {
    int b = cmd.substring(11).toInt();
    if (b >= 0 && b <= 15) max7219Send(REG_INTENSITY, b);
  } else if (cmd == "DNF") {
    timerState = STATE_STOPPED;
    displayDNF();
    Serial.println("STATUS:DNF");
  }
}
