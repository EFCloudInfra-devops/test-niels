# main.py
from fastapi import FastAPI, HTTPException, Depends, Header
from fastapi.responses import JSONResponse
from typing import Optional, List
from .devices import load_devices, get_device
from . import netconf
from . import models, schemas
from .database import SessionLocal, init_db, Base, engine
from sqlalchemy.orm import Session
import traceback
import json
from datetime import datetime
from .models import InterfaceCache, CachedVlan, AuditLog

def write_audit(
    db: Session,
    *,
    actor: str,
    action: str,
    device: str,
    interface: str | None = None,
    request_id: int | None = None,
    comment: str | None = None,
    payload: dict | None = None,
):
    entry = models.AuditLog(
        actor=actor,
        action=action,
        device=device,
        interface=interface,
        request_id=request_id,
        comment=comment,
        payload=payload,
    )
    db.add(entry)
    db.commit()


app = FastAPI()

# Initialize DB (creates tables if not present)
init_db()

# simple dependency: DB session
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# === Very simple "auth" dependency for demo ===
# Expects headers: X-User, X-Role
def get_current_user(x_user: Optional[str] = Header(None), x_role: Optional[str] = Header(None)):
    """
    Returns a dict with username & role. In production replace with real auth.
    """
    username = x_user or "anonymous"
    role = x_role or "reader"
    return {"username": username, "role": role}

def require_role(allowed=("admin", "approver")):
    def checker(user=Depends(get_current_user)):
        if user["role"] not in allowed:
            raise HTTPException(status_code=403, detail="Not allowed")
        return user
    return checker

# --- existing endpoints ---
@app.get("/api/inventory")
def inventory():
    devs = load_devices()
    return [{"name": k, "mgmt": v.get("host")} for k,v in devs.items()]

@app.get("/api/switches")
def switches_list():
    devs = load_devices()
    return [{"name": k} for k in devs.keys()]

@app.get("/api/switches/{device}/ping")
def ping_device(device: str):
    try:
        dev = get_device(device)
    except KeyError:
        raise HTTPException(404, "Unknown device")
    try:
        with netconf.connect(dev):
            return {"ok": True}
    except Exception:
        raise HTTPException(503, "NETCONF unreachable")

@app.get("/api/switches/{device}/interfaces")
def interfaces(device: str, db: Session = Depends(get_db)):
    data = netconf.get_interfaces_cached(device, db)

    return {
        "device": device,
        "source": data.get("source", "cache"),
        "retrieved_at": data["timestamp"],
        "interfaces": data["interfaces"]
    }

@app.get("/api/switches/{device}/interface/{ifname}/live")
def interface_live(device: str, ifname: str):
    try:
        return netconf.get_interface_live_cached(device, ifname)
    except KeyError:
        raise HTTPException(404, "Unknown device")
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(500, str(e))

@app.get("/api/switches/{device}/vlans")
def get_cached_vlans(device: str, db: Session = Depends(get_db)):

    row = (
        db.query(CachedVlan)
        .filter(CachedVlan.device == device)
        .one_or_none()
    )

    if not row:
        return {
            "device": device,
            "vlans": [],
            "cached": False
        }

    return {
        "device": device,
        "vlans": row.data,
        "cached": True,
        "updated_at": row.updated_at
    }

# === Change request endpoints ===

@app.post("/api/requests", response_model=schemas.ChangeRequestOut, status_code=201)
def create_request(req: schemas.ChangeRequestCreate, user=Depends(get_current_user), db: Session = Depends(get_db)):
    # persist request in DB
    cr = models.ChangeRequest(
        device=req.device,
        interface=req.interface,
        requester=req.requester or user.get("username"),
        config=req.config,
        status=models.RequestStatus.pending
    )
    db.add(cr)
    db.commit()
    db.refresh(cr)
    return cr

@app.get("/api/requests", response_model=List[schemas.ChangeRequestOut])
def list_requests(status: Optional[str] = None, db: Session = Depends(get_db), user=Depends(get_current_user)):
    q = db.query(models.ChangeRequest)
    if status:
        q = q.filter(models.ChangeRequest.status == status)
    # simple RBAC: non-approvers only see their own reqs
    if user["role"] not in ("approver", "admin"):
        q = q.filter(models.ChangeRequest.requester == user["username"])
    items = q.order_by(models.ChangeRequest.created_at.desc()).all()
    return items

