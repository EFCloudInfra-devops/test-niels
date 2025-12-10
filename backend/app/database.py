# database.py
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DB_PATH = os.getenv("APP_DB_PATH", "/app/data/app.db")
# sqlite connection string
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{DB_PATH}")

# For SQLite, enable check_same_thread=False so sessions can be used across threads
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

def init_db():
    # creates tables if not existing (useful for simple deployments)
    from . import models
    Base.metadata.create_all(bind=engine)
