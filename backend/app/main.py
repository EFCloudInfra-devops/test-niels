# main.py
from fastapi import FastAPI, HTTPException, Depends, Header, Query
from fastapi.responses import JSONResponse, PlainTextResponse
from typing import Optional, List
from .devices import load_devices, get_device
from . import netconf, models, schemas
from .database import SessionLocal, init_db, Base, engine
from sqlalchemy.orm import Session
import traceback
import json
from datetime import datetime
from .models import InterfaceCache, CachedVlan, AuditLog
import xml.sax.saxutils as sax

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

    # mark approved
    req.status = models.RequestStatus.approved
    req.approver = user["username"]
    if comment:
        req.comment = comment

    write_audit(
        db,
        actor=user["username"],
        action="approve",
        device=req.device,
        interface=req.interface,
        request_id=req.id,
        comment=comment,
        payload={"status": "approved", "type": req.type}
    )

    db.commit()

    # ----------------------------
    # APPLY
    # ----------------------------
    try:
        dev = get_device(req.device)

        # ---- DELETE FLOW ----
        if getattr(req, "type", None) == "delete":
            with netconf.connect(dev) as nc:
                xml = f"""
                <config>
                  <configuration>
                    <interfaces>
                      <interface operation="delete">
                        <name>{sax.escape(req.interface)}</name>
                      </interface>
                    </interfaces>
                  </configuration>
                </config>
                """
                nc.edit_config(target="candidate", config=xml)
                nc.commit()

            write_audit(
                db,
                actor="system",
                action="delete_success",
                device=req.device,
                interface=req.interface,
                request_id=req.id,
                payload={"delete": True}
            )

        # ---- MODIFY FLOW ----
        else:
            with netconf.connect(dev) as nc:
                netconf.apply_interface_config(
                    nc,
                    interface=req.interface,
                    config=req.config
                )

            write_audit(
                db,
                actor="system",
                action="apply_success",
                device=req.device,
                interface=req.interface,
                request_id=req.id,
                payload={"config": req.config}
            )

        # ----------------------------
        # DIRECT SERVER-SIDE REFRESH
        # ----------------------------
        from app.jobs.refresh_interfaces import refresh_interfaces_for_device
        try:
            refresh_interfaces_for_device(req.device)
        except Exception as e:
            write_audit(
                db,
                actor="system",
                action="device_refresh_failed",
                device=req.device,
                interface=req.interface,
                request_id=req.id,
                comment=str(e)
            )

    except Exception as e:
        req.status = models.RequestStatus.failed
        req.comment = str(e)
        db.commit()

        write_audit(
            db,
            actor="system",
            action="apply_failed",
            device=req.device,
            interface=req.interface,
            request_id=req.id,
            comment=str(e),
            payload={"type": req.type}
        )

        raise HTTPException(500, f"Apply failed: {e}")

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
    write_audit(
        db,
        actor=user["username"],
        action="reject",
        device=item.device,
        interface=item.interface,
        request_id=item.id,
        comment=comment,
        payload={
            "status": "rejected"
        }
    )

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
    q = db.query(models.AuditLog)

    if device:
        q = q.filter(models.AuditLog.device == device)
    if interface:
        q = q.filter(models.AuditLog.interface == interface)

    rows = q.order_by(models.AuditLog.timestamp.desc()).limit(limit).all()

    return [
        {
            "id": r.id,
            "timestamp": r.timestamp.isoformat(),
            "actor": r.actor,
            "action": r.action,
            "device": r.device,
            "interface": r.interface,
            "request_id": r.request_id,
            "comment": r.comment,
            "payload": r.payload,
        }
        for r in rows
    ]

def _normalize_cached_interfaces_row(row):
    """
    Ensure cached InterfaceCache.data is sane:
      - ensure _source present
      - remove skeleton / unconfigured physical ports (keep only configured or VC)
      - ensure expected fields exist (best-effort)
    """
    changed = False
    cleaned = []
    for p in (row.data or []):
        # ensure name exists
        name = p.get("name")
        if not name:
            changed = True
            continue

        # make sure source present
        if "_source" not in p:
            p["_source"] = "cache"
            changed = True

        # keep if configured OR explicit vc_port True
        if p.get("configured") or p.get("vc_port"):
            # normalize a few common keys to avoid undefined in frontend
            p.setdefault("member", 0)
            p.setdefault("fpc", 0)
            p.setdefault("type", name.split("-",1)[0] if "-" in name else ("ae" if name.startswith("ae") else "ge"))
            p.setdefault("port", 0)
            p.setdefault("bundle", None)
            p.setdefault("mode", None)
            p.setdefault("access_vlan", None)
            p.setdefault("trunk_vlans", [])
            p.setdefault("native_vlan", None)
            p.setdefault("admin_up", True)
            p.setdefault("oper_up", False)
            p.setdefault("description", None)
            p.setdefault("vc_status", None)
            cleaned.append(p)
        else:
            changed = True

    if changed:
        row.data = cleaned
        row.updated_at = datetime.utcnow()
    return changed

