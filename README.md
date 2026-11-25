<div align="center">

# 🫀 ECG Monitor & Automated Medication Control

**A bedside cardiac monitoring system that streams real ECG records, detects arrhythmias and abnormal heart rates in real time, and drives an Arduino bedside unit (alarm, indicator and medication pump), with push alerts to medical staff.**

![Python](https://img.shields.io/badge/Python-3776AB?style=for-the-badge&logo=python&logoColor=white)
![PyQt5](https://img.shields.io/badge/PyQt5-41CD52?style=for-the-badge&logo=qt&logoColor=white)
![Arduino](https://img.shields.io/badge/Arduino-00878F?style=for-the-badge&logo=arduino&logoColor=white)
![NumPy](https://img.shields.io/badge/NumPy-013243?style=for-the-badge&logo=numpy&logoColor=white)
![PhysioNet](https://img.shields.io/badge/PhysioNet-MIT--BIH-B31B1B?style=for-the-badge)

</div>

---

## 📖 Overview

Built for the **Biomedical Monitors** course at Cairo University's Faculty of Engineering, this project links software and hardware in one closed-loop patient-monitoring prototype:

1. **ECG streaming.** Records from the **MIT-BIH Arrhythmia Database** (PhysioNet) are played back in real time as a scrolling 10-second ECG trace.
2. **Beat classification.** Each beat is mapped from its expert annotation to *Normal, Atrial Arrhythmia, Ventricular Arrhythmia, Escape Beat* or *Other*, and heart rate is calculated from R-R intervals.
3. **Clinical rules.** Configurable bradycardia and tachycardia thresholds, together with the arrhythmia class, set the patient's status.
4. **Automated response.** On a critical event the system raises an on-screen alert, sends a **push notification** to staff and activates the **medication pump**, buzzer and alarm LED on the Arduino bedside unit.
5. **Live sensors.** The Arduino reports **body temperature** (LM35) and **pulse rate** (pulse sensor) over serial.

## ✨ Features

- 📈 Real-time ECG plotting with **pyqtgraph**, at 10 Hz refresh over a sliding 10 s window
- 🧠 MIT-BIH beat-label mapping (N, L, R, A, a, J, S, V, F, [, !, ], e, j, E, /, f, Q)
- ❤️ Heart-rate estimation from successive annotated beats
- 🎚️ Adjustable **low and high HR thresholds**
- 🚨 Status engine: *Normal · Bradycardia · Tachycardia · Atrial / Ventricular Arrhythmia*
- 🔔 **Pushover** notifications with a configurable cooldown, so a sustained event does not flood staff with alerts
- 🔌 Serial protocol with the Arduino: auto-detected COM ports, a connection self-test, and buzzer, LED and pump control
- 🌡️ Live temperature and pulse readings from hardware sensors
- 🧾 Timestamped event log for every alert and actuator action
- 🛡️ Safe shutdown: the pump is switched off and the serial port closed when the app exits

## 🏗️ Architecture

```
┌────────────────────────────┐          serial (9600 baud)         ┌──────────────────────────┐
│  PyQt5 desktop app         │  ── TEST / GET_SENSORS / PUMP_ON ─▶ │  Arduino bedside unit    │
│  • ECG plot (pyqtgraph)    │  ◀──── "36.8:78" / "OK" ─────────── │  • LM35 temperature (A1) │
│  • Beat classifier & rules │                                     │  • Pulse sensor (A0)     │
│  • Alert thread            │                                     │  • Buzzer (D3), LED (D4) │
└──────┬──────────────┬──────┘                                     │  • Pump relay (D7)       │
       │ wfdb         │ HTTPS                                      └──────────────────────────┘
       ▼              ▼
 MIT-BIH records   Pushover API → staff phones
```

### Serial command protocol

| Command | Response | Action |
|---|---|---|
| `TEST` | `OK` | Connection handshake |
| `GET_SENSORS` | `<temp>:<bpm>` | Read temperature (°C) and pulse rate |
| `BUZZER_ON` / `BUZZER_OFF` | text ack | Audible alarm |
| `LED_ON` / `LED_OFF` | text ack | Visual alarm |
| `PUMP_ON` / `PUMP_OFF` | text ack | Medication pump relay (active-low) |

## 🚀 Getting Started

```bash
git clone https://github.com/Mohamedd-Ayyman/ecg-monitor-medication-control.git
cd ecg-monitor-medication-control
pip install -r requirements.txt
```

1. **Get ECG data:** download the [MIT-BIH Arrhythmia Database](https://physionet.org/content/mitdb/1.0.0/) and extract it into `data/`.
2. **(Optional) Hardware:** flash `arduino/bedside_unit/bedside_unit.ino` to an Arduino Uno with the wiring shown above.
3. **(Optional) Alerts:** copy `.env.example` to `.env` and set your Pushover keys as environment variables.
4. **Run the app:**
   ```bash
   python ecg_monitor.py
   ```
5. Click **Load ECG Dataset**, choose a record (for example `100.hea` or `208.hea` for frequent PVCs), then click **Start ECG Monitoring**.

> The app works without hardware. Connect the Arduino to enable live sensors and the actuators.

## 🗂️ Project Structure

```
├── ecg_monitor.py                 # PyQt5 application (UI, classification, alerts, serial)
├── arduino/bedside_unit/          # Arduino firmware for sensors & actuators
├── requirements.txt
└── .env.example                   # Pushover / dataset configuration
```

## ⚕️ Disclaimer

This is an educational prototype. Beat classes come from the dataset's expert annotations; it is **not** a certified medical device.

## 👤 Author

**Mohamed Ayman** · [GitHub](https://github.com/Mohamedd-Ayyman) · [LinkedIn](https://linkedin.com/in/mohamed-ayman-8787b0269)
