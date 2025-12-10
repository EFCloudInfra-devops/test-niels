# /app/backend/app/devices.py
import os, json

DEVICES_JSON = os.getenv("NETCONF_DEVICES_JSON", "/app/devices.json")

def load_devices() -> dict:
    """
    Return dict mapping device-name -> device dict (host, username, password, port).
    Raises descriptive errors if not configured.
    """
    if not DEVICES_JSON:
        raise ValueError("NETCONF_DEVICES_JSON not configured")
    if not os.path.exists(DEVICES_JSON):
        raise FileNotFoundError(f"Devices JSON not found: {DEVICES_JSON}")
    with open(DEVICES_JSON) as fh:
        data = json.load(fh)
    # data should be { "switch01": { "host": "...", "username": "...", "password": "..."} }
    return data

def get_device(name: str) -> dict:
    devs = load_devices()
    if name not in devs:
        raise KeyError(f"Unknown device: {name}")
    return devs[name]
