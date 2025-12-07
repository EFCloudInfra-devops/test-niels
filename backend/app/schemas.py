
from pydantic import BaseModel
from typing import List, Optional, Literal

class InterfaceConfig(BaseModel):
    name: str
    member: int
    type: Literal['ge','xe']
    port: int
    admin_up: bool = True
    oper_up: bool = False
    mode: Literal['access','trunk']
    access_vlan: Optional[int] = None
    trunk_vlans: Optional[List[int]] = None
    native_vlan: Optional[int] = None
    poe: Optional[bool] = None
    speed: Optional[str] = None  # 'auto','10m','100m','1g','10g'
    duplex: Optional[str] = None # 'auto','full'

class CommitRequest(BaseModel):
    device: str
    user: str
    interfaces: List[str]  # interface names e.g. ge-0/0/1
    config: InterfaceConfig

class BulkCommitRequest(BaseModel):
    device: str
    user: str
    items: List[CommitRequest]

class ValidateResponse(BaseModel):
    ok: bool
    errors: List[str] = []

class DeviceInfo(BaseModel):
    name: str
    mgmt_ip: str
    vc_members: int = 2
    platform: str = 'EX4300-48P'
    version: str = '21.4R3-S11.3'
