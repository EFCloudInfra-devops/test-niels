
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from .config import settings
from .schemas import DeviceInfo, InterfaceConfig, CommitRequest, BulkCommitRequest, ValidateResponse
from .models import init_db, SessionLocal, AuditLog, DesiredState, ActualCache
from .utils import validate_config
from . import netconf
from apscheduler.schedulers.background import BackgroundScheduler
import json

app = FastAPI(title='EX4300 Port UI Backend', version='0.1.0')
app.add_middleware(
    CORSMiddleware,
    allow_origins=['*'],
    allow_credentials=True,
    allow_methods=['*'],
    allow_headers=['*'],
)

init_db()

scheduler = BackgroundScheduler()


def refresh_actual_cache():
    cfg_ele = netconf.get_configuration()
    parsed = netconf.parse_interfaces_config(cfg_ele)
    oper = netconf.get_operational()
    db = SessionLocal()
    try:
        for p in parsed:
            name = p['name']
            if name in oper:
                p.update(oper[name])
            rec = db.query(ActualCache).filter_by(device=settings.device_name, interface=name).first()
            payload = json.dumps(p)
            if rec:
                rec.payload = payload
                db.add(rec)
            else:
                db.add(ActualCache(device=settings.device_name, interface=name, payload=payload))
        db.commit()
    finally:
        db.close()

scheduler.add_job(refresh_actual_cache, 'interval', seconds=settings.sync_interval_seconds, id='actual_sync', replace_existing=True)
scheduler.start()


@app.get('/api/devices', response_model=DeviceInfo)
def get_device():
    return DeviceInfo(name=settings.device_name, mgmt_ip=settings.mgmt_ip)


@app.get('/api/switches/{device}/interfaces')
def list_interfaces(device: str):
    if device != settings.device_name:
        raise HTTPException(404, 'Onbekend device')
    db = SessionLocal()
    try:
        rows = db.query(ActualCache).filter_by(device=device).all()
        if not rows:
            refresh_actual_cache()
            rows = db.query(ActualCache).filter_by(device=device).all()
        return [json.loads(r.payload) for r in rows]
    finally:
        db.close()


@app.post('/api/validate', response_model=ValidateResponse)
def validate(config: InterfaceConfig):
    errors = validate_config(config)
    return ValidateResponse(ok=(len(errors)==0), errors=errors)


@app.post('/api/commit')
def commit(req: CommitRequest):
    errors = validate_config(req.config)
    if errors:
        raise HTTPException(400, detail=errors)
    changes = [{'interface': i, 'config': req.config.model_dump()} for i in req.interfaces]
    result = netconf.commit_bulk(changes)
    db = SessionLocal()
    try:
        if result.get('ok'):
            for i in req.interfaces:
                ds = db.query(DesiredState).filter_by(device=req.device, interface=i).first()
                payload = json.dumps(req.config.model_dump())
                if ds:
                    ds.payload = payload
                    db.add(ds)
                else:
                    db.add(DesiredState(device=req.device, interface=i, payload=payload))
            db.add(AuditLog(user=req.user, device=req.device, interfaces=','.join(req.interfaces), action=json.dumps(req.config.model_dump()), result='committed', diff='pre/post snapshot saved'))
            db.commit()
        else:
            db.add(AuditLog(user=req.user, device=req.device, interfaces=','.join(req.interfaces), action=json.dumps(req.config.model_dump()), result='failed', diff=result.get('error','')))
            db.commit()
        return result
    finally:
        db.close()


@app.post('/api/commit/bulk')
def commit_bulk(req: BulkCommitRequest):
    merged = []
    for item in req.items:
        errs = validate_config(item.config)
        if errs:
            raise HTTPException(400, detail=errs)
        for i in item.interfaces:
            merged.append({'interface': i, 'config': item.config.model_dump()})
    result = netconf.commit_bulk(merged)
    db = SessionLocal()
    try:
        if result.get('ok'):
            for item in req.items:
                for i in item.interfaces:
                    payload = json.dumps(item.config.model_dump())
                    ds = db.query(DesiredState).filter_by(device=item.device, interface=i).first()
                    if ds:
                        ds.payload = payload
                        db.add(ds)
                    else:
                        db.add(DesiredState(device=item.device, interface=i, payload=payload))
            db.add(AuditLog(user=req.user, device=settings.device_name, interfaces=','.join([x['interface'] for x in merged]), action='bulk', result='committed', diff='pre/post snapshot saved'))
            db.commit()
        else:
            db.add(AuditLog(user=req.user, device=settings.device_name, interfaces=','.join([x['interface'] for x in merged]), action='bulk', result='failed', diff=result.get('error','')))
            db.commit()
        return result
    finally:
        db.close()


@app.post('/api/sync')
def sync_now():
    refresh_actual_cache()
    return {'ok': True}