@app.post("/api/requests/{req_id}/approve", status_code=200)
def approve_request(
    req_id: int,
    comment: Optional[str] = None,
    db: Session = Depends(get_db),
    user = Depends(require_role(("admin", "approver")))
):
    req = db.query(models.ChangeRequest).get(req_id)
    if not req:
        raise HTTPException(404, "Request not found")

    if req.status != models.RequestStatus.pending:
        raise HTTPException(400, "Request not pending")

    # ✅ mark approved
    req.status = models.RequestStatus.approved
    req.approver = user["username"]
    if comment:
        req.comment = comment

    db.commit()

    # ✅ audit: approved
    audit(
        db,
        event="approved",
        device=req.device,
        interface=req.interface,
        request_id=req.id,
        actor=user["username"],
        config=req.config,
        message=comment
    )

    # 🔐 APPLY
    try:
        device_info = get_device(req.device)

        with netconf.connect(device_info) as nc:
            netconf.apply_interface_config(
                nc,
                interface=req.interface,
                config=req.config
            )

        # ✅ audit: apply success
        audit(
            db,
            event="apply_success",
            device=req.device,
            interface=req.interface,
            request_id=req.id,
            actor="system",
            config=req.config
        )

    except Exception as e:
        req.status = "failed"
        req.comment = str(e)
        db.commit()

        # ❌ audit: apply failed
        audit(
            db,
            event="apply_failed",
            device=req.device,
            interface=req.interface,
            request_id=req.id,
            actor="system",
            config=req.config,
            message=str(e)
        )

        raise HTTPException(500, f"NETCONF apply failed: {e}")

    db.refresh(req)
    return req

@app.post("/api/requests/{req_id}/reject")
def reject_request(
    req_id: int,
    comment: Optional[str] = None,
    db: Session = Depends(get_db),
    user = Depends(require_role(("admin", "approver")))
):
    item = db.query(models.ChangeRequest).get(req_id)
    if not item:
        raise HTTPException(404, "Request not found")

    if item.status != models.RequestStatus.pending:
        raise HTTPException(400, "Request not pending")

    item.status = models.RequestStatus.rejected
    item.approver = user["username"]
    if comment:
        item.comment = comment

    db.commit()
    db.refresh(item)
    return item

@app.post("/api/switches/{device}/interfaces/retrieve")
def interfaces_retrieve(device: str, db: Session = Depends(get_db)):
    interfaces = netconf.get_interfaces_raw(device)

    for i in interfaces:
        i["_source"] = "live"

    netconf.store_interfaces_cache(
        db,
        device=device,
        interfaces=interfaces
    )

    return {
        "device": device,
        "source": "live",
        "retrieved_at": datetime.utcnow().isoformat(),
        "interfaces": interfaces
    }

@app.post("/api/switches/{device}/vlans/refresh")
def refresh_vlans(device: str, db: Session = Depends(get_db),
                  user=Depends(require_role(("admin","approver")))):

    vlans = netconf.get_vlans(device)

    db.merge(
        CachedVlan(
            device=device,
            data=vlans,
            updated_at=datetime.utcnow()
        )
    )
    db.commit()

    return {
        "device": device,
        "count": len(vlans),
        "status": "refreshed"
    }

@app.get("/api/audit")
def load_audit(
    device: Optional[str] = None,
    interface: Optional[str] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
    user = Depends(require_role(("admin", "approver")))
):
    q = db.query(AuditLog)

    if device:
        q = q.filter(AuditLog.device == device)
    if interface:
        q = q.filter(AuditLog.interface == interface)

    rows = (
        q.order_by(AuditLog.created_at.desc())
         .limit(limit)
         .all()
    )

    return [
        {
            "id": r.id,
            "event": r.event,
            "device": r.device,
            "interface": r.interface,
            "actor": r.actor,
            "message": r.message,
            "config": r.config,
            "created_at": r.created_at.isoformat()
        }
        for r in rows
    ]

@app.on_event("startup")
def normalize_interface_cache():
    db = SessionLocal()
    try:
        rows = db.query(InterfaceCache).all()
        for row in rows:
            changed = False
            for p in row.data:
                if "_source" not in p:
                    p["_source"] = "cache"
                    changed = True
            if changed:
                row.updated_at = datetime.utcnow()
        db.commit()
    finally:
        db.close()
