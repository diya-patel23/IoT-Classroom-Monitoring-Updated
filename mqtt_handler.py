
"""
Smart Classroom Monitor – MQTT Handler
=======================================
Subscribes to the Mosquitto broker and processes sensor data from the
Raspberry Pi. Uses paho-mqtt v2.x callback API.
"""

import json
import logging
import threading
from datetime import datetime, timezone

try:
    import paho.mqtt.client as mqtt
    PAHO_AVAILABLE = True
except ImportError:
    PAHO_AVAILABLE = False

import config

log = logging.getLogger(__name__)


class MQTTHandler:
    """Manages the MQTT connection, subscription and message processing."""

    def __init__(self, broker_host: str, broker_port: int, topic: str,
                 on_reading_callback):

        self.broker_host = broker_host
        self.broker_port = broker_port
        self.topic = topic
        self._callback = on_reading_callback
        self._connected = False
        self._lock = threading.Lock()
        self._client = None
        self._should_run = False

        if not PAHO_AVAILABLE:
            log.warning("paho-mqtt is not installed – MQTT will be unavailable")

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def is_connected(self) -> bool:
        with self._lock:
            return self._connected

    def connect(self):
        """Start the MQTT client loop in a background thread."""

        if not PAHO_AVAILABLE:
            log.error("Cannot connect: paho-mqtt not installed")
            return

        if self._client is not None:
            self.disconnect()

        self._should_run = True

        try:
            self._client = mqtt.Client(
                mqtt.CallbackAPIVersion.VERSION2,
                client_id=config.MQTT_CLIENT_ID,
            )

            self._client.on_connect = self._on_connect
            self._client.on_disconnect = self._on_disconnect
            self._client.on_message = self._on_message

            if config.MQTT_USERNAME and config.MQTT_PASSWORD:
                self._client.username_pw_set(
                    config.MQTT_USERNAME,
                    config.MQTT_PASSWORD
                )

            log.info(
                "Connecting to MQTT broker at %s:%s ...",
                self.broker_host,
                self.broker_port
            )

            self._client.connect_async(
                self.broker_host,
                self.broker_port,
                keepalive=config.MQTT_KEEPALIVE
            )

            self._client.loop_start()

        except Exception:
            log.exception("MQTT connect failed")
            with self._lock:
                self._connected = False

    def disconnect(self):
        """Cleanly disconnect and stop the background loop."""

        self._should_run = False

        if self._client is not None:
            try:
                self._client.loop_stop()
                self._client.disconnect()
            except Exception:
                log.exception("Error during MQTT disconnect")

            self._client = None

        with self._lock:
            self._connected = False

        log.info("MQTT disconnected")

    def publish_room_led_off(self) -> bool:
        """Send an OFF command to the Raspberry Pi."""

        if not self.is_connected or self._client is None:
            log.warning("Cannot send LED command: MQTT disconnected")
            return False

        try:
            payload = json.dumps({"command": "OFF"})

            result = self._client.publish(
                config.MQTT_LED_COMMAND_TOPIC,
                payload,
                qos=1
            )

            return result.rc == mqtt.MQTT_ERR_SUCCESS

        except Exception:
            log.exception("Could not publish room LED command")
            return False

    # ------------------------------------------------------------------
    # MQTT callbacks (paho-mqtt v2)
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code,
                properties=None):

        if reason_code == 0:
            log.info("MQTT connected")

            # Raspberry Pi sensor data
            client.subscribe(self.topic)

            # Arduino Uno DHT11 data
            client.subscribe(config.MQTT_ENVIRONMENT_TOPIC)

            log.info("Subscribed to Pi topic: %s", self.topic)
            log.info(
                "Subscribed to Arduino topic: %s",
                config.MQTT_ENVIRONMENT_TOPIC
            )

            with self._lock:
                self._connected = True

        else:
            log.warning("MQTT connection failed: %s", reason_code)

            with self._lock:
                self._connected = False

    def _on_disconnect(self, client, userdata, flags, reason_code,
                       properties=None):

        with self._lock:
            self._connected = False

        if reason_code != 0:
            log.warning(
                "MQTT unexpected disconnect (rc=%s). Will reconnect...",
                reason_code
            )
        else:
            log.info("MQTT clean disconnect")


    def _on_message(self, client, userdata, msg):

        try:
            payload = msg.payload.decode("utf-8")
            data = json.loads(payload)

        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            log.warning("Invalid MQTT payload: %s", exc)
            return

        # Arduino Uno DHT11 message
        if msg.topic == config.MQTT_ENVIRONMENT_TOPIC:

            reading = self._validate_environment(data)

            if reading is not None:
                log.info(
                    "Arduino DHT11: Temperature=%s°C, Humidity=%s%%",
                    reading["temperature"],
                    reading["humidity"]
                )

                if self._callback:
                    try:
                        self._callback(reading)
                    except Exception:
                        log.exception("Arduino callback error")

            return

        # Raspberry Pi sensor message
        reading = self._validate(data)

        if reading is not None:
            log.info(
                "Raspberry Pi data: occupancy=%s, room LED=%s",
                reading.get("occupancy_count"),
                "ON" if reading.get("room_light_led") else "OFF"
            )

            if self._callback:
                try:
                    self._callback(reading)
                except Exception:
                    log.exception("Pi callback error")

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_environment(data: dict) -> dict | None:
        """Validate Arduino Uno DHT11 readings."""

        if not isinstance(data, dict):
            return None

        try:
            temperature = float(data.get("temperature_c"))
            humidity = float(data.get("humidity_percent"))

            if not (-40 <= temperature <= 80):
                return None

            if not (0 <= humidity <= 100):
                return None

        except (ValueError, TypeError):
            return None

        timestamp = data.get("timestamp")

        if timestamp:
            try:
                datetime.fromisoformat(str(timestamp))
            except ValueError:
                timestamp = datetime.now(timezone.utc).isoformat()
        else:
            timestamp = datetime.now(timezone.utc).isoformat()

        return {
            "temperature": temperature,
            "humidity": humidity,
            "environment_timestamp": str(timestamp),
            "environment_sensor": "DHT11"
        }
        
    @staticmethod
    def _validate(data: dict) -> dict | None:
        """Normalise the Raspberry Pi sensor payload."""

        if not isinstance(data, dict):
            return None

        reading = {}

        # Timestamp
        ts = data.get("timestamp")

        if ts:
            try:
                datetime.fromisoformat(str(ts))
                reading["timestamp"] = str(ts)
            except ValueError:
                reading["timestamp"] = datetime.now(
                    timezone.utc
                ).isoformat()
        else:
            reading["timestamp"] = datetime.now(
                timezone.utc
            ).isoformat()

        # Temperature
        temp = data.get("temperature_c", data.get("temperature"))

        try:
            temp = float(temp) if temp is not None else None
            reading["temperature"] = (
                temp if temp is not None and -40 <= temp <= 80
                else None
            )
        except (ValueError, TypeError):
            reading["temperature"] = None

        # Humidity
        hum = data.get("humidity_percent", data.get("humidity"))

        try:
            hum = float(hum) if hum is not None else None
            reading["humidity"] = (
                hum if hum is not None and 0 <= hum <= 100
                else None
            )
        except (ValueError, TypeError):
            reading["humidity"] = None

        # LDR light readings
        light = data.get("light_status", data.get("light"))
        light_raw = data.get("ldr_digital", data.get("light_raw"))

        reading["light"] = light if isinstance(light, str) else None

        try:
            reading["light_raw"] = (
                int(light_raw) if light_raw is not None else None
            )
        except (ValueError, TypeError):
            reading["light_raw"] = None

        # Distance
        dist = data.get(
            "distance1_cm",
            data.get("distance_cm", data.get("distance"))
        )

        try:
            dist = float(dist) if dist is not None else None
            reading["distance"] = (
                dist if dist is not None and 0 <= dist <= 400
                else None
            )
        except (ValueError, TypeError):
            reading["distance"] = None

        # PIR sensors
        for key in ("pir1", "pir2"):
            val = data.get(key)
            reading[key] = bool(val) if val is not None else None

        # Additional classroom information
        reading["room_id"] = data.get("room_id", "room1")

        reading["distance1_cm"] = data.get(
            "distance1_cm",
            data.get("distance_cm")
        )

        reading["distance2_cm"] = data.get("distance2_cm")

        try:
            reading["occupancy_count"] = max(
                0, int(data.get("occupancy_count", 0) or 0)
            )
        except (ValueError, TypeError):
            reading["occupancy_count"] = 0

        reading["occupancy"] = bool(
            data.get(
                "occupancy",
                reading["occupancy_count"] > 0
            )
        )

        reading["entry_exit_event"] = data.get("entry_exit_event")

        # IMPORTANT: Read the LED state from the decoded JSON dictionary.
        reading["room_light_led"] = bool(
            data.get("room_light_led", False)
        )

        reading["ac_status"] = data.get("ac_status")
        reading["alert"] = data.get("alert", "No alert")
        reading["led_command_result"] = data.get("led_command_result")

        return reading

    @staticmethod
    def _classify_light(raw_value: int) -> str:
        """Classify a raw LDR value into Dark / Moderate / Bright."""

        from database import get_setting

        bright_th = get_setting("light_bright_threshold") or 700
        mod_th = get_setting("light_moderate_threshold") or 300

        if raw_value >= bright_th:
            return "Bright"
        elif raw_value >= mod_th:
            return "Moderate"
        else:
            return "Dark"
