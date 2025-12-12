# models.py
from sqlalchemy import Column, Integer, String, DateTime, Text, JSON, ForeignKey
from sqlalchemy import Enum as SAEnum
from datetime import datetime
from .database import Base
import enum

class RequestStatus(enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    failed = "failed"

class ChangeRequest(Base):
    __tablename__ = "change_requests"
    id = Column(Integer, primary_key=True, index=True)
    device = Column(String(200), index=True, nullable=False)
    interface = Column(String(200), nullable=False)
    requester = Column(String(200), nullable=False)
    config = Column(JSON, nullable=False)   # store the config payload as JSON
    status = Column(SAEnum(RequestStatus), default=RequestStatus.pending, index=True)
    approver = Column(String(200), nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    comment = Column(Text, nullable=True)
    type = Column(String, default="config")   # "config" | "delete"

class InterfaceCache(Base):
    __tablename__ = "interface_cache"

    device = Column(String, primary_key=True)
    data = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow)
    
class Vlan(Base):
    __tablename__ = "vlans"

    id = Column(Integer, primary_key=True)
    device = Column(String, index=True)
    vlan_id = Column(Integer)
    name = Column(String)
    fetched_at = Column(DateTime)

class CachedInterface(Base):
    __tablename__ = "cached_interfaces"

    device = Column(String, primary_key=True)
    name = Column(String, primary_key=True)
    data = Column(JSON)
    fetched_at = Column(DateTime)
    updated_at = Column(DateTime, default=datetime.utcnow)

class CachedVlan(Base):
    __tablename__ = "vlan_cache"

    device = Column(String, primary_key=True)
    # vlan_id = Column(String, primary_key=True)  # string → supports “1”, “100-110”
    data = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    # __table_args__ = (
    #     UniqueConstraint("device", "vlan_id", name="uix_device_vlan"),
    # )

class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(Integer, primary_key=True)
    timestamp = Column(DateTime, default=datetime.utcnow, nullable=False)

    actor = Column(String, nullable=False)
    action = Column(String, nullable=False)     # approve / reject / apply_success / apply_failed

    device = Column(String, nullable=False)
    interface = Column(String, nullable=True)

    request_id = Column(Integer, ForeignKey("change_requests.id"))
    comment = Column(String, nullable=True)

    payload = Column(JSON, nullable=True)