@app.on_event("startup")
def normalize_interface_cache():
    db = SessionLocal()
    try:
        rows = db.query(InterfaceCache).all()
        any_changed = False
        for row in rows:
            if _normalize_cached_interfaces_row(row):
                any_changed = True
                db.merge(row)
        if any_changed:
            db.commit()
    finally:
        db.close()

# -------------------------
# ROLLBACK API (UI TAB)
# -------------------------

@app.get("/api/rollback/{device}")
def rollback_list(
    device: str,
    user=Depends(require_role(("admin","approver"))),
):
    """
    Return parsed commit history.
    """
    try:
        dev = get_device(device)
        txt = netconf.get_rollback_list(dev)
    except Exception as e:
        raise HTTPException(500, f"NETCONF failed: {e}")

    commits = []

    import re
    line_re = re.compile(
        r"^\s*(\d+)\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2}\s+\S+)\s+by\s+(\S+)"
    )

    current = None
    for line in txt.splitlines():
        m = line_re.match(line)
        if m:
            if current:
                commits.append(current)

            current = {
                "index": int(m.group(1)),
                "timestamp": m.group(2),
                "user": m.group(3),
                "comment": ""
            }

        elif "comment:" in line and current:
            current["comment"] = line.split("comment:", 1)[1].strip()

    if current:
        commits.append(current)

    return commits

@app.get("/api/rollback/{device}/{idx}/diff")
def rollback_diff(device: str, idx: int):
    try:
        dev = get_device(device)
        diff = netconf.get_rollback_diff(dev, idx)
        
        # 🔥 belangrijk: altijd raw plaintext teruggeven
        return PlainTextResponse(diff if diff else "")
    except Exception as e:
        raise HTTPException(500, f"NETCONF failed: {e}")


@app.post("/api/rollback/{device}/{idx}/apply")
def rollback_apply(
    device: str,
    idx: int,
    user=Depends(require_role(("admin","approver"))),
    db: Session = Depends(get_db)
):
    """
    Apply rollback <idx>.
    """
    try:
        dev = get_device(device)

        with netconf.connect(dev) as nc:
            netconf.apply_rollback(nc, idx)

        # audit log
        write_audit(
            db,
            actor=user["username"],
            action="rollback_apply",
            device=device,
            interface=None,
            comment=f"Rollback {idx} applied",
            payload={"rollback": idx}
        )

        return {"status": "ok", "rollback": idx}

    except Exception as e:
        raise HTTPException(500, f"NETCONF rollback failed: {e}")
    
@app.post("/api/interface/{device}/{interface}/refresh")
def refresh_single_interface(device: str, interface: str):
    dev = get_device(device)

    with netconf.connect(dev) as nc:
        xml = netconf.get_single_interface(nc, interface)

    # convert XML → parsed dict (zelfde parser als voor full retrieve)
    parsed = netconf.parse_interface_xml(xml)

    return {
        "device": device,
        "interface": interface,
        "data": parsed
    }

@app.post("/api/requests/delete", status_code=200)
def request_delete_interface(
    device: str = Body(...),
    interface: str = Body(...),
    comment: Optional[str] = Body(None),
    db: Session = Depends(get_db),
    user=Depends(require_role(("admin",)))
):
    req = models.ChangeRequest(
        device=device,
        interface=interface,
        type="delete",
        status=models.RequestStatus.pending,
        requester=user["username"],
        config={},  # geen config nodig
        comment=comment
    )
    db.add(req)
    db.commit()
    db.refresh(req)

    write_audit(
        db,
        actor=user["username"],
        action="request_delete",
        device=device,
        interface=interface,
        request_id=req.id,
        comment=comment,
        payload={"delete": True}
    )

    return req
