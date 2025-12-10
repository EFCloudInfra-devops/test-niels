# backend/app/auth.py
from fastapi import HTTPException, Depends, Header
from passlib.context import CryptContext
import jwt
from datetime import datetime, timedelta
from .db import SessionLocal
from .models import User

JWT_SECRET = "pVoor7BmwBotzyKL"
JWT_ALGO = "HS256"
ACCESS_EXPIRE_MINUTES = 60 * 24

pwd_ctx = CryptContext(schemes=["bcrypt"], deprecated="auto")

def hash_password(pw: str) -> str:
    return pwd_ctx.hash(pw)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_ctx.verify(plain, hashed)

def create_token_for_user(user: User):
    payload = {
        "sub": user.username,
        "role": user.role,
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_EXPIRE_MINUTES)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGO)

def get_db_user(username: str):
    db = SessionLocal()
    try:
        return db.query(User).filter(User.username == username).first()
    finally:
        db.close()

def get_current_user(authorization: str = Header(None)):
    if not authorization:
        raise HTTPException(status_code=401, detail="Not authenticated")
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Invalid auth header")
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGO])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid token")
    username = payload.get("sub")
    if not username:
        raise HTTPException(status_code=401, detail="Invalid token payload")
    user = get_db_user(username)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user

# simple role dependency
def require_role(min_role: str):
    order = {"reader": 0, "operator": 1, "approver": 2, "admin": 3}
    def dep(user = Depends(get_current_user)):
        if order.get(user.role, 0) < order.get(min_role, 0):
            raise HTTPException(status_code=403, detail="Insufficient role")
        return user
    return dep
