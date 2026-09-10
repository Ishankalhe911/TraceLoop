from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, root_validator
from typing import Optional
from datetime import datetime

# Import frozen models from the unified database.py
from database import get_db, User, UserRole, UserStatus, DocType, DocStatus, KYCDocument
from auth import get_current_user

router = APIRouter()

def standard_response(data: dict = None, code: int = 200) -> dict:
    """Mandatory API response envelope per SOP Section 4.1"""
    return {
        "status": "success",
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.utcnow().isoformat() + "Z",
        "traceloop_version": "v1"
    }

# --- PAYLOAD SCHEMAS ---
class RegisterUserPayload(BaseModel):
    phone: str = Field(..., max_length=15, description="Phone number for OTP")
    name: str = Field(..., max_length=120)
    role: UserRole
    # Strict DPDP/SOP Rule: Only accept the last 4 digits of Aadhaar[cite: 14, 15]
    aadhaar_last4: str = Field(..., min_length=4, max_length=4) 
    
    gst_number: Optional[str] = None
    cpcb_number: Optional[str] = None
    brand_auth_code: Optional[str] = None
    service_center_id: Optional[str] = None

    @root_validator(skip_on_failure=True)
    def validate_role_requirements(cls, values):
        """Enforces SOP Section 1.2 KYC Requirements dynamically based on role[cite: 15]."""
        role = values.get("role")
        
        if role == UserRole.ADMIN:
            raise ValueError("Admin role cannot be self-assigned.")
            
        if role == UserRole.RESELLER and not values.get("gst_number"):
            raise ValueError("GST registration number is mandatory for RESELLER onboarding.")
            
        if role == UserRole.RECYCLER and not values.get("cpcb_number"):
            raise ValueError("CPCB registration number is mandatory for RECYCLER onboarding.")
            
        if role == UserRole.VERIFIABLE:
            if not values.get("brand_auth_code") or not values.get("service_center_id"):
                raise ValueError("Brand authorization code and service center ID are mandatory for VERIFIABLE nodes.")
                
        return values

class OTPVerifyPayload(BaseModel):
    phone: str
    otp_code: str

class KYCUploadPayload(BaseModel):
    doc_type: DocType
    file_hash: str  # In a real app, this would use FastAPI UploadFile. Using hash for hackathon simplicity.


# --- ROUTES ---

@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_user(payload: RegisterUserPayload, db: Session = Depends(get_db)):
    """Registers a new user off-chain and sets status to PENDING[cite: 15]."""
    existing_user = db.query(User).filter(User.phone == payload.phone).first()
    if existing_user:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Phone number is already registered.")

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
            "message": "Registration initiated. Proceed to OTP verification."
        }, 201)
        
    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=f"Database error during registration: {str(e)}")


@router.post("/otp/verify")
def verify_otp(payload: OTPVerifyPayload, db: Session = Depends(get_db)):
    """
    Simulates OTP verification. Advances auto-activated roles to ACTIVE, 
    and manual roles to KYC_IN_PROGRESS[cite: 15].
    """
    user = db.query(User).filter(User.phone == payload.phone).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    # Hackathon Mock: Assume any 4-digit OTP is valid
    if len(payload.otp_code) != 4:
        raise HTTPException(status_code=400, detail="Invalid OTP")

    # Role-based activation rules per SOP Section 1.2[cite: 15]
    if user.role in [UserRole.BUYER, UserRole.FIRST_BUYER, UserRole.RESELLER]:
        user.status = UserStatus.ACTIVE
    else:
        # VERIFIABLE and RECYCLER require manual admin approval[cite: 15]
        user.status = UserStatus.KYC_IN_PROGRESS 

    db.commit()
    
    return standard_response({
        "user_id": user.id,
        "new_status": user.status,
        "message": "OTP verified successfully."
    })


@router.post("/kyc/upload")
def upload_kyc_document(
    payload: KYCUploadPayload, 
    db: Session = Depends(get_db), 
    current_user: User = Depends(get_current_user)
):
    """
    Logs KYC document uploads and auto-activates specific roles.
    Enforces SOP Section 1.2 document requirements.
    """
    # 1. Enforce Role-Specific Document Requirements
    if current_user.role == UserRole.FIRST_BUYER and payload.doc_type != DocType.PURCHASE_PROOF:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="FIRST_BUYER must upload a PURCHASE_PROOF."
        )
        
    if current_user.role == UserRole.RESELLER and payload.doc_type != DocType.BUSINESS_PROOF:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, 
            detail="RESELLER must upload a BUSINESS_PROOF."
        )

    try:
        # 2. Save the document
        new_doc = KYCDocument(
            user_id=current_user.id,
            doc_type=payload.doc_type,
            file_path="s3://mock-bucket/document.pdf",
            file_hash=payload.file_hash,
            # Hackathon MVP: Auto-accepting the document to streamline testing
            status=DocStatus.ACCEPTED 
        )
        db.add(new_doc)
        
        # 3. Advance User Status to ACTIVE for specific roles[cite: 5, 7]
        message = "Document uploaded successfully."
        if current_user.role in [UserRole.FIRST_BUYER, UserRole.RESELLER]:
            current_user.status = UserStatus.ACTIVE
            message += " Account is now ACTIVE."
        
        db.commit()
        
        return standard_response({
            "doc_id": new_doc.id,
            "new_status": current_user.status,
            "message": message
        })
        
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, 
            detail=f"Failed to process upload: {str(e)}"
        )

@router.get("/me")
def get_my_profile(current_user: User = Depends(get_current_user)):
    """Fetches the currently authenticated user's profile."""
    return standard_response({
        "user_id": current_user.id,
        "name": current_user.name,
        "role": current_user.role,
        "status": current_user.status,
        "admin_approved": current_user.admin_approved
    })
