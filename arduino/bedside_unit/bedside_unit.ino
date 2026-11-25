#define BUZZER_PIN 3
#define LED_PIN 4
#define PUMP_RELAY_PIN 7

#define TEMP_SENSOR_PIN A1
#define PULSE_SENSOR_PIN A0

void setup() {
  Serial.begin(9600);

  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(LED_PIN, OUTPUT);
  pinMode(PUMP_RELAY_PIN, OUTPUT);

  digitalWrite(BUZZER_PIN, LOW);
  digitalWrite(LED_PIN, LOW);
  digitalWrite(PUMP_RELAY_PIN, HIGH);
}

void loop() {
  if (Serial.available()) {
    String command = Serial.readStringUntil('\n');
    command.trim();

    if (command == "TEST") {
      Serial.println("OK");
    }

    else if (command == "BUZZER_ON") {
      digitalWrite(BUZZER_PIN, HIGH);
      Serial.println("Buzzer ON");
    } else if (command == "BUZZER_OFF") {
      digitalWrite(BUZZER_PIN, LOW);
      Serial.println("Buzzer OFF");
    }

    else if (command == "LED_ON") {
      digitalWrite(LED_PIN, HIGH);
      Serial.println("LED ON");
    } else if (command == "LED_OFF") {
      digitalWrite(LED_PIN, LOW);
      Serial.println("LED OFF");
    }

    else if (command == "PUMP_ON") {
      digitalWrite(PUMP_RELAY_PIN, LOW);
      Serial.println("Pump ON");
    } else if (command == "PUMP_OFF") {
      digitalWrite(PUMP_RELAY_PIN, HIGH);
      Serial.println("Pump OFF");
    }

    else if (command == "GET_SENSORS") {
      float temperature = readTemperatureCelsius();
      int bpm = readPulseRate();
      Serial.print(temperature, 1);
      Serial.print(":");
      Serial.println(bpm);
    }

    else {
      Serial.println("UNKNOWN");
    }
  }
}

float readTemperatureCelsius() {
  int raw = analogRead(TEMP_SENSOR_PIN);
  float voltage = raw * (5.0 / 1023.0);
  float temperature = voltage * 100.0;
  return temperature;
}

int readPulseRate() {
  const int threshold = 512;
  int pulseCount = 0;
  bool pulseDetected = false;

  unsigned long startTime = millis();
  while (millis() - startTime < 1000) {
    int val = analogRead(PULSE_SENSOR_PIN);
    if (val > threshold && !pulseDetected) {
      pulseDetected = true;
      pulseCount++;
    } else if (val < threshold) {
      pulseDetected = false;
    }
  }

  return pulseCount * 60;
}
