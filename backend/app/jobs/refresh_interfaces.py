# /app/backend/app/jobs/refresh_interfaces.py
from datetime import datetime
from app.netconf import get_interfaces_raw, store_interfaces_cache
from app.devices import load_devices
from app.database import SessionLocal, Base, engine

Base.metadata.create_all(bind=engine)

def refresh():
    db = SessionLocal()
    try:
        devices = load_devices()  # returns dict {name: {...}}
        for dev_name in devices.keys():
            try:
                print(f"[{datetime.utcnow()}] Refresh interfaces for {dev_name}")
                interfaces = get_interfaces_raw(dev_name)
                # reuse the existing store helper so DB schema stays consistent
                store_interfaces_cache(db, dev_name, interfaces)
                print(f"✔ done: {dev_name} ({len(interfaces)} interfaces)")
            except Exception as e:
                print(f"✖ failed {dev_name}: {e}")
    finally:
        db.close()

def main():
    refresh()

if __name__ == "__main__":
    main()
