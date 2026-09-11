import random
import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field
from pydantic import root_validator

from database import get_db, User, UserRole, UserStatus, DocType, DocStatus, KYCDocument
from auth import (
    get_current_user,
    get_current_user_any_status,  
    create_otp_session,
    verify_otp_session,
    issue_tokens,
    refresh_access_token
)

logger = logging.getLogger("TraceLoop.UserRoutes")
router = APIRouter(prefix="/api/v1", tags=["Auth & Users"])

def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "traceloop_version": "v1"
    }

# --- SCHEMAS ---

class RegisterUserPayload(BaseModel):
    phone: str = Field(..., max_length=15)
    name: str = Field(..., max_length=120)
    role: UserRole
    aadhaar_last4: str = Field(..., min_length=4, max_length=4)
    gst_number: Optional[str] = None
    cpcb_number: Optional[str] = None
    brand_auth_code: Optional[str] = None
    service_center_id: Optional[str] = None

    @root_validator(skip_on_failure=True)
    def validate_role_requirements(cls, values):
        role = values.get("role")
        if role == UserRole.ADMIN:
            raise ValueError("Admin role cannot be self-assigned.")
        if role == UserRole.RESELLER and not values.get("gst_number"):
            raise ValueError("GST number is mandatory for RESELLER.")
        if role == UserRole.RECYCLER and not values.get("cpcb_number"):
            raise ValueError("CPCB number is mandatory for RECYCLER.")
        if role == UserRole.VERIFIABLE:
            if not values.get("brand_auth_code") or not values.get("service_center_id"):
                raise ValueError("brand_auth_code and service_center_id are mandatory for VERIFIABLE.")
        return values

class OTPSendPayload(BaseModel):
    phone: str = Field(..., max_length=15)

class OTPVerifyPayload(BaseModel):
    phone: str = Field(..., max_length=15)
    otp_code: str = Field(..., min_length=4, max_length=6)

class RefreshTokenPayload(BaseModel):
    refresh_token: str

class KYCUploadPayload(BaseModel):
    doc_type: DocType
    file_hash: str = Field(..., min_length=64, max_length=64, description="SHA-256 of the document")


# --- ROUTES ---

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(payload: RegisterUserPayload, db: Session = Depends(get_db)):
    if db.query(User).filter(User.phone == payload.phone).first():
        raise HTTPException(status_code=409, detail="Phone number already registered.")
    try:
        new_user = User(
            phone=payload.phone,
            name=payload.name,
            role=payload.role,
            aadhaar_last4=payload.aadhaar_last4,
            gst_number=payload.gst_number,
            cpcb_number=payload.cpcb_number,
            brand_auth_code=payload.brand_auth_code,
            service_center_id=payload.service_center_id,
            status=UserStatus.PENDING,
            admin_approved=False,
            aadhaar_verified=False
        )
        db.add(new_user)
        db.commit()
        db.refresh(new_user)
        return standard_response({
            "user_id": new_user.id,
            "role": new_user.role,
            "status": new_user.status,
            "message": "Registration successful. Call POST /api/v1/otp/send to receive your OTP."
        }, 201)
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Registration failed: {str(e)}")


@router.post("/otp/send")
def send_otp(payload: OTPSendPayload, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == payload.phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="Phone number not registered.")
    if user.status in [UserStatus.SUSPENDED, UserStatus.REJECTED]:
        raise HTTPException(status_code=403, detail=f"Account is {user.status}. Cannot send OTP.")
    otp = str(random.randint(100000, 999999))
    create_otp_session(phone=payload.phone, otp=otp, db=db)
    logger.info(f"OTP generated for {payload.phone}")
    return standard_response({
        "message": "OTP sent successfully.",
        "otp_preview": otp,  # REMOVE IN PRODUCTION — replace with SMS gateway
        "expires_in_minutes": 10
    })


@router.post("/otp/verify")
def verify_otp(payload: OTPVerifyPayload, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == payload.phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    verify_otp_session(phone=payload.phone, otp_code=payload.otp_code, db=db)

    # All roles move to KYC_IN_PROGRESS after OTP — activation happens via:
    # BUYER/FIRST_BUYER/RESELLER: after admin approves their KYC doc
    # VERIFIABLE/RECYCLER: after admin approves their account
    if user.status == UserStatus.PENDING:
        user.status = UserStatus.KYC_IN_PROGRESS
    db.commit()

    # Tokens issued here so user can call /kyc/upload
    tokens = issue_tokens(user=user, db=db)
    return standard_response({
        "user_id": user.id,
        "role": user.role,
        "status": user.status,
        "message": "OTP verified. Upload your KYC document to proceed.",
        **tokens
    })


@router.post("/token/refresh")
def refresh_token(payload: RefreshTokenPayload, db: Session = Depends(get_db)):
    try:
        tokens = refresh_access_token(refresh_token=payload.refresh_token, db=db)
        return standard_response(tokens)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/kyc/upload")
def upload_kyc_document(
    payload: KYCUploadPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_any_status) 
):
    """
    Uploads KYC document hash. Document is set to PENDING — 
    admin must review and accept before account becomes ACTIVE.
    No role is auto-activated here anymore.
    """
    role_doc_map = {
        UserRole.FIRST_BUYER: DocType.PURCHASE_PROOF,
        UserRole.RESELLER: DocType.BUSINESS_PROOF,
        UserRole.VERIFIABLE: DocType.BRAND_AUTH,
        UserRole.RECYCLER: DocType.CPCB_CERT,
    }
    required_doc = role_doc_map.get(current_user.role)
    if required_doc and payload.doc_type != required_doc:
        raise HTTPException(
            status_code=400,
            detail=f"{current_user.role.value} must upload {required_doc.value}, not {payload.doc_type.value}."
        )

    try:
        new_doc = KYCDocument(
            user_id=current_user.id,
            doc_type=payload.doc_type,
            file_path="s3://traceloop-kyc/pending/document.pdf",
            file_hash=payload.file_hash,
            status=DocStatus.PENDING  # ← Admin must review, never auto-accepted
        )
        db.add(new_doc)
        db.commit()

        return standard_response({
            "doc_id": new_doc.id,
            "status": "PENDING",
            "message": "Document submitted. Awaiting admin review. You will be activated once approved."
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"KYC upload failed: {str(e)}")


@router.get("/me")
def get_my_profile(current_user: User = Depends(get_current_user)):
    return standard_response({
        "user_id": current_user.id,
        "name": current_user.name,
        "phone": current_user.phone,
        "role": current_user.role,
        "status": current_user.status,
        "admin_approved": current_user.admin_approved,
        "aadhaar_verified": current_user.aadhaar_verified
    })
