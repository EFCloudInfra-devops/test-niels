
from pydantic import BaseModel
import os, json

class Device(BaseModel):
    name: str
    mgmt_ip: str
    group: str | None = None

class Settings(BaseModel):
    devices: list[Device]
    netconf_username: str = os.getenv('NETCONF_USERNAME', 'automation')
    netconf_password: str = os.getenv('NETCONF_PASSWORD', '')
    sync_interval_seconds: int = int(os.getenv('SYNC_INTERVAL_SECONDS', '300'))

inv_path = '/app/inventory.json'
with open(inv_path, 'r') as f:
    data = json.load(f)

settings = Settings(devices=[Device(**d) for d in data])
