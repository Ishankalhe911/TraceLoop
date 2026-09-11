import os
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import List

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from sqlalchemy.orm import Session

from database import get_db, User, UserRole, UserStatus, JWTSession, OTPSession

# --- CONFIG ---
JWT_SECRET = os.getenv("JWT_SECRET", "traceloop-dev-secret-change-in-prod")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "60"))
REFRESH_TOKEN_EXPIRE_DAYS = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))
OTP_EXPIRE_MINUTES = 10
OTP_MAX_ATTEMPTS = 5

bearer_scheme = HTTPBearer()

# --- HELPERS ---

def _hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

def _hmac_otp(phone: str, otp: str) -> str:
    return hmac.new(JWT_SECRET.encode(), f"{phone}:{otp}".encode(), hashlib.sha256).hexdigest()

def create_access_token(user_id: str, role: str) -> str:
    payload = {
        "sub": user_id,
        "role": role,
        "type": "access",
        "exp": datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def create_refresh_token(user_id: str) -> str:
    payload = {
        "sub": user_id,
        "type": "refresh",
        "exp": datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)

def decode_token(token: str) -> dict:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token has expired.")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token.")

# --- CORE DEPENDENCY ---

def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db)
) -> User:
    """
    Validates JWT, checks session is not revoked, enforces user is ACTIVE.
    Used on every authenticated route.
    """
    token = credentials.credentials
    payload = decode_token(token)

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type.")

    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")

    # Enforce account must be ACTIVE — blocks PENDING, KYC_IN_PROGRESS, SUSPENDED, REJECTED
    if user.status != UserStatus.ACTIVE:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"Account is not active. Current status: {user.status}. Complete KYC or contact admin."
        )

    return user

# --- ROLE-BASED ACCESS CONTROL ---

def RequireRole(allowed_roles: List[UserRole]):
    """
    Factory that returns a FastAPI dependency enforcing role-based access.
    Usage: current_user: User = Depends(RequireRole([UserRole.VERIFIABLE]))
    """
    def role_checker(current_user: User = Depends(get_current_user)) -> User:
        if current_user.role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied. Required role(s): {[r.value for r in allowed_roles]}. Your role: {current_user.role.value}"
            )
        return current_user
    return role_checker

# --- OTP HELPERS (used by user_routes auth endpoints) ---

def generate_otp_hash(phone: str, otp: str) -> str:
    """HMAC-SHA256 of phone+otp. Never stores raw OTP."""
    return _hmac_otp(phone, otp)

def verify_otp_session(phone: str, otp_code: str, db: Session) -> bool:
    """
    Validates OTP against stored hash. Enforces expiry and attempt limit.
    Returns True on success, raises HTTPException on failure.
    """
    session = db.query(OTPSession).filter(
        OTPSession.phone == phone,
        OTPSession.used == False
    ).order_by(OTPSession.created_at.desc()).first()

    if not session:
        raise HTTPException(status_code=400, detail="No active OTP session. Request a new OTP.")

    if datetime.utcnow() > session.expires_at.replace(tzinfo=None):
        raise HTTPException(status_code=400, detail="OTP has expired. Request a new one.")

    if session.attempts >= OTP_MAX_ATTEMPTS:
        raise HTTPException(status_code=429, detail="Too many OTP attempts. Request a new OTP.")

    session.attempts += 1
    db.commit()

    expected_hash = _hmac_otp(phone, otp_code)
    if not hmac.compare_digest(session.otp_hash, expected_hash):
        raise HTTPException(status_code=400, detail="Invalid OTP.")

    session.used = True
    db.commit()
    return True

def create_otp_session(phone: str, otp: str, db: Session) -> None:
    """Stores a new OTP session. Invalidates any previous sessions for this phone."""
    # Invalidate previous sessions
    db.query(OTPSession).filter(OTPSession.phone == phone, OTPSession.used == False).update({"used": True})

    new_session = OTPSession(
        phone=phone,
        otp_hash=_hmac_otp(phone, otp),
        expires_at=datetime.utcnow() + timedelta(minutes=OTP_EXPIRE_MINUTES),
        used=False,
        attempts=0
    )
    db.add(new_session)
    db.commit()

def issue_tokens(user: User, db: Session) -> dict:
    """
    Creates access + refresh tokens, stores refresh token hash in JWTSession.
    Returns both tokens.
    """
    access_token = create_access_token(user.id, user.role.value)
    refresh_token = create_refresh_token(user.id)

    # Store refresh token hash (never raw)
    db_session = JWTSession(
        user_id=user.id,
        refresh_token_hash=_hash(refresh_token),
        expires_at=datetime.utcnow() + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS),
        revoked=False
    )
    db.add(db_session)
    db.commit()

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60
    }

def refresh_access_token(refresh_token: str, db: Session) -> dict:
    """
    Validates refresh token, issues new access token.
    Rotates refresh token (old one revoked, new one issued).
    """
    payload = decode_token(refresh_token)

    if payload.get("type") != "refresh":
        raise HTTPException(status_code=401, detail="Invalid token type.")

    token_hash = _hash(refresh_token)
    session = db.query(JWTSession).filter(
        JWTSession.refresh_token_hash == token_hash,
        JWTSession.revoked == False
    ).first()

    if not session:
        raise HTTPException(status_code=401, detail="Refresh token is invalid or has been revoked.")

    if datetime.utcnow() > session.expires_at.replace(tzinfo=None):
        raise HTTPException(status_code=401, detail="Refresh token has expired.")

    user = db.query(User).filter(User.id == session.user_id).first()
    if not user or user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="Account is not active.")

    # Revoke old session (token rotation)
    session.revoked = True
    db.commit()

    return issue_tokens(user, db)

def get_current_user_any_status(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db)
) -> User:
    """
    Same as get_current_user but allows KYC_IN_PROGRESS.
    Only used for /kyc/upload so users can submit docs before being ACTIVE.
    """
    token = credentials.credentials
    payload = decode_token(token)

    if payload.get("type") != "access":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token type.")

    user_id = payload.get("sub")
    user = db.query(User).filter(User.id == user_id).first()

    if not user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found.")

    if user.status in [UserStatus.SUSPENDED, UserStatus.REJECTED]:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=f"Account is {user.status}.")

    return user
