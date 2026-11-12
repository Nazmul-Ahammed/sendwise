import hashlib
import secrets

from fastapi import Depends, HTTPException, Header
from sqlalchemy.orm import Session

from . import models
from .db import get_db


def hash_password(pw: str, salt: str = "") -> str:
    if not salt:
        salt = secrets.token_hex(16)
    dk = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 200_000).hex()
    return f"{salt}${dk}"


def verify_password(pw: str, stored: str) -> bool:
    try:
        salt, _ = stored.split("$", 1)
    except ValueError:
        return False
    return secrets.compare_digest(hash_password(pw, salt), stored)


def create_token(db: Session, user_id: int) -> str:
    token = secrets.token_hex(24)
    db.add(models.AuthToken(token=token, user_id=user_id))
    db.commit()
    return token


def require_user(
    authorization: str = Header(default=""),
    db: Session = Depends(get_db),
):
    if not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing token")
    token = authorization[7:]
    row = db.query(models.AuthToken).filter(models.AuthToken.token == token).first()
    if not row:
        raise HTTPException(status_code=401, detail="Invalid token")
    user = db.query(models.User).filter(models.User.id == row.user_id).first()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user
