# /app/backend/app/services.py
from .devices import load_devices

def list_devices():
    devs = load_devices()
    return [{"name": k} for k in devs.keys()]
