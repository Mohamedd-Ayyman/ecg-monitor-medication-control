"""ECG Monitor & Automated Medication Control System.

PyQt5 desktop app that streams ECG records from the PhysioNet MIT-BIH
Arrhythmia Database, classifies beats from their annotations, detects
bradycardia/tachycardia/arrhythmias against configurable thresholds, and
controls an Arduino-based bedside unit (buzzer, LED, medication pump) over
serial. Critical events trigger optional Pushover push notifications.
"""
import os
import sys
import time
import threading
import numpy as np
from scipy import signal
from PyQt5 import QtCore, QtGui, QtWidgets
from PyQt5.QtWidgets import QMessageBox, QDialog, QVBoxLayout, QLabel, QFileDialog
import serial
import requests
import pandas as pd
from datetime import datetime
from pyqtgraph import PlotWidget
import wfdb
from serial.tools import list_ports

PUSHOVER_USER_KEY = os.getenv("PUSHOVER_USER_KEY", "")
PUSHOVER_API_TOKEN = os.getenv("PUSHOVER_API_TOKEN", "")
DEFAULT_RECORD = os.getenv("ECG_RECORD_PATH", os.path.join("data", "mit-bih-arrhythmia-database-1.0.0", "100"))


class ArrhythmiaNotification(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("URGENT MEDICAL ALERT")
        self.setMinimumSize(400, 200)
        self.setWindowFlags(self.windowFlags() |
                            QtCore.Qt.WindowStaysOnTopHint)

        layout = QVBoxLayout(self)

        warning_label = QLabel("⚠️ ARRHYTHMIA DETECTED! ⚠️")
        warning_label.setStyleSheet(
            "font-size: 24px; font-weight: bold; color: red;")
        warning_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(warning_label)

        message = "Abnormal heart rhythm detected.\nMedical staff has been notified."

        details_label = QLabel(message)
        details_label.setStyleSheet("font-size: 18px;")
        details_label.setAlignment(QtCore.Qt.AlignCenter)
        layout.addWidget(details_label)

        self.setStyleSheet("""
            QDialog {
                background-color: #FFEEEE;
                border: 2px solid red;
            }
        """)


class ECGMonitor(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("ECG Monitor and Medication Control System")
        self.resize(1200, 700)

        self.serial_port = None
        self.hardware_connected = False
        self.is_pump_active = False
        self.is_buzzer_active = False
        self.is_led_active = False

        self.ecg_data = []
        self.current_index = 0
        self.threshold_hr_low = 60
        self.threshold_hr_high = 100
        self.arrhythmia_detected = False
        self.ecg_timer = QtCore.QTimer()
        self.ecg_timer.timeout.connect(self.update_ecg)
        self.ecg_timer.setInterval(100)

        self.sample_rate = 360
        self.last_notification_time = 0
        self.notification_cooldown = 60
        self.notification_dialog = None

        self.temperature = 0.0
        self.pulse_rate = 0
        self.sensor_timer = QtCore.QTimer()
        self.sensor_timer.timeout.connect(self.read_arduino_sensors)
        self.sensor_timer.setInterval(1000)

        self.setup_ui()

        self.monitor_thread = None
        self.stop_thread = False

        self.record_name = None
        self.record_data = None
        self.annotation = None
        self.current_ecg_class = "N"

    def setup_ui(self):
        main_layout = QtWidgets.QVBoxLayout(self)

        control_panel = QtWidgets.QHBoxLayout()

        hardware_group = QtWidgets.QGroupBox("Hardware Control")
        hardware_layout = QtWidgets.QVBoxLayout(hardware_group)

        com_layout = QtWidgets.QHBoxLayout()
        com_label = QtWidgets.QLabel("COM Port:")
        self.com_port_combo = QtWidgets.QComboBox()
        ports = [p.device for p in list_ports.comports()]
        self.com_port_combo.addItems(ports or [f"COM{i}" for i in range(1, 11)])
        self.com_port_combo.setEditable(True)
        self.connect_button = QtWidgets.QPushButton("Connect")
        self.connect_button.clicked.connect(self.toggle_hardware_connection)
        com_layout.addWidget(com_label)
        com_layout.addWidget(self.com_port_combo)
        com_layout.addWidget(self.connect_button)
        hardware_layout.addLayout(com_layout)

        manual_controls = QtWidgets.QHBoxLayout()

        self.pump_button = QtWidgets.QPushButton("Pump: OFF")
        self.pump_button.clicked.connect(self.toggle_pump)
        self.pump_button.setEnabled(False)
        self.not_button = QtWidgets.QPushButton("Send Test Notification")
        self.not_button.clicked.connect(lambda: self.send_pushover_notification(
            message="Test alert from ECG Monitor",
            user_key=PUSHOVER_USER_KEY,
            api_token=PUSHOVER_API_TOKEN,
            title="ECG Alert"
        ))
        self.not_button.setEnabled(True)

        manual_controls.addWidget(self.pump_button)
        manual_controls.addWidget(self.not_button)
        hardware_layout.addLayout(manual_controls)

        sensor_layout = QtWidgets.QHBoxLayout()
        temp_layout = QtWidgets.QHBoxLayout()
        temp_layout.addWidget(QtWidgets.QLabel("Temperature:"))
        self.temp_value_label = QtWidgets.QLabel("-- °C")
        self.temp_value_label.setStyleSheet(
            "font-size: 16px; font-weight: bold;")
        temp_layout.addWidget(self.temp_value_label)

        pulse_layout = QtWidgets.QHBoxLayout()
        pulse_layout.addWidget(QtWidgets.QLabel("Pulse Sensor:"))
        self.pulse_value_label = QtWidgets.QLabel("-- BPM")
        self.pulse_value_label.setStyleSheet(
            "font-size: 16px; font-weight: bold;")
        pulse_layout.addWidget(self.pulse_value_label)

        sensor_layout.addLayout(temp_layout)
        sensor_layout.addLayout(pulse_layout)
        hardware_layout.addLayout(sensor_layout)

        control_panel.addWidget(hardware_group)

        patient_group = QtWidgets.QGroupBox("Patient Control")
        patient_layout = QtWidgets.QVBoxLayout(patient_group)

        threshold_layout = QtWidgets.QHBoxLayout()
        threshold_layout.addWidget(QtWidgets.QLabel("HR Thresholds (bpm):"))

        threshold_layout.addWidget(QtWidgets.QLabel("Low"))
        self.hr_low_input = QtWidgets.QSpinBox()
        self.hr_low_input.setRange(30, 100)
        self.hr_low_input.setValue(60)
        self.hr_low_input.valueChanged.connect(self.update_thresholds)
        threshold_layout.addWidget(self.hr_low_input)

        threshold_layout.addWidget(QtWidgets.QLabel("High"))
        self.hr_high_input = QtWidgets.QSpinBox()
        self.hr_high_input.setRange(60, 200)
        self.hr_high_input.setValue(100)
        self.hr_high_input.valueChanged.connect(self.update_thresholds)
        threshold_layout.addWidget(self.hr_high_input)

        patient_layout.addLayout(threshold_layout)

        ecg_controls = QtWidgets.QHBoxLayout()
        self.start_ecg_button = QtWidgets.QPushButton("Start ECG Monitoring")
        self.start_ecg_button.clicked.connect(self.toggle_ecg_monitoring)
        self.start_ecg_button.setEnabled(False)

        self.load_ecg_file_button = QtWidgets.QPushButton("Load ECG Dataset")
        self.load_ecg_file_button.clicked.connect(self.load_ecg_dataset)

        ecg_controls.addWidget(self.start_ecg_button)
        ecg_controls.addWidget(self.load_ecg_file_button)
        patient_layout.addLayout(ecg_controls)

        control_panel.addWidget(patient_group)
        main_layout.addLayout(control_panel)

        center_layout = QtWidgets.QHBoxLayout()

        ecg_view_group = QtWidgets.QGroupBox("ECG Monitor")
        ecg_view_layout = QtWidgets.QVBoxLayout(ecg_view_group)

        self.ecg_plot = PlotWidget()
        self.ecg_plot.setBackground('w')
        self.ecg_plot.setTitle("ECG Signal")
        self.ecg_plot.setLabel('left', 'Amplitude (mV)')
        self.ecg_plot.setLabel('bottom', 'Time (s)')
        self.ecg_plot.showGrid(x=True, y=True)
        self.ecg_plot.setYRange(-1.5, 1.5)
        self.ecg_curve = self.ecg_plot.plot(pen='b', width=2)

        ecg_view_layout.addWidget(self.ecg_plot)
        center_layout.addWidget(ecg_view_group, 3)

        status_group = QtWidgets.QGroupBox("Patient Status")
        status_layout = QtWidgets.QVBoxLayout(status_group)

        self.patient_info_label = QtWidgets.QLabel("Monitoring Active")
        self.patient_info_label.setAlignment(QtCore.Qt.AlignCenter)
        self.patient_info_label.setStyleSheet(
            "font-size: 14px; font-weight: bold;")
        status_layout.addWidget(self.patient_info_label)

        hr_layout = QtWidgets.QHBoxLayout()
        hr_layout.addWidget(QtWidgets.QLabel("Heart Rate:"))
        self.hr_value_label = QtWidgets.QLabel("-- bpm")
        self.hr_value_label.setStyleSheet(
            "font-size: 18px; font-weight: bold;")
        hr_layout.addWidget(self.hr_value_label)
        status_layout.addLayout(hr_layout)

        ecg_class_layout = QtWidgets.QHBoxLayout()
        ecg_class_layout.addWidget(QtWidgets.QLabel("ECG Classification:"))
        self.ecg_class_label = QtWidgets.QLabel("Normal")
        self.ecg_class_label.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: green;")
        ecg_class_layout.addWidget(self.ecg_class_label)
        status_layout.addLayout(ecg_class_layout)

        self.status_label = QtWidgets.QLabel("Status: Normal")
        self.status_label.setStyleSheet(
            "font-size: 16px; font-weight: bold; color: green;")
        self.status_label.setAlignment(QtCore.Qt.AlignCenter)
        status_layout.addWidget(self.status_label)

        self.log_text = QtWidgets.QTextEdit()
        self.log_text.setReadOnly(True)
        status_layout.addWidget(QtWidgets.QLabel("Event Log:"))
        status_layout.addWidget(self.log_text)

        center_layout.addWidget(status_group, 1)
        main_layout.addLayout(center_layout)

        self.status_bar = QtWidgets.QStatusBar()
        main_layout.addWidget(self.status_bar)
        self.status_bar.showMessage("System ready")

        self.log_message("System initialized and ready")

    def send_pushover_notification(self, message, user_key, api_token, title="Alert"):
        if not user_key or not api_token:
            self.log_message("Pushover not configured (set PUSHOVER_USER_KEY / PUSHOVER_API_TOKEN); alert logged only")
            return
        url = "https://api.pushover.net/1/messages.json"
        data = {
            "token": api_token,
            "user": user_key,
            "message": message,
            "title": title
        }

        response = requests.post(url, data=data, timeout=10)
        if response.status_code == 200:
            print(" Notification sent successfully.")
        else:
            print(" Failed to send notification:", response.text)

    def toggle_hardware_connection(self):
        if not self.hardware_connected:
            try:
                port = self.com_port_combo.currentText()
                self.serial_port = serial.Serial(
                    port, baudrate=9600, timeout=1)

                time.sleep(2)

                if self.serial_port and self.serial_port.is_open:
                    self.serial_port.write(b"TEST\n")
                    time.sleep(0.5)
                    response = self.serial_port.readline().decode('utf-8').strip()

                    if not response or "OK" not in response:
                        raise Exception("Arduino not responding correctly")

                self.hardware_connected = True
                self.connect_button.setText("Disconnect")
                self.pump_button.setEnabled(True)
                self.start_ecg_button.setEnabled(True)
                self.log_message(f"Connected to hardware on {port}")
                self.status_bar.showMessage(f"Connected to hardware on {port}")

                self.sensor_timer.start()

            except Exception as e:
                if self.serial_port:
                    self.serial_port.close()
                QMessageBox.critical(
                    self, "Connection Error", f"Could not connect to Arduino: {str(e)}")
                self.log_message(f"Hardware connection failed: {str(e)}")
        else:
            self.sensor_timer.stop()
            if self.serial_port:
                self.serial_port.close()
            self.hardware_connected = False
            self.connect_button.setText("Connect")
            self.pump_button.setEnabled(False)
            self.start_ecg_button.setEnabled(False)
            self.log_message("Disconnected from hardware")
            self.status_bar.showMessage("Disconnected from hardware")

            if self.ecg_timer.isActive():
                self.toggle_ecg_monitoring()

    def read_arduino_sensors(self):
        if self.hardware_connected and self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.write(b"GET_SENSORS\n")
                time.sleep(0.1)

                response = self.serial_port.readline().decode('utf-8').strip()
                if response and ":" in response:
                    try:
                        parts = response.split(":")
                        if len(parts) >= 2:
                            temp_str, pulse_str = parts[0], parts[1]
                            self.temperature = float(temp_str)
                            self.pulse_rate = int(pulse_str)

                            self.temp_value_label.setText(
                                f"{self.temperature:.1f} °C")
                            self.pulse_value_label.setText(
                                f"{self.pulse_rate} BPM")

                            if self.pulse_rate < self.threshold_hr_low or self.pulse_rate > self.threshold_hr_high:
                                self.temp_value_label.setStyleSheet(
                                    "font-size: 16px; font-weight: bold; color: red;")
                                self.pulse_value_label.setStyleSheet(
                                    "font-size: 16px; font-weight: bold; color: red;")
                            else:
                                self.temp_value_label.setStyleSheet(
                                    "font-size: 16px; font-weight: bold; color: green;")
                                self.pulse_value_label.setStyleSheet(
                                    "font-size: 16px; font-weight: bold; color: green;")
                    except Exception as e:
                        self.log_message(
                            f"Error parsing sensor data: {str(e)}")
            except Exception as e:
                self.log_message(f"Error reading sensors: {str(e)}")

    def toggle_pump(self):
        self.is_pump_active = not self.is_pump_active
        if self.is_pump_active:
            self.pump_button.setText("Pump: ON")
            self.send_hardware_command("PUMP_ON")
            self.log_message("Medication pump activated")
        else:
            self.pump_button.setText("Pump: OFF")
            self.send_hardware_command("PUMP_OFF")
            self.log_message("Medication pump deactivated")

    def send_hardware_command(self, command):
        if self.hardware_connected and self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.write(f"{command}\n".encode())
                time.sleep(0.1)
                response = self.serial_port.readline().decode('utf-8').strip()
                if response:
                    self.log_message(f"Arduino response: {response}")
                else:
                    self.log_message("No response from Arduino")
            except Exception as e:
                self.log_message(f"Error sending command: {e}")

    def update_thresholds(self):
        self.threshold_hr_low = self.hr_low_input.value()
        self.threshold_hr_high = self.hr_high_input.value()
        self.log_message(
            f"Updated HR thresholds: {self.threshold_hr_low}-{self.threshold_hr_high} bpm")

    def toggle_ecg_monitoring(self):
        if not self.ecg_timer.isActive():
            if not self.record_data:
                QMessageBox.warning(
                    self, "No Data", "Please load an ECG dataset first")
                return

            self.ecg_timer.start()
            self.start_ecg_button.setText("Stop ECG Monitoring")
            self.log_message("ECG monitoring started")

            self.stop_thread = False
            self.monitor_thread = threading.Thread(
                target=self.arrhythmia_detection_thread)
            self.monitor_thread.daemon = True
            self.monitor_thread.start()
        else:
            self.ecg_timer.stop()
            self.start_ecg_button.setText("Start ECG Monitoring")
            self.log_message("ECG monitoring stopped")

            self.stop_thread = True
            if self.monitor_thread:
                self.monitor_thread.join(timeout=1.0)

            if self.is_pump_active:
                self.toggle_pump()
            self.send_hardware_command("BUZZER_OFF")
            self.send_hardware_command("LED_OFF")

    def load_ecg_dataset(self):
        try:
            path, _ = QFileDialog.getOpenFileName(
                self, "Select a PhysioNet record header (.hea)", os.path.dirname(DEFAULT_RECORD) or ".",
                "WFDB header (*.hea)")
            record_name = os.path.splitext(path)[0] if path else DEFAULT_RECORD
            self.record_name = record_name

            self.record_data = wfdb.rdrecord(record_name)
            self.annotation = wfdb.rdann(record_name, 'atr')

            self.log_message(f"Loaded record: {self.record_name}")
            self.log_message(
                f"Signal shape: {self.record_data.p_signal.shape}")
            self.log_message(f"Annotations: {len(self.annotation.symbol)}")

            self.status_bar.showMessage(
                f"PhysioNet record {self.record_name} loaded successfully")
            self.start_ecg_button.setEnabled(True)

        except Exception as e:
            QMessageBox.critical(
                self, "Error", f"Could not load ECG data: {str(e)}")
            self.log_message(f"Error loading ECG data: {str(e)}")

    def update_ecg(self):
        if not self.record_data or not self.annotation:
            return

        window_size = 10  # seconds
        samples_per_window = int(window_size * self.record_data.fs)

        signal_length = self.record_data.p_signal.shape[0]
        start_idx = self.current_index % max(
            1, signal_length - samples_per_window)
        end_idx = min(start_idx + samples_per_window, signal_length)

        times = np.arange(start_idx, end_idx) / self.record_data.fs

        # Use the first channel of the ECG signal (typically lead II)
        values = self.record_data.p_signal[start_idx:end_idx, 0]

        self.ecg_curve.setData(times, values)

        # Find current annotation
        current_ann_idx = np.searchsorted(
            self.annotation.sample, start_idx) - 1
        current_ann_idx = max(0, current_ann_idx)

        # Get the current annotation symbol
        current_symbol = self.annotation.symbol[current_ann_idx] if current_ann_idx < len(
            self.annotation.symbol) else 'N'

        # Update the ECG classification
        self.update_ecg_classification(current_symbol)

        # Calculate heart rate
        heart_rate = self.calculate_heart_rate_from_annotation(current_ann_idx)
        self.hr_value_label.setText(f"{heart_rate} bpm")

        # Update patient status
        self.update_patient_status(heart_rate)

        # Move to next segment
        step_size = int(self.record_data.fs * 0.1)  # Advance by 0.1 seconds
        self.current_index = (self.current_index + step_size) % signal_length

    def update_ecg_classification(self, symbol):
        # PhysioNet MIT-BIH annotation labels:
        # N: Normal beat
        # L: Left bundle branch block beat
        # R: Right bundle branch block beat
        # A: Atrial premature beat
        # a: Aberrated atrial premature beat
        # J: Nodal (junctional) premature beat
        # S: Supraventricular premature beat
        # V: Premature ventricular contraction
        # F: Fusion of ventricular and normal beat
        # [: Start of ventricular flutter/fibrillation
        # !: Ventricular flutter wave
        # ]: End of ventricular flutter/fibrillation
        # e: Atrial escape beat
        # j: Nodal (junctional) escape beat
        # E: Ventricular escape beat
        # /: Paced beat
        # f: Fusion of paced and normal beat
        # Q: Unclassifiable beat

        if symbol in ['N', 'L', 'R', '.']:
            self.current_ecg_class = "Normal"
            self.ecg_class_label.setText("Normal")
            self.ecg_class_label.setStyleSheet(
                "font-size: 16px; font-weight: bold; color: green;")
            self.arrhythmia_detected = False
        elif symbol in ['A', 'a', 'J', 'S']:
            self.current_ecg_class = "Atrial Arrhythmia"
            self.ecg_class_label.setText("Atrial Arrhythmia")
            self.ecg_class_label.setStyleSheet(
                "font-size: 16px; font-weight: bold; color: orange;")
            self.arrhythmia_detected = True
        elif symbol in ['V', 'F', '[', '!', ']']:
            self.current_ecg_class = "Ventricular Arrhythmia"
            self.ecg_class_label.setText("Ventricular Arrhythmia")
            self.ecg_class_label.setStyleSheet(
                "font-size: 16px; font-weight: bold; color: red;")
            self.arrhythmia_detected = True
        elif symbol in ['e', 'j', 'E']:
            self.current_ecg_class = "Escape Beat"
            self.ecg_class_label.setText("Escape Beat")
            self.ecg_class_label.setStyleSheet(
                "font-size: 16px; font-weight: bold; color: orange;")
            self.arrhythmia_detected = True
        elif symbol in ['/', 'f', 'Q']:
            self.current_ecg_class = "Other"
            self.ecg_class_label.setText("Other")
            self.ecg_class_label.setStyleSheet(
                "font-size: 16px; font-weight: bold; color: blue;")
            self.arrhythmia_detected = False
        else:
            self.current_ecg_class = "Unknown"
            self.ecg_class_label.setText("Unknown")
            self.ecg_class_label.setStyleSheet(
                "font-size: 16px; font-weight: bold; color: gray;")
            self.arrhythmia_detected = False

    def calculate_heart_rate_from_annotation(self, ann_idx):
        if not self.annotation or ann_idx >= len(self.annotation.sample) or ann_idx < 0:
            return 75  # Default value

        # Try to calculate heart rate based on RR intervals
        # Find the next annotation after current
        next_idx = ann_idx + 1

        if next_idx < len(self.annotation.sample):
            # Calculate the RR interval in seconds
            rr_interval = (
                self.annotation.sample[next_idx] - self.annotation.sample[ann_idx]) / self.record_data.fs
            if rr_interval > 0:
                hr = int(60 / rr_interval)
                return max(30, min(200, hr))  # Limit to reasonable range

        # If we can't calculate from RR, use these defaults based on annotation type
        symbol = self.annotation.symbol[ann_idx]

        if symbol in ['N', 'L', 'R', '.']:
            return int(np.random.normal(75, 5))
        elif symbol in ['A', 'a', 'J', 'S']:
            return int(np.random.normal(110, 10))
        elif symbol in ['V', 'F', '[', '!', ']']:
            return int(np.random.normal(140, 15))
        elif symbol in ['e', 'j', 'E']:
            return int(np.random.normal(50, 5))
        else:
            return int(np.random.normal(80, 10))

    def update_patient_status(self, heart_rate):
        if self.current_ecg_class == "Ventricular Arrhythmia":
            status = "VENTRICULAR ARRHYTHMIA"
            color = "red"
            self.arrhythmia_detected = True
        elif self.current_ecg_class == "Atrial Arrhythmia":
            status = "ATRIAL ARRHYTHMIA"
            color = "orange"
            self.arrhythmia_detected = True
        elif heart_rate < self.threshold_hr_low:
            status = "BRADYCARDIA"
            color = "red"
            self.arrhythmia_detected = True
        elif heart_rate > self.threshold_hr_high:
            status = "TACHYCARDIA"
            color = "red"
            self.arrhythmia_detected = True
        else:
            status = "Normal"
            color = "green"
            if self.current_ecg_class == "Normal":
                self.arrhythmia_detected = False

        self.status_label.setText(f"Status: {status}")
        self.status_label.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {color};")

        if self.arrhythmia_detected and color == "red":
            self.trigger_arrhythmia_response()

    def arrhythmia_detection_thread(self):
        while not self.stop_thread:
            if self.arrhythmia_detected:
                current_time = time.time()

                if current_time - self.last_notification_time > self.notification_cooldown:
                    self.send_notification()
                    self.last_notification_time = current_time

            time.sleep(1)

    def trigger_arrhythmia_response(self):
        # Rate-limit the response so a sustained event does not flood staff with alerts
        now = time.time()
        if now - self.last_notification_time < self.notification_cooldown:
            return
        self.last_notification_time = now

        self.send_hardware_command("BUZZER_ON")
        self.send_hardware_command("LED_ON")
        if not self.is_pump_active:
            self.toggle_pump()
            self.log_message(
                "CRITICAL: Automated medication delivery initiated")

        self.log_message("ALERT: Arrhythmia detected! Medical staff notified.")

        self.send_notification()
        self.show_notification_dialog()

    def send_notification(self):
        notification_text = "ALERT: Arrhythmia detected. Immediate attention required!"

        try:
            print(f"Sending notification: {notification_text}")
            self.send_pushover_notification(
                message=notification_text,
                user_key=PUSHOVER_USER_KEY,
                api_token=PUSHOVER_API_TOKEN,
                title="ECG Alert"
            )
            self.log_message(f"Notification sent to medical staff")

        except Exception as e:
            self.log_message(f"Failed to send notification: {str(e)}")

    def show_notification_dialog(self):
        if not self.notification_dialog or not self.notification_dialog.isVisible():
            self.notification_dialog = ArrhythmiaNotification(self)
            self.notification_dialog.show()

    def log_message(self, message):
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.log_text.append(f"[{timestamp}] {message}")
        self.log_text.verticalScrollBar().setValue(
            self.log_text.verticalScrollBar().maximum())

    def closeEvent(self, event):
        self.stop_thread = True
        if self.monitor_thread and self.monitor_thread.is_alive():
            self.monitor_thread.join(timeout=1.0)

        if self.ecg_timer.isActive():
            self.ecg_timer.stop()

        if self.sensor_timer.isActive():
            self.sensor_timer.stop()

        if self.is_pump_active:
            self.send_hardware_command("PUMP_OFF")

        if self.serial_port and self.serial_port.is_open:
            self.serial_port.close()

        event.accept()


if __name__ == "__main__":
    app = QtWidgets.QApplication(sys.argv)
    window = ECGMonitor()
    window.show()
    sys.exit(app.exec_())
