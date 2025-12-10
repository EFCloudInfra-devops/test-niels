# models.py
from sqlalchemy import Column, Integer, String, DateTime, Text, JSON
from sqlalchemy import Enum as SAEnum
from datetime import datetime
from .database import Base
import enum

class RequestStatus(enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"

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

class InterfaceCache(Base):
    __tablename__ = "interface_cache"

    device = Column(String, primary_key=True)
    data = Column(JSON, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow)
