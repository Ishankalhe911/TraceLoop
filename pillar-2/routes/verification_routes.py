"""Physical-first verification workflow for TraceLoop.

MVP flow:
1. The owner is physically with a verifier and explicitly shares their Passport ID
   by creating a request assigned to that verifier.
2. The verifier sees only the assigned request in their inbox. No public verifier or
   device directory is needed.
3. Because the owner created the assigned request, the assigned verifier may inspect
   the serial and configuration for that request without a separate accept step.
4. Stamping uses Device.public_id on-chain and Device.id for off-chain foreign keys.

The public Passport ID is not a secret or an authorization credential. The explicit
owner-created, verifier-scoped request is what authorizes private serial disclosure.
"""

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from auth import RequireRole, get_current_user
from database import (
    Device,
    DeviceStatus,
    ManufacturerCache,
    RegistrationStatus,
    User,
    UserRole,
    UserStatus,
    VerificationRequest,
    VerificationStatus,
    get_db,
)
from services.device_identity import is_public_id
from services.ledger_service import LedgerSubmissionError, ledger_service

router = APIRouter(prefix="/verification", tags=["Verification"])
logger = logging.getLogger(__name__)

_ACTIVE_REQUEST_STATUSES = (VerificationStatus.PENDING, VerificationStatus.IN_PROGRESS)
_SERIAL_VISIBLE_REQUEST_STATUSES = (
    VerificationStatus.PENDING,
    VerificationStatus.IN_PROGRESS,
    VerificationStatus.STAMPED,
)


def standard_response(
    data: dict = None, code: int = 200, response_status: str = "success"
) -> dict:
    """Return TraceLoop's standard response envelope."""
    return {
        "status": response_status,
        "code": code,
        "data": data or {},
        "error": None,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceloop_version": "v1",
    }


