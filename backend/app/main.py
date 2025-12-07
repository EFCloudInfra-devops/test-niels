
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .config import settings, Device
from .schemas import DeviceInfo, InterfaceConfig, CommitRequest, BulkCommitRequest, ValidateResponse, RollbackRequest
from .models import init_db, SessionLocal, AuditLog, DesiredState, ActualCache, RollbackSnap
from .utils import validate_config
from . import netconf
from apscheduler.schedulers.background import BackgroundScheduler
import json

app = FastAPI(title='EX4300 Port UI Backend', version='0.2.1')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

init_db()

scheduler = BackgroundScheduler()


def device_by_name(name: str) -> Device:
    for d in settings.devices:
        if d.name == name:
            return d
    raise KeyError('device not found')


def refresh_actual_cache(dev: Device):
    try:
        cfg_ele = netconf.get_configuration(dev)
        parsed = netconf.parse_interfaces_config(cfg_ele)
        oper = netconf.get_operational(dev)
        roles, mode = netconf.get_vc_roles(dev)
    except Exception:
        return  # skip device on errors
    db = SessionLocal()
    try:
        for p in parsed:
            name = p['name']
            if name in oper:
                p.update(oper[name])
            rec = db.query(ActualCache).filter_by(device=dev.name, interface=name).first()
            payload = json.dumps(p)
            if rec:
                rec.payload = payload
                db.add(rec)
            else:
                db.add(ActualCache(device=dev.name, interface=name, payload=payload))
        db.commit()
        meta = {'name': dev.name, 'mgmt_ip': dev.mgmt_ip, 'roles': roles, 'vc_mode': mode}
        rec = db.query(ActualCache).filter_by(device=dev.name, interface='__meta__').first()
        payload = json.dumps(meta)
        if rec:
            rec.payload = payload
            db.add(rec)
        else:
            db.add(ActualCache(device=dev.name, interface='__meta__', payload=payload))
        db.commit()
    finally:
        db.close()

for dev in settings.devices:
    scheduler.add_job(lambda d=dev: refresh_actual_cache(d), 'interval', seconds=settings.sync_interval_seconds, id=f'actual_sync_{dev.name}', replace_existing=True)

scheduler.start()


@app.get('/api/health')
def health():
    return {'ok': True}


@app.get('/api/inventory')
def inventory():
    return [d.model_dump() for d in settings.devices]


@app.get('/api/devices/{device}', response_model=DeviceInfo)
def get_device(device: str):
    try:
        dev = device_by_name(device)
    except KeyError:
        raise HTTPException(404, 'Onbekend device')
    db = SessionLocal()
    try:
        meta = db.query(ActualCache).filter_by(device=dev.name, interface='__meta__').first()
        roles = None
        version = None
        if meta:
            m = json.loads(meta.payload)
            roles = m.get('roles')
        return DeviceInfo(name=dev.name, mgmt_ip=dev.mgmt_ip, roles=roles, version=version)
    finally:
        db.close()


@app.get('/api/switches/{device}/interfaces')
def list_interfaces(device: str):
    try:
        dev = device_by_name(device)
    except KeyError:
        raise HTTPException(404, 'Onbekend device')
    db = SessionLocal()
    try:
        rows = db.query(ActualCache).filter_by(device=device).all()
        if not rows:
            refresh_actual_cache(dev)
            rows = db.query(ActualCache).filter_by(device=device).all()
        return [json.loads(r.payload) for r in rows if r.interface != '__meta__']
    finally:
        db.close()


@app.post('/api/validate', response_model=ValidateResponse)
def validate(config: InterfaceConfig):
    errors = validate_config(config)
    return ValidateResponse(ok=(len(errors)==0), errors=errors)


@app.post('/api/commit')
def commit(req: CommitRequest):
    try:
        dev = device_by_name(req.device)
    except KeyError:
        raise HTTPException(404, 'Onbekend device')
    errors = validate_config(req.config)
    if errors:
        raise HTTPException(400, detail=errors)
    changes = [{'interface': i, 'config': req.config.model_dump()} for i in req.interfaces]
    result = netconf.commit_bulk(dev, changes)
    db = SessionLocal()
    try:
        if result.get('ok'):
            snap = RollbackSnap(device=dev.name, pre=result.get('pre',''), post=result.get('post',''))
            db.add(snap)
            for i in req.interfaces:
                ds = db.query(DesiredState).filter_by(device=req.device, interface=i).first()
                payload = json.dumps(req.config.model_dump())
                if ds:
                    ds.payload = payload
                    db.add(ds)
                else:
                    db.add(DesiredState(device=req.device, interface=i, payload=payload))
            db.add(AuditLog(user=req.user, device=req.device, interfaces=','.join(req.interfaces), action=json.dumps(req.config.model_dump()), result='committed', diff=result.get('diff','')))
            db.commit()
        else:
            db.add(AuditLog(user=req.user, device=req.device, interfaces=','.join(req.interfaces), action=json.dumps(req.config.model_dump()), result='failed', diff=result.get('error','')))
            db.commit()
        return result
    finally:
        db.close()


@app.post('/api/commit/bulk')
def commit_bulk(req: BulkCommitRequest):
    results = {}
    db = SessionLocal()
    try:
        for dev_name in req.devices:
            try:
                dev = device_by_name(dev_name)
            except KeyError:
                results[dev_name] = {'ok': False, 'error': 'Unknown device'}
                continue
            merged = []
            for item in req.items:
                errs = validate_config(item.config)
                if errs:
                    results[dev_name] = {'ok': False, 'error': '; '.join(errs)}
                    break
                for i in item.interfaces:
                    merged.append({'interface': i, 'config': item.config.model_dump()})
            if not merged:
                continue
            res = netconf.commit_bulk(dev, merged)
            results[dev_name] = res
            if res.get('ok'):
                snap = RollbackSnap(device=dev.name, pre=res.get('pre',''), post=res.get('post',''))
                db.add(snap)
                for item in req.items:
                    for i in item.interfaces:
                        payload = json.dumps(item.config.model_dump())
                        ds = db.query(DesiredState).filter_by(device=dev_name, interface=i).first()
                        if ds:
                            ds.payload = payload
                            db.add(ds)
                        else:
                            db.add(DesiredState(device=dev_name, interface=i, payload=payload))
                db.add(AuditLog(user=req.user, device=dev_name, interfaces=','.join([x['interface'] for x in merged]), action='bulk', result='committed', diff=res.get('diff','')))
                db.commit()
            else:
                db.add(AuditLog(user=req.user, device=dev_name, interfaces=','.join([x['interface'] for x in merged]), action='bulk', result='failed', diff=res.get('error','')))
                db.commit()
        return results
    finally:
        db.close()


@app.post('/api/sync/{device}')
def sync_now(device: str):
    try:
        dev = device_by_name(device)
    except KeyError:
        raise HTTPException(404, 'Onbekend device')
    refresh_actual_cache(dev)
    return {'ok': True}


@app.post('/api/rollback')
def do_rollback(req: RollbackRequest):
    try:
        dev = device_by_name(req.device)
    except KeyError:
        raise HTTPException(404, 'Onbekend device')
    res = netconf.rollback(dev, level=req.level)
    db = SessionLocal()
    try:
        if res.get('ok'):
            db.add(AuditLog(user=req.user, device=req.device, interfaces='', action=f'rollback {req.level}', result='committed', diff=res.get('diff','')))
            db.commit()
        else:
            db.add(AuditLog(user=req.user, device=req.device, interfaces='', action=f'rollback {req.level}', result='failed', diff=res.get('error','')))
            db.commit()
        return res
    finally:
        db.close()
