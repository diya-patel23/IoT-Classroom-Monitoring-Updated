#!/usr/bin/env python3
"""Smart Classroom Monitor - Raspberry Pi publisher and room LED controller.

Uses two PIR sensors, two HC-SR04 entrance sensors, a digital LDR,
an occupancy indicator LED (GPIO18), and a website-controlled room LED (GPIO13).
Temperature and humidity remain None because the DHT sensor is not used.
"""

import csv
import json
import os
import threading
import time
from datetime import datetime

import RPi.GPIO as GPIO
import paho.mqtt.client as mqtt

# --------------------------- GPIO (BCM numbering) ---------------------------
PIR1_PIN = 17
PIR2_PIN = 27
TRIG1_PIN, ECHO1_PIN = 23, 24       # Outside ultrasonic sensor
TRIG2_PIN, ECHO2_PIN = 5, 6         # Inside ultrasonic sensor
LDR_PIN = 22
OCCUPANCY_LED_PIN = 18              # Existing outside indicator LED
ROOM_LED_PIN = 13                   # New LED above the LDR

# ------------------------------- Settings ----------------------------------
DISTANCE_THRESHOLD = 20             # cm
SEQUENCE_TIMEOUT = 3.0              # seconds
SENSOR_GAP = 0.06
MOTION_TIMEOUT = 60
LDR_BRIGHT_STATE = GPIO.LOW         # Verify this against the actual LDR module
INITIAL_OCCUPANCY = 0
PUBLISH_INTERVAL = 0.5
ROOM_LED_START_ON = os.environ.get("ROOM_LED_START_ON", "1") == "1"  # Set to 0 to start OFF
CSV_FILE = "classroom_data.csv"

# ------------------------------- MQTT --------------------------------------
BROKER_IP = os.environ.get("MQTT_BROKER_IP", "10.1.107.244")
MQTT_PORT = int(os.environ.get("MQTT_PORT", "1883"))
MQTT_USERNAME = os.environ.get("MQTT_USERNAME", "pi_user")
MQTT_PASSWORD = os.environ.get("MQTT_PASSWORD", "YOUR_NEW_PASSWORD")
MQTT_SENSOR_TOPIC = "classroom/room1/sensors"
MQTT_LED_COMMAND_TOPIC = "classroom/room1/led/set"
MQTT_CLIENT_ID = "raspberry_pi_classroom"

CSV_HEADERS = [
    "Timestamp", "PIR1", "PIR2", "Distance1_cm", "Distance2_cm",
    "Temperature_C", "Humidity_percent", "LDR_Digital", "Occupancy_Count",
    "Occupancy", "Entry_Exit_Event", "Light_Status", "Room_LED_Status", "Alert"
]

# ----------------------------- GPIO setup ----------------------------------
room_led_on = ROOM_LED_START_ON
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup(PIR1_PIN, GPIO.IN)
GPIO.setup(PIR2_PIN, GPIO.IN)
GPIO.setup(TRIG1_PIN, GPIO.OUT)
GPIO.setup(ECHO1_PIN, GPIO.IN)
GPIO.setup(TRIG2_PIN, GPIO.OUT)
GPIO.setup(ECHO2_PIN, GPIO.IN)
GPIO.setup(LDR_PIN, GPIO.IN, pull_up_down=GPIO.PUD_UP)
GPIO.setup(OCCUPANCY_LED_PIN, GPIO.OUT)
GPIO.setup(ROOM_LED_PIN, GPIO.OUT)
GPIO.output(TRIG1_PIN, GPIO.LOW)
GPIO.output(TRIG2_PIN, GPIO.LOW)
GPIO.output(OCCUPANCY_LED_PIN, GPIO.LOW)
GPIO.output(ROOM_LED_PIN, GPIO.HIGH if room_led_on else GPIO.LOW)
time.sleep(0.5)

# --------------------------- Occupancy state -------------------------------
occupancy_count = max(0, INITIAL_OCCUPANCY)
sequence = None
sequence_start_time = 0.0
previous_sensor1 = False
previous_sensor2 = False
last_motion_time = 0.0
room_led_on = ROOM_LED_START_ON
state_lock = threading.RLock()

# ----------------------------- MQTT client ---------------------------------
mqtt_client = mqtt.Client(
    mqtt.CallbackAPIVersion.VERSION2,
    client_id=MQTT_CLIENT_ID,
)
mqtt_client.username_pw_set(MQTT_USERNAME, MQTT_PASSWORD)
mqtt_client.reconnect_delay_set(min_delay=1, max_delay=30)


def publish_room_led_state():
    """Publish the Pi's actual room LED GPIO state."""
    if mqtt_client.is_connected():
        mqtt_client.publish(
            MQTT_SENSOR_TOPIC,
            json.dumps({
                "room_id": "room1",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "room_light_led": bool(room_led_on),
                "occupancy_count": occupancy_count,
                "occupancy": occupancy_count > 0,
            }),
            qos=1,
        )


