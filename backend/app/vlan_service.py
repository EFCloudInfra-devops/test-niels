from datetime import datetime
from app.models import Vlan

def fetch_vlans_live(device):
    """
    Existing NETCONF call that returns:
    [{ "id": 10, "name": "USERS" }, ...]
    """
    return get_vlans_from_device(device)


def refresh_vlans(db, device):
    vlans = fetch_vlans_live(device)

    # clear old
    db.query(Vlan).filter(Vlan.device == device).delete()

    # store new
    for v in vlans:
        db.add(Vlan(
            device=device,
            vlan_id=v["id"],
            name=v["name"],
            fetched_at=datetime.utcnow()
        ))

    db.commit()
    return vlans