def generate_sha256(data: str) -> str:
    """Hash the canonical configuration for on-chain integrity."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def _utc_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat()


def _require_active_verifier(user: User) -> None:
    if user.role != UserRole.VERIFIABLE or user.status != UserStatus.ACTIVE:
        raise HTTPException(status_code=403, detail="An active approved verifier account is required.")


def _get_device_by_public_id(db: Session, public_id: str) -> Device:
    if not is_public_id(public_id):
        raise HTTPException(status_code=422, detail="Invalid public Passport ID format.")
    device = db.query(Device).filter(Device.public_id == public_id).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Device not found in Trace-Loop.")
    return device


def _require_confirmed_registration(device: Device) -> None:
    if device.registration_status != RegistrationStatus.CONFIRMED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Device registration is not confirmed on the ledger yet.",
        )


def _get_request_and_device(
    db: Session, verification_id: str
) -> tuple[VerificationRequest, Device]:
    request = db.query(VerificationRequest).filter(
        VerificationRequest.id == verification_id
    ).first()
    if request is None:
        raise HTTPException(status_code=404, detail="Verification request not found.")
    device = db.query(Device).filter(Device.id == request.device_id).first()
    if device is None:
        raise HTTPException(status_code=409, detail="Verification device record is unavailable.")
    return request, device


def _get_assigned_request_for_verifier(
    db: Session, verification_id: str, verifier_id: str
) -> tuple[VerificationRequest, Device]:
    request, device = _get_request_and_device(db, verification_id)
    if request.verifier_id != verifier_id:
        raise HTTPException(status_code=403, detail="This request is not assigned to you.")
    _require_confirmed_registration(device)
    return request, device


# --- PAYLOADS ---
class CreateVerificationRequestPayload(BaseModel):
    public_id: str = Field(..., min_length=1, max_length=40)
    # Owner gets this from the verifier in person, e.g. by scanning their verifier QR.
    # There is deliberately no public verifier directory or device directory.
    verifier_id: str = Field(..., min_length=1, max_length=36)


class StampDevicePayload(BaseModel):
    # Preferred: request ID. device_id is a compatibility field containing the
    # PUBLIC Passport ID, never Device.id (the internal database UUID).
    verification_id: Optional[str] = Field(default=None, min_length=1, max_length=36)
    device_id: Optional[str] = Field(default=None, min_length=1, max_length=40)


class RejectVerificationPayload(BaseModel):
    reason: str = Field(..., min_length=1, max_length=500)


# --- OWNER: SHARE A PASSPORT WITH A SPECIFIC VERIFIER IN PERSON ---
@router.post("/requests", status_code=status.HTTP_201_CREATED)
def create_verification_request(
    payload: CreateVerificationRequestPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Owner explicitly shares a device for verification with a specific verifier.

    The UI can collect verifier_id by scanning the verifier's QR or by entering an
    identifier given at the physical service counter. No public lookup directory is
    required. The response contains no serial or private configuration.
    """
    device = _get_device_by_public_id(db, payload.public_id)
    if device.current_owner_id != current_user.id:
        raise HTTPException(status_code=403, detail="Only the current device owner can share it for verification.")
    _require_confirmed_registration(device)

    verifier = db.query(User).filter(User.id == payload.verifier_id).first()
    if (
        verifier is None
        or verifier.role != UserRole.VERIFIABLE
        or verifier.status != UserStatus.ACTIVE
        or verifier.id == current_user.id
    ):
        raise HTTPException(status_code=422, detail="Selected verifier is unavailable or not authorized.")

    open_request = db.query(VerificationRequest).filter(
        VerificationRequest.device_id == device.id,
        VerificationRequest.status.in_(_ACTIVE_REQUEST_STATUSES),
    ).first()
    if open_request is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This device already has an active verification request.",
        )

    request = VerificationRequest(
        device_id=device.id,  # Always the private internal UUID foreign key.
        requested_by=current_user.id,
        verifier_id=verifier.id,
        status=VerificationStatus.PENDING,
    )
    db.add(request)
    try:
        db.commit()
        db.refresh(request)
    except Exception as exc:
        db.rollback()
        logger.error("Could not create verification request (exception_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Could not create verification request.") from None

    return standard_response(
        {
            "verification_id": request.id,
            "public_id": device.public_id,
            "brand": device.brand_name,
            "status": request.status.value,
            "created_at": _utc_iso(request.created_at),
        },
        201,
    )


@router.get("/requests/assigned")
def get_assigned_verification_requests(
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.VERIFIABLE])),
):
    """Show the verifier only requests that owners explicitly shared with them."""
    _require_active_verifier(current_user)
    requests = db.query(VerificationRequest).filter(
        VerificationRequest.verifier_id == current_user.id,
        VerificationRequest.status.in_(_ACTIVE_REQUEST_STATUSES),
    ).order_by(VerificationRequest.created_at.desc()).all()

    results = []
    for request in requests:
        device = db.query(Device).filter(Device.id == request.device_id).first()
        if device is None or device.registration_status != RegistrationStatus.CONFIRMED:
            continue
        results.append(
            {
                "verification_id": request.id,
                "public_id": device.public_id,
                "brand": device.brand_name,
                "status": request.status.value,
                "created_at": _utc_iso(request.created_at),
            }
        )
    return standard_response({"total": len(results), "requests": results})


