# schemas.py
from pydantic import BaseModel
from typing import Optional, List, Any
from enum import Enum
from datetime import datetime

class RequestStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"

class ChangeRequestCreate(BaseModel):
    device: str
    interface: str
    config: dict
    requester: Optional[str] = "ui"
    created_at: Optional[datetime] = None
    status: Optional[RequestStatus] = RequestStatus.pending

class ChangeRequestOut(BaseModel):
    id: int
    device: str
    interface: str
    requester: str
    config: dict
    status: RequestStatus
    approver: Optional[str]
    created_at: datetime
    updated_at: datetime
    comment: Optional[str]
