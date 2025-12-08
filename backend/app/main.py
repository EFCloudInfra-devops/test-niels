
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .config import settings, Device
from .schemas import InterfaceConfig, CommitRequest, ValidateResponse
from .models import init_db, SessionLocal, ActualCache
from .utils import validate_config
from . import netconf
from apscheduler.schedulers.background import BackgroundScheduler
import json, re, logging
from datetime import datetime

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s')
log = logging.getLogger('ex4300-backend')

app = FastAPI(title='EX4300 Port UI Backend', version='0.3.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=False,
    allow_methods=['*'],
    allow_headers=['*'],
)

init_db()

scheduler = BackgroundScheduler()

GE_PAT = re.compile(r'^ge-\d+/0/\d+$')
XE_PAT = re.compile(r'^xe-\d+/2/\d+$')
AE_PAT = re.compile(r'^ae\d+$')

# helper to update meta row
def _write_last_refresh(db: SessionLocal, dev: Device):
    payload = json.dumps({'last_refresh': datetime.utcnow().isoformat()})
    rec = db.query(ActualCache).filter_by(device=dev.name, interface='__meta__').first()
    if rec:
        rec.payload = payload
        db.add(rec)
    else:
        db.add(ActualCache(device=dev.name, interface='__meta__', payload=payload))
    db.commit()


def device_by_name(name: str) -> Device:
    for d in settings.devices:
        if d.name == name:
            return d
    raise KeyError('device not found')


def refresh_actual_cache(dev: Device):
    try:
        cfg_ele = netconf.get_configuration(dev)
        parsed = netconf.parse_interfaces_config(cfg_ele)
    except Exception as e:
        log.error(f"Config read failed for {dev.name}: {e}")
        parsed = []
    try:
        oper = netconf.get_operational(dev)
    except Exception as e:
        log.error(f"Oper read failed for {dev.name}: {e}")
        oper = {}

    by_name = {p['name']: p for p in parsed}

    for iname, o in oper.items():
        if iname not in by_name and (GE_PAT.match(iname) or XE_PAT.match(iname) or AE_PAT.match(iname)):
            try:
                scheme = 'ae' if iname.startswith('ae') else iname.split('-', 1)[0]
                by_name[iname] = {
                    'name': iname, 'member': 0, 'fpc': 0, 'type': scheme,
                    'aggregate': scheme == 'ae', 'bundle': None,
                    'port': 0, 'mode': 'access', 'access_vlan': None, 'trunk_vlans': None,
                    'native_vlan': None, 'poe': None, 'speed': None, 'duplex': None,
                    'admin_up': o.get('admin_up', False), 'oper_up': o.get('oper_up', False),
                    'configured': False, 'description': None
                }
            except Exception:
                pass
        elif iname in by_name:
            by_name[iname].update(o)

    merged = list(by_name.values())

    db = SessionLocal()
    try:
        for p in merged:
            rec = db.query(ActualCache).filter_by(device=dev.name, interface=p['name']).first()
            payload = json.dumps(p)
            if rec:
                rec.payload = payload
                db.add(rec)
            else:
                db.add(ActualCache(device=dev.name, interface=p['name'], payload=payload))
        db.commit()
        _write_last_refresh(db, dev)             # <-- add this
        log.info(f"Actual cache refreshed: {dev.name} ({len(merged)} interfaces)")
    finally:
        db.close()

# Warm cache on startup (so UI sees cached immediately)
@app.on_event('startup')
def warm_cache_on_startup():
    for dev in settings.devices:
        try:
            refresh_actual_cache(dev)
        except Exception as e:
            log.error(f"Warm cache failed for {dev.name}: {e}")

# Schedule periodic refresh
for dev in settings.devices:
    scheduler.add_job(lambda d=dev: refresh_actual_cache(d), 'interval', seconds=settings.sync_interval_seconds, id=f'actual_sync_{dev.name}', replace_existing=True)

scheduler.start()

@app.get('/api/health')
def health():
    return {'ok': True}

@app.get('/api/inventory')
def inventory():
    return [d.model_dump() for d in settings.devices]

@app.get('/api/switches/{device}/interfaces')
def list_interfaces(device: str):
    try:
        dev = device_by_name(device)
    except KeyError:
        raise HTTPException(404, 'Unknown device')
    db = SessionLocal()
    try:
        rows = db.query(ActualCache).filter_by(device=device).all()
        return [json.loads(r.payload) for r in rows]
    finally:
        db.close()

# LIVE first
@app.get('/api/switches/{device}/interface/{ifname:path}/live')
def get_interface_live(device: str, ifname: str):
    try:
        dev = device_by_name(device)
    except KeyError:
        raise HTTPException(404, 'Unknown device')
    try:
        info = netconf.get_interface_live(dev, ifname)
        return info
    except Exception as e:
        raise HTTPException(500, f'live read failed: {e}')

@app.get('/api/devices/{device}/vlans')
def get_vlans(device: str):
    try:
        dev = device_by_name(device)
    except KeyError:
        raise HTTPException(404, 'Unknown device')
    try:
        return netconf.get_vlans(dev)
    except Exception as e:
        raise HTTPException(500, f'vlan read failed: {e}')

@app.post('/api/sync/{device}')
def sync_now(device: str):
    try:
        dev = device_by_name(device)
    except KeyError:
        raise HTTPException(404, 'Unknown device')
    refresh_actual_cache(dev)
    return {'ok': True}

@app.post('/api/validate', response_model=ValidateResponse)
def validate(config: InterfaceConfig):
    errors = validate_config(config)
    return ValidateResponse(ok=(len(errors)==0), errors=errors)

@app.post('/api/commit')
def commit(req: CommitRequest):
    try:
        dev = device_by_name(req.device)
    except KeyError:
        raise HTTPException(404, 'Unknown device')
    errors = validate_config(req.config)
    if errors:
        raise HTTPException(400, detail=errors)
    try:
        res = netconf.commit_changes(dev, req.interfaces, req.config.model_dump())
        return res
    except Exception as e:
        raise HTTPException(500, f'commit failed: {e}')

# endpoint to read last refresh
@app.get('/api/last-refresh/{device}')
def last_refresh(device: str):
    try:
        dev = device_by_name(device)
    except KeyError:
        raise HTTPException(404, 'Unknown device')
    db = SessionLocal()
    try:
        rec = db.query(ActualCache).filter_by(device=dev.name, interface='__meta__').first()
        if rec:
            payload = json.loads(rec.payload)
            return {'last_refresh': payload.get('last_refresh')}
        return {'last_refresh': None}
    finally:
        db.close()
