
from pydantic import BaseModel
from typing import List, Optional, Union, Literal

class InterfaceConfig(BaseModel):
    name: str
    member: int
    fpc: int
    type: Literal['ge','xe','ae']
    port: int
    mode: Literal['access','trunk']
    access_vlan: Optional[Union[int, str]] = None
    trunk_vlans: Optional[List[Union[int, str]]] = None
    native_vlan: Optional[Union[int, str]] = None
    speed: Optional[str] = None

class CommitRequest(BaseModel):
    device: str
    user: str
    interfaces: List[str]
    config: InterfaceConfig

class ValidateResponse(BaseModel):
    ok: bool
    errors: List[str] = []