def on_connect(client, userdata, flags, reason_code, properties):
    if reason_code == 0:
        print("MQTT connected successfully")
        client.subscribe(MQTT_LED_COMMAND_TOPIC, qos=1)
        print("Subscribed to:", MQTT_LED_COMMAND_TOPIC)
    else:
        print("MQTT connection failed:", reason_code)


def on_disconnect(client, userdata, disconnect_flags, reason_code, properties):
    print("MQTT disconnected:", reason_code)


def on_message(client, userdata, msg):
    """Handle website room-light commands, enforcing occupancy on the Pi."""
    global room_led_on
    if msg.topic != MQTT_LED_COMMAND_TOPIC:
        return
    try:
        command_data = json.loads(msg.payload.decode("utf-8"))
        command = str(command_data.get("command", "")).strip().upper()
    except (UnicodeDecodeError, json.JSONDecodeError):
        print("Ignored invalid room LED command")
        return

    if command != "OFF":
        print("Ignored unsupported room LED command:", command)
        return

    with state_lock:
        if occupancy_count > 0:
            print("Room LED OFF rejected: classroom is occupied")
            client.publish(
                MQTT_SENSOR_TOPIC,
                json.dumps({
                    "room_id": "room1",
                    "timestamp": datetime.now().isoformat(timespec="seconds"),
                    "room_light_led": room_led_on,
                    "occupancy_count": occupancy_count,
                    "occupancy": True,
                    "led_command_result": "REJECTED_OCCUPIED",
                }), qos=1,
            )
            return
        GPIO.output(ROOM_LED_PIN, GPIO.LOW)
        room_led_on = False
        print("Room LED switched OFF from website")
        client.publish(
            MQTT_SENSOR_TOPIC,
            json.dumps({
                "room_id": "room1",
                "timestamp": datetime.now().isoformat(timespec="seconds"),
                "room_light_led": False,
                "occupancy_count": occupancy_count,
                "occupancy": False,
                "led_command_result": "OFF_SUCCESS",
            }), qos=1,
        )


mqtt_client.on_connect = on_connect
mqtt_client.on_disconnect = on_disconnect
mqtt_client.on_message = on_message

# -------------------------- Sensor functions -------------------------------
def get_distance(trigger_pin, echo_pin):
    GPIO.output(trigger_pin, GPIO.LOW)
    time.sleep(0.002)
    GPIO.output(trigger_pin, GPIO.HIGH)
    time.sleep(0.00001)
    GPIO.output(trigger_pin, GPIO.LOW)

    start_wait = time.monotonic()
    while GPIO.input(echo_pin) == GPIO.LOW:
        if time.monotonic() - start_wait > 0.03:
            return None
    pulse_start = time.monotonic()
    while GPIO.input(echo_pin) == GPIO.HIGH:
        if time.monotonic() - pulse_start > 0.03:
            return None
    pulse_end = time.monotonic()
    distance = ((pulse_end - pulse_start) * 34300) / 2
    return round(distance, 2) if 2 <= distance <= 400 else None


def is_detected(distance):
    return distance is not None and distance < DISTANCE_THRESHOLD


def update_occupancy(sensor1_detected, sensor2_detected):
    global occupancy_count, previous_sensor1, previous_sensor2
    global sequence, sequence_start_time
    now = time.monotonic()
    event = None
    with state_lock:
        sensor1_edge = sensor1_detected and not previous_sensor1
        sensor2_edge = sensor2_detected and not previous_sensor2
        previous_sensor1 = sensor1_detected
        previous_sensor2 = sensor2_detected

        if sequence is not None and now - sequence_start_time > SEQUENCE_TIMEOUT:
            sequence = None

        if sensor1_edge:
            if sequence == "SENSOR2_FIRST":
                occupancy_count = max(0, occupancy_count - 1)
                event, sequence = "EXIT", None
            elif sequence is None:
                sequence, sequence_start_time = "SENSOR1_FIRST", now

        if sensor2_edge and event is None:
            if sequence == "SENSOR1_FIRST":
                occupancy_count += 1
                event, sequence = "ENTRY", None
            elif sequence is None:
                sequence, sequence_start_time = "SENSOR2_FIRST", now

    if event:
        print(f"\n{event} DETECTED! Current occupancy: {occupancy_count}")
    return event


def read_pir():
    global last_motion_time
    pir1 = GPIO.input(PIR1_PIN)
    pir2 = GPIO.input(PIR2_PIN)
    if pir1 or pir2:
        last_motion_time = time.monotonic()
    recent_motion = time.monotonic() - last_motion_time < MOTION_TIMEOUT
    return pir1, pir2, recent_motion


