# Room LED control update

## What changed

- The Raspberry Pi keeps the existing outside occupancy LED on BCM GPIO18.
- A separate small room LED above the LDR is controlled on BCM GPIO13 (physical pin 33).
- The Pi subscribes to `classroom/room1/led/set` and publishes sensor data to `classroom/room1/sensors`.
- The Flask dashboard shows the room LED state and offers **Turn OFF** only in Live mode when occupancy is zero and the room LED is ON.
- The Raspberry Pi checks `occupancy_count` again before switching the LED OFF. A website-side check alone is not relied on.
- Temperature and humidity remain `null` because no DHT sensor is being used.

## Wiring for the new small LED

1. BCM GPIO13 (physical pin 33) -> 220–330 ohm resistor -> LED long leg (anode).
2. LED short leg (cathode) -> GND (physical pin 34).
3. Keep the existing outside occupancy LED on BCM GPIO18 (physical pin 12).
4. Place the new LED so its light falls on the LDR.

This is only for a small low-current LED. Do not connect a room bulb or mains load directly to a Raspberry Pi GPIO.

## Start with the LED ON for the demo

By default, `ROOM_LED_START_ON=1`, so the room LED starts ON and the website can demonstrate switching it OFF while the classroom is empty. To start with it OFF instead, set `ROOM_LED_START_ON=0` in the Pi shell before running the script.

## Raspberry Pi setup

Install dependencies if they are not already installed:

```bash
sudo apt update
sudo apt install python3-rpi.gpio
python3 -m pip install paho-mqtt
```

Set the broker address to the laptop's current Wi-Fi IPv4 address, and set the NEW MQTT password that you configured in Mosquitto:

```bash
export MQTT_BROKER_IP="YOUR_LAPTOP_WIFI_IPV4"
export MQTT_USERNAME="pi_user"
export MQTT_PASSWORD="YOUR_NEW_PASSWORD"
python3 raspberry_pi/pi_smart_classroom.py
```

Run the script from the folder where you want `classroom_data.csv` to be created. Do not paste your password into chat or commit it to GitHub.

## Windows laptop / Flask website setup

In the PowerShell window used to start Flask, set the same broker credentials. The Flask backend connects to Mosquitto on the same laptop using `localhost`:

```powershell
$env:MQTT_BROKER_HOST = "localhost"
$env:MQTT_BROKER_PORT = "1883"
$env:MQTT_TOPIC = "classroom/room1/sensors"
$env:MQTT_USERNAME = "pi_user"
$env:MQTT_PASSWORD = "YOUR_NEW_PASSWORD"
python app.py
```

Open the website and change the operating mode from Demo to **Live**. The dashboard button will be enabled only after live sensor data reports occupancy 0 and `room_light_led` ON.

## Notes

- `BROKER_IP` must be the laptop's current Wi-Fi IPv4 address, not an old address. Check with `ipconfig` on Windows.
- Mosquitto must listen on port 1883 and allow the Pi's Wi-Fi subnet through Windows Firewall.
- The old MQTT password was present in an earlier code paste. Create a new Mosquitto password for `pi_user` and use the new one on both devices.
- The LDR was previously stuck LOW. This does not prevent the new LED GPIO state from being tracked, but the LDR's Bright/Dark reading will remain unreliable until its wiring/module behavior is fixed. The website LED ON/OFF status is tracked separately from the LDR.
- The occupancy algorithm assumes one person crosses the two entrance sensors at a time. Tailgating or simultaneous crossings can cause a count error.
- Occupancy starts from `INITIAL_OCCUPANCY=0` each time the Pi script starts. Set this to the actual count when starting with people already inside.
