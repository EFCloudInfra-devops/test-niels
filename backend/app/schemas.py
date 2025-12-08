from pydantic import BaseModel
from typing import List, Optional, Literal

class InterfaceConfig(BaseModel):
    name: str
    member: int
    fpc: int
    type: Literal['ge','xe']
    port: int
    admin_up: bool = True
    oper_up: bool = False
    mode: Literal['access','trunk']
    access_vlan: Optional[int] = None
    trunk_vlans: Optional[List[int]] = None
    native_vlan: Optional[int] = None
    poe: Optional[bool] = None
    speed: Optional[str] = None
    duplex: Optional[str] = None

class CommitRequest(BaseModel):
    device: str
    user: str
    interfaces: List[str]
    config: InterfaceConfig

class BulkCommitRequest(BaseModel):
    devices: List[str]
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
    version: str | None = None
    roles: List[str] | None = None

class RollbackRequest(BaseModel):
    device: str
    level: int = 1
    user: str
