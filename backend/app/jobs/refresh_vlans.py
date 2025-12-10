#refresh_vlans.py
from datetime import datetime
from app.database import SessionLocal, Base, engine
from app.devices import get_devices
from app.netconf import get_vlans
from app.models import CachedVlan

# safety-net: tables bestaan ook bij standalone job
Base.metadata.create_all(bind=engine)

def refresh():
    db = SessionLocal()
    try:
        for device in get_devices():
            name = device["name"]

            print(f"[{datetime.utcnow()}] Refresh VLANs for {name}")

            vlans = get_vlans(name)

            db.merge(CachedVlan(
                device=name,
                data=vlans,
                updated_at=datetime.utcnow(),
            ))

            db.commit()

            print(f"✔ VLANs stored: {name} ({len(vlans)})")

    finally:
        db.close()

def main():
    refresh()

if __name__ == "__main__":
    main()