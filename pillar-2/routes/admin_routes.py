from datetime import datetime
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session
from pydantic import BaseModel

from database import get_db, User, UserRole, UserStatus, AdminAction, ActionType
from auth import RequireRole, get_current_user

router = APIRouter(prefix="/admin", tags=["Admin"])

def standard_response(data: dict = None, code: int = 200) -> dict:
    return {
        "status": "success", "code": code, "data": data or {},
        "error": None, "timestamp": datetime.utcnow().isoformat() + "Z", "traceloop_version": "v1"
    }

class ApprovalPayload(BaseModel):
    notes: str = None

@router.get("/approvals/pending")
def get_pending_approvals(
    db: Session = Depends(get_db),
    # Strict SOP Rule: Only ADMIN can access these routes
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """Fetches all VERIFIABLE and RECYCLER accounts awaiting manual approval."""
    pending_users = db.query(User).filter(
        User.status == UserStatus.KYC_IN_PROGRESS,
        User.role.in_([UserRole.VERIFIABLE, UserRole.RECYCLER])
    ).all()

    return standard_response({
        "pending_accounts": [
            {
                "user_id": u.id,
                "name": u.name,
                "role": u.role,
                "brand_auth_code": u.brand_auth_code,
                "cpcb_number": u.cpcb_number
            } for u in pending_users
        ]
    })


@router.post("/approvals/{target_user_id}/approve")
def approve_account(
    target_user_id: str,
    payload: ApprovalPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.ADMIN]))
):
    """
    Approves a VERIFIABLE or RECYCLER account, shifting them to ACTIVE status.
    This allows them to finally interact with the ledger.
    """
    target_user = db.query(User).filter(User.id == target_user_id).first()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found.")

    if target_user.role not in [UserRole.VERIFIABLE, UserRole.RECYCLER]:
        raise HTTPException(status_code=400, detail="Only Verifiable and Recycler accounts require admin approval.")

    if target_user.status == UserStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Account is already active.")

    try:
        target_user.status = UserStatus.ACTIVE
        target_user.admin_approved = True
        target_user.admin_approved_by = current_user.id
        target_user.admin_approved_at = datetime.utcnow()

        audit_log = AdminAction(
            admin_id=current_user.id,
            action_type=ActionType.APPROVE,
            target_user_id=target_user.id,
            notes=payload.notes
        )
        db.add(audit_log)
        db.commit()

        return standard_response({
            "target_user_id": target_user.id,
            "new_status": target_user.status,
            "message": f"{target_user.role} account approved and activated."
        })

    except Exception as e:
        db.rollback()
        raise HTTPException(status_code=500, detail=str(e))