def read_light():
    ldr_value = GPIO.input(LDR_PIN)
    light_status = "Bright" if ldr_value == LDR_BRIGHT_STATE else "Dark"
    return ldr_value, light_status

# ------------------------------ CSV setup ----------------------------------
if not os.path.exists(CSV_FILE):
    with open(CSV_FILE, "w", newline="") as file:
        csv.writer(file).writerow(CSV_HEADERS)

# -------------------------------- Main -------------------------------------
try:
    print("\nInitializing Smart Classroom Monitoring System...")
    print("Allowing PIR sensors to stabilize for 30 seconds...")
    time.sleep(30)

    initial_distance1 = get_distance(TRIG1_PIN, ECHO1_PIN)
    time.sleep(SENSOR_GAP)
    initial_distance2 = get_distance(TRIG2_PIN, ECHO2_PIN)
    previous_sensor1 = is_detected(initial_distance1)
    previous_sensor2 = is_detected(initial_distance2)

    if MQTT_PASSWORD == "YOUR_NEW_PASSWORD":
        print("WARNING: Set MQTT_PASSWORD environment variable to your new broker password.")

    mqtt_client.connect(BROKER_IP, MQTT_PORT, 60)
    mqtt_client.loop_start()

    print("Smart Classroom Monitoring Started")
    print("Initial occupancy:", occupancy_count)
    print("Outside occupancy LED: GPIO18")
    print("Website-controlled room LED: GPIO13 -", "ON" if room_led_on else "OFF")

    while True:
        timestamp = datetime.now().isoformat(timespec="seconds")
        distance1 = get_distance(TRIG1_PIN, ECHO1_PIN)
        time.sleep(SENSOR_GAP)
        distance2 = get_distance(TRIG2_PIN, ECHO2_PIN)
        sensor1_detected = is_detected(distance1)
        sensor2_detected = is_detected(distance2)

        entry_exit_event = update_occupancy(sensor1_detected, sensor2_detected)
        with state_lock:
            occupied = occupancy_count > 0
            current_count = occupancy_count
            # Preserve the existing outside indicator behavior.
            GPIO.output(OCCUPANCY_LED_PIN, GPIO.HIGH if occupied else GPIO.LOW)

        pir1, pir2, recent_motion = read_pir()
        ldr_digital, light_status = read_light()

        # Temperature and humidity are intentionally not used.
        temperature_c = None
        humidity_percent = None

        alert = "LIGHT LIKELY ON WHILE ROOM EMPTY" if (not occupied and light_status == "Bright") else "No alert"

        payload = {
            "room_id": "room1",
            "timestamp": timestamp,
            "pir1": int(pir1),
            "pir2": int(pir2),
            "distance_cm": distance1,
            "distance1_cm": distance1,
            "distance2_cm": distance2,
            "temperature_c": temperature_c,
            "humidity_percent": humidity_percent,
            "ldr_digital": int(ldr_digital),
            "light_status": light_status,
            "occupancy": occupied,
            "occupancy_count": current_count,
            "entry_exit_event": entry_exit_event,
            "room_light_led": bool(room_led_on),
            "ac_status": None,
            "alert": alert,
        }

        if mqtt_client.is_connected():
            mqtt_client.publish(MQTT_SENSOR_TOPIC, json.dumps(payload), qos=1)

        with open(CSV_FILE, "a", newline="") as file:
            csv.writer(file).writerow([
                timestamp, pir1, pir2, distance1, distance2,
                temperature_c, humidity_percent, ldr_digital,
                current_count, "Occupied" if occupied else "Empty",
                entry_exit_event, light_status, "ON" if room_led_on else "OFF", alert,
            ])

        print("\n------------------------------------")
        print("Timestamp:", timestamp)
        print("PIR 1:", pir1, "| PIR 2:", pir2, "| Recent movement:", recent_motion)
        print("Ultrasonic 1:", distance1, "cm | Ultrasonic 2:", distance2, "cm")
        print("Entry/Exit event:", entry_exit_event)
        print("Occupancy count:", current_count, "|", "Occupied" if occupied else "Empty")
        print("LDR:", ldr_digital, "->", light_status)
        print("Outside occupancy LED:", "ON" if occupied else "OFF")
        print("Room LED:", "ON" if room_led_on else "OFF")
        print("Alert:", alert)
        print("------------------------------------")
        time.sleep(PUBLISH_INTERVAL)

except KeyboardInterrupt:
    print("\nStopping Smart Classroom Monitoring System...")
except Exception as error:
    print("System error:", error)
finally:
    try:
        GPIO.output(OCCUPANCY_LED_PIN, GPIO.LOW)
        GPIO.output(ROOM_LED_PIN, GPIO.LOW)
    except Exception:
        pass
    try:
        mqtt_client.loop_stop()
        mqtt_client.disconnect()
    except Exception:
        pass
    GPIO.cleanup()
    print("GPIO cleaned up. System stopped.")
