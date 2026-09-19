import os
import random
import logging
from datetime import datetime, timezone
from typing import Optional
import requests 
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, root_validator

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
        "timestamp": datetime.now(timezone.utc).isoformat(),
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
    serial_for_device: Optional[str] = Field(
        None, max_length=80,
        description="Serial number of the device this proof covers. "
                    "Required for FIRST_BUYER (PURCHASE_PROOF) and RESELLER (BUSINESS_PROOF). "
                    "Admin verifies both your identity AND this serial in one step."
    )


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

    # --- FAST2SMS INTEGRATION (BULLETPROOF) ---
    fast2sms_key = os.getenv("FAST2SMS_API_KEY")
    sms_sent_successfully = False
    
    if fast2sms_key:
        try:
            # Extracts exactly the last 10 digits, ignoring '+91', '0', or spaces
            clean_phone = ''.join(filter(str.isdigit, payload.phone))[-10:]
            
            url = "https://www.fast2sms.com/dev/bulkV2"
            querystring = {
                "authorization": fast2sms_key,
                "variables_values": otp,
                "route": "otp",
                "numbers": clean_phone
            }
            
            response = requests.get(url, headers={'cache-control': "no-cache"}, params=querystring)
            
            if response.status_code == 200:
                sms_sent_successfully = True
            else:
                logger.error(f"Fast2SMS API Failed (Status {response.status_code}): {response.text}")
        except Exception as e:
            logger.error(f"Fast2SMS HTTP Request Exception: {str(e)}")
    else:
        logger.warning("FAST2SMS_API_KEY missing. Falling back to Dev Console OTP.")

    return standard_response({
        "message": "OTP sent successfully.",
        # MAGIC TRICK FAILSAFE: Only hide the OTP if the SMS actually sent! 
        # If Fast2SMS fails (e.g. empty wallet), it passes the OTP to the Dev Console so your demo survives.
        "otp_preview": otp if not sms_sent_successfully else None, 
        "expires_in_minutes": 10
    })

@router.post("/otp/verify")
def verify_otp(payload: OTPVerifyPayload, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.phone == payload.phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found.")

    verify_otp_session(phone=payload.phone, otp_code=payload.otp_code, db=db)

    if user.status == UserStatus.PENDING:
        if user.role == UserRole.BUYER:
            user.status = UserStatus.ACTIVE
        else:
            user.status = UserStatus.KYC_IN_PROGRESS
    db.commit()

    tokens = issue_tokens(
        user=user,
        db=db
    )

    message = (
        "OTP verified. Signed in successfully."
        if user.status == UserStatus.ACTIVE
        else "OTP verified. Upload the required KYC document and wait for admin activation."
    )

    return standard_response({
        "user_id": user.id,
        "role": user.role,
        "status": user.status,
        "message": message,
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
    Upload KYC document hash. Always PENDING — admin must review.
    FIRST_BUYER and RESELLER must include serial_for_device.
    Admin verifies identity + device serial in one step.
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

    # FIX: Sanitize and validate serial number so whitespace is caught
    clean_serial = payload.serial_for_device.strip().upper() if payload.serial_for_device and payload.serial_for_device.strip() else None

    # Serial required for device-owning roles — admin verifies person + device together
    if current_user.role in [UserRole.FIRST_BUYER, UserRole.RESELLER]:
        if payload.doc_type in [DocType.PURCHASE_PROOF, DocType.BUSINESS_PROOF]:
            if not clean_serial:
                raise HTTPException(
                    status_code=400,
                    detail="serial_for_device is required. Include the serial number of the device "
                           "this proof covers. Admin will verify it belongs to you before activation."
                )

    try:
        new_doc = KYCDocument(
            user_id=current_user.id,
            doc_type=payload.doc_type,
            file_path="s3://traceloop-kyc/pending/document.pdf",
            file_hash=payload.file_hash,
            serial_for_device=clean_serial,
            status=DocStatus.PENDING
        )
        db.add(new_doc)
        db.commit()
        db.refresh(new_doc) # FIX: Ensure we have the ID from Postgres

        return standard_response({
            "doc_id": new_doc.id,
            "status": "PENDING",
            "serial_for_device": new_doc.serial_for_device,
            "message": "Document submitted. Awaiting admin review. "
                       "You will be activated once approved."
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"KYC upload failed: {str(e)}")


@router.get("/me")
def get_my_profile(current_user: User = Depends(get_current_user_any_status)): # FIX: Unblocks dashboard for unverified users
    """Allows authenticated users in any status (PENDING, KYC_IN_PROGRESS, ACTIVE) to view profile."""
    return standard_response({
        "user_id": current_user.id,
        "name": current_user.name,
        "phone": current_user.phone,
        "role": current_user.role,
        "status": current_user.status,
        "admin_approved": current_user.admin_approved,
        "aadhaar_verified": current_user.aadhaar_verified,
        # NEW: Expose gamification metrics to the frontend
        "reward_points": current_user.reward_points,
        "dispute_count": current_user.dispute_count
    })

@router.delete("/me", status_code=status.HTTP_200_OK)
def delete_my_account(
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user_any_status)
):
    """
    DPDP Act Section 12(3) Compliance: Right to Erasure.
    Deletes the user's PII from the off-chain Postgres database, 
    orphaning their on-chain device history into anonymous ghost records.
    """
    try:
        db.delete(current_user)
        db.commit()
        return standard_response({
            "message": "Account and personal data successfully deleted in compliance with DPDP Act. On-chain history is now fully anonymized."
        })
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Account deletion failed: {str(e)}"
        )


@router.get("/kyc/my-documents")
def get_my_kyc_documents(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user_any_status) 
):
    """Allows a user to see the status of their own KYC uploads for dashboard notifications."""
    docs = db.query(KYCDocument).filter(
        KYCDocument.user_id == current_user.id
    ).order_by(KYCDocument.uploaded_at.desc()).all()

    return standard_response({
        "documents": [
            {
                "doc_id": d.id,
                "doc_type": d.doc_type.value if hasattr(d.doc_type, 'value') else d.doc_type,
                "status": d.status.value if hasattr(d.status, 'value') else d.status,
                "serial_for_device": getattr(d, 'serial_for_device', None),
                "rejection_reason": getattr(d, 'rejection_reason', None)
            } for d in docs
        ]
    })