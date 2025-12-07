
from sqlalchemy import create_engine, Column, Integer, String, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker
from datetime import datetime
import os

DB_PATH = os.getenv('DB_PATH', '/app/data/app.db')
engine = create_engine(f'sqlite:///{DB_PATH}', echo=False, future=True)
SessionLocal = sessionmaker(bind=engine)
Base = declarative_base()

class AuditLog(Base):
    __tablename__ = 'audit_logs'
    id = Column(Integer, primary_key=True)
    user = Column(String(128))
    device = Column(String(128))
    interfaces = Column(Text)
    action = Column(Text)
    result = Column(String(64))
    diff = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)

class DesiredState(Base):
    __tablename__ = 'desired_state'
    id = Column(Integer, primary_key=True)
    device = Column(String(128))
    interface = Column(String(128))
    payload = Column(Text)
    updated_at = Column(DateTime, default=datetime.utcnow)

class ActualCache(Base):
    __tablename__ = 'actual_cache'
    id = Column(Integer, primary_key=True)
    device = Column(String(128))
    interface = Column(String(128))
    payload = Column(Text)
    refreshed_at = Column(DateTime, default=datetime.utcnow)

class RollbackSnap(Base):
    __tablename__ = 'rollback_snaps'
    id = Column(Integer, primary_key=True)
    device = Column(String(128))
    pre = Column(Text)
    post = Column(Text)
    created_at = Column(DateTime, default=datetime.utcnow)


def init_db():
    Base.metadata.create_all(engine)