@router.post("/requests/{verification_id}/reject")
def reject_verification_request(
    verification_id: str,
    payload: RejectVerificationPayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.VERIFIABLE])),
):
    """Assigned verifier can decline a physically presented device before stamping."""
    _require_active_verifier(current_user)
    request, device = _get_assigned_request_for_verifier(db, verification_id, current_user.id)
    if request.status != VerificationStatus.PENDING or request.config_snapshot is not None:
        raise HTTPException(status_code=409, detail="Only an unattempted request can be rejected.")

    reason = payload.reason.strip()
    if not reason:
        raise HTTPException(status_code=422, detail="A rejection reason is required.")
    request.status = VerificationStatus.REJECTED
    request.rejection_reason = reason
    try:
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Could not reject verification request (exception_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Could not reject verification request.") from None
    return standard_response(
        {"verification_id": request.id, "public_id": device.public_id, "status": request.status.value}
    )


@router.get("/requests/{verification_id}")
def get_verification_request_details(
    verification_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Show private serial/config only to current owner or assigned verifier.

    Owner-created assignment is the explicit consent boundary for the physical
    inspection. A Passport ID alone never grants access to serial information.
    """
    request, device = _get_request_and_device(db, verification_id)
    is_current_owner = current_user.id == device.current_owner_id
    is_original_requester = current_user.id == request.requested_by
    is_assigned_verifier = current_user.id == request.verifier_id

    if is_assigned_verifier:
        _require_active_verifier(current_user)
        _require_confirmed_registration(device)
    if not is_current_owner and not is_original_requester and not is_assigned_verifier:
        raise HTTPException(status_code=403, detail="You are not authorized to view this request.")

    data = {
        "verification_id": request.id,
        "public_id": device.public_id,
        "brand": device.brand_name,
        "status": request.status.value,
        "created_at": _utc_iso(request.created_at),
        "manufacturer_match": request.manufacturer_match,
        "stamped_at": _utc_iso(request.stamped_at),
        "reconciliation_required": (
            request.status == VerificationStatus.IN_PROGRESS and request.config_snapshot is not None
        ),
    }
    # The assigned verifier can see only the device explicitly shared by its
    # current owner. A former owner/requester sees workflow status, not current PII.
    verifier_has_active_share = (
        is_assigned_verifier and request.status in _SERIAL_VISIBLE_REQUEST_STATUSES
    )
    if is_current_owner or verifier_has_active_share:
        data["serial_raw"] = device.serial_raw
        data["current_config"] = device.current_config
    else:
        data["serial_available_for_this_request"] = False
    return standard_response(data)


# --- VERIFIER: STAMP THROUGH AN OWNER-SHARED REQUEST; NO ACCEPT STEP REQUIRED ---
@router.post("/stamp", status_code=status.HTTP_200_OK)
def issue_verification_stamp(
    payload: StampDevicePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.VERIFIABLE])),
):
    """Stamp an owner-shared request after the physical inspection.

    A separate accept endpoint is intentionally omitted. Creating and assigning a
    request is the owner's explicit share action; the verifier can then inspect and
    stamp it in person. Reconciliation-required requests are never blindly resubmitted.
    """
    _require_active_verifier(current_user)
    if bool(payload.verification_id) == bool(payload.device_id):
        raise HTTPException(
            status_code=422,
            detail="Supply exactly one of verification_id or public Passport ID in device_id.",
        )

    if payload.verification_id:
        request, device = _get_assigned_request_for_verifier(
            db, payload.verification_id, current_user.id
        )
    else:
        device = _get_device_by_public_id(db, payload.device_id)
        _require_confirmed_registration(device)
        request = db.query(VerificationRequest).filter(
            VerificationRequest.device_id == device.id,
            VerificationRequest.verifier_id == current_user.id,
            VerificationRequest.status.in_(_ACTIVE_REQUEST_STATUSES),
        ).order_by(VerificationRequest.created_at.desc()).first()
        if request is None:
            raise HTTPException(
                status_code=409,
                detail="The owner must share this Passport with you before it can be stamped.",
            )

    if request.verifier_id != current_user.id:
        raise HTTPException(status_code=403, detail="This request is not assigned to you.")
    if request.status == VerificationStatus.STAMPED:
        raise HTTPException(status_code=409, detail="This verification request has already been stamped.")
    if request.status not in _ACTIVE_REQUEST_STATUSES:
        raise HTTPException(status_code=409, detail="This verification request is no longer active.")
    if request.device_id != device.id:
        raise HTTPException(status_code=409, detail="Verification request/device mismatch.")
    _require_confirmed_registration(device)

    request_id = request.id
    public_id = device.public_id

    # A snapshot is a durable latch after a prior attempt. An uncertain result must
    # be reconciled manually; no blind duplicate stamp is sent.
    if request.config_snapshot is not None:
        return standard_response(
            {
                "verification_id": request_id,
                "public_id": public_id,
                "verification_status": "RECONCILIATION_REQUIRED",
                "message": "A previous stamp attempt may have reached the ledger; do not retry automatically.",
            },
            202,
            "pending",
        )

    cached_mfg = db.query(ManufacturerCache).filter(
        ManufacturerCache.serial_hash == device.serial_hash
    ).first()
    differences = []
    if cached_mfg:
        manufacturer_config = cached_mfg.original_config or {}
        current_config = device.current_config or {}
        for component in sorted(set(manufacturer_config.keys()) | set(current_config.keys())):
            if manufacturer_config.get(component) != current_config.get(component):
                differences.append(
                    {
                        "component": component,
                        "manufacturer_value": manufacturer_config.get(component),
                        "current_value": current_config.get(component),
                    }
                )

    confirmed_config_hash = generate_sha256(json.dumps(device.current_config, sort_keys=True))

    # Read-only preflight first. No request is latched if the chain check fails.
    try:
        can_verify = ledger_service.can_verify(public_id)
    except Exception as exc:
        logger.warning("Verification preflight unavailable (exception_type=%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503,
            detail="Could not check the device's on-chain verification state. Please retry later.",
        ) from None
    if not can_verify:
        raise HTTPException(status_code=409, detail="Device cannot be verified in its current on-chain state.")

    # Atomically move the request to IN_PROGRESS and latch the exact config before
    # the mutating call. This prevents concurrent stamp requests from both passing.
    try:
        claimed = db.query(VerificationRequest).filter(
            VerificationRequest.id == request_id,
            VerificationRequest.verifier_id == current_user.id,
            VerificationRequest.status.in_((VerificationStatus.PENDING, VerificationStatus.IN_PROGRESS)),
            VerificationRequest.config_snapshot.is_(None),
        ).update(
            {
                VerificationRequest.status: VerificationStatus.IN_PROGRESS,
                VerificationRequest.config_snapshot: device.current_config,
            },
            synchronize_session=False,
        )
        if claimed != 1:
            db.rollback()
            return standard_response(
                {
                    "verification_id": request_id,
                    "public_id": public_id,
                    "verification_status": "RECONCILIATION_REQUIRED",
                    "message": "A stamp attempt is already in progress or needs reconciliation; do not retry automatically.",
                },
                202,
                "pending",
            )
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("Could not persist verification attempt (exception_type=%s)", type(exc).__name__)
        raise HTTPException(status_code=500, detail="Could not prepare verification safely.") from None

    try:
        chain_result = ledger_service.verify_device(
            public_id=public_id,
            confirmed_config_hash=confirmed_config_hash,
        )
    except LedgerSubmissionError as exc:
        if not exc.submission_attempted:
            # This is known not to have been submitted. Clear the latch and make the
            # request PENDING again. If clearing fails, keep it locked conservatively.
            try:
                db.query(VerificationRequest).filter(
                    VerificationRequest.id == request_id,
                    VerificationRequest.status == VerificationStatus.IN_PROGRESS,
                ).update(
                    {
                        VerificationRequest.config_snapshot: None,
                        VerificationRequest.status: VerificationStatus.PENDING,
                    },
                    synchronize_session=False,
                )
                db.commit()
            except Exception as clear_exc:
                db.rollback()
                logger.error("Could not clear safe verification latch (exception_type=%s)", type(clear_exc).__name__)
                return standard_response(
                    {
                        "verification_id": request_id,
                        "public_id": public_id,
                        "verification_status": "RECONCILIATION_REQUIRED",
                        "message": "Verification did not complete; this request is locked for manual review.",
                    },
                    202,
                    "pending",
                )
            raise HTTPException(
                status_code=502,
                detail="Verification transaction could not be submitted. You may retry this request.",
            ) from None

        logger.warning("Verification outcome uncertain (verification_id=%s)", request_id)
        return standard_response(
            {
                "verification_id": request_id,
                "public_id": public_id,
                "verification_status": "RECONCILIATION_REQUIRED",
                "message": "The transaction outcome is uncertain. Do not submit another stamp until reconciled.",
            },
            202,
            "pending",
        )
    except Exception as exc:
        logger.warning("Unexpected verification outcome (exception_type=%s)", type(exc).__name__)
        return standard_response(
            {
                "verification_id": request_id,
                "public_id": public_id,
                "verification_status": "RECONCILIATION_REQUIRED",
                "message": "The transaction outcome is uncertain. Do not submit another stamp until reconciled.",
            },
            202,
            "pending",
        )

    stamped_at = datetime.now(timezone.utc)
    # Re-load after the conditional UPDATE because synchronize_session=False was used.
    request = db.query(VerificationRequest).filter(VerificationRequest.id == request_id).one()
    request.status = VerificationStatus.STAMPED
    request.stamped_at = stamped_at
    request.manufacturer_match = None if cached_mfg is None else (len(differences) == 0)
    device.status = DeviceStatus.VERIFIED
    device.stamp_valid = True
    device.last_verified_at = stamped_at
    device.last_verified_by = current_user.id
    try:
        db.commit()
        db.refresh(request)
    except Exception as exc:
        db.rollback()
        logger.error(
            "Chain stamp confirmed but local persistence failed (verification_id=%s, tx_id=%s, exception_type=%s)",
            request_id,
            chain_result.get("tx_id", "unknown"),
            type(exc).__name__,
        )
        return standard_response(
            {
                "verification_id": request_id,
                "public_id": public_id,
                "chain_tx_id": chain_result.get("tx_id"),
                "verification_status": "RECONCILIATION_REQUIRED",
                "message": "The ledger confirmed the stamp, but local state needs reconciliation. Do not retry the stamp.",
            },
            202,
            "pending",
        )

    return standard_response(
        {
            "public_id": public_id,
            "device_id": public_id,  # Compatibility alias; never the internal UUID.
            "verification_id": request_id,
            "chain_tx_id": chain_result["tx_id"],
            "stamp_valid": True,
            "differences_noted": differences,
            "manufacturer_match": request.manufacturer_match,
            "status": DeviceStatus.VERIFIED.value,
        }
    )


@router.get("/device/{device_id}/history")
def get_verification_history(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Return sanitized verification history to the owner or a workflow participant.

    `device_id` is the public Passport ID. The response does not expose verifier IDs,
    serial numbers, raw evidence, or internal Device.id values.
    """
    device = _get_device_by_public_id(db, device_id)
    _require_confirmed_registration(device)

    is_current_owner = device.current_owner_id == current_user.id
    has_workflow_relationship = db.query(VerificationRequest.id).filter(
        VerificationRequest.device_id == device.id,
        (VerificationRequest.requested_by == current_user.id)
        | (VerificationRequest.verifier_id == current_user.id),
    ).first() is not None
    if not is_current_owner and not has_workflow_relationship:
        raise HTTPException(status_code=403, detail="You are not authorized to view this verification history.")

    history = db.query(VerificationRequest).filter(
        VerificationRequest.device_id == device.id
    ).order_by(VerificationRequest.created_at.desc()).all()
    return standard_response(
        {
            "public_id": device.public_id,
            "device_id": device.public_id,
            "stamp_history": [
                {
                    "verification_id": request.id,
                    "status": request.status.value,
                    "stamped_at": _utc_iso(request.stamped_at),
                    "manufacturer_match": request.manufacturer_match,
                }
                for request in history
            ],
        }
    )
