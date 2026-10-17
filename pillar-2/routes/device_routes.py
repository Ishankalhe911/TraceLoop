"""Device registration and owner-facing device routes.

Identity contract:
- Device.id is a private internal UUID for database foreign keys.
- Device.public_id is the brand-labelled random public/on-chain identifier.
- Raw serial and serial fingerprint stay off-chain and are never sent to ledger.

Registration persists a PENDING device before calling Algorand. An uncertain
ledger outcome is retained for reconciliation; this route never blindly retries.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from auth import RequireRole, get_current_user
from database import (
    DocStatus,
    DocType,
    Device,
    DeviceStatus,
    KYCDocument,
    RegistrationStatus,
    User,
    UserRole,
    get_db,
)
from services.device_identity import (
    generate_internal_id,
    generate_public_id,
    resolve_brand,
)
from services.ledger_service import LedgerSubmissionError, ledger_service

router = APIRouter(prefix="/devices", tags=["Devices"])
logger = logging.getLogger(__name__)


def standard_response(
    data: Optional[dict] = None,
    code: int = 200,
    *,
    result_status: str = "success",
    error: Optional[str] = None,
) -> dict:
    return {
        "status": result_status,
        "code": code,
        "data": data or {},
        "error": error,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "traceloop_version": "v1",
    }


def generate_sha256(data: str) -> str:
    """Return a SHA-256 hex digest. Callers must keep serial digests private."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def make_aware(dt: datetime) -> Optional[datetime]:
    """Ensure a datetime is timezone-aware (UTC), handling naive and aware values."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _enum_value(value: Any) -> Any:
    return value.value if hasattr(value, "value") else value


def _persist_registration_outcome(
    db: Session,
    internal_id: str,
    registration_status: RegistrationStatus,
    *,
    tx_id: Optional[str] = None,
    confirmed_round: Optional[int] = None,
    error_code: Optional[str] = None,
) -> bool:
    """Best-effort state update; returns False rather than hiding persistence failure.

    Error codes are fixed internal identifiers. Exception text and request data are
    deliberately never persisted in this field.
    """
    try:
        device = db.query(Device).filter(Device.id == internal_id).one_or_none()
        if device is None:
            raise LookupError("Registration row was not found")

        device.registration_status = registration_status
        if tx_id is not None:
            device.registration_tx_id = tx_id
        if confirmed_round is not None:
            device.registration_confirmed_round = int(confirmed_round)
        device.registration_error_code = error_code
        device.registration_updated_at = datetime.now(timezone.utc)
        db.commit()
        return True
    except Exception as exc:
        db.rollback()
        logger.error(
            "Could not persist registration outcome (exception_type=%s)",
            type(exc).__name__,
        )
        return False


def _registration_failure_response(
    *,
    response: Optional[Response],
    db: Session,
    device: Device,
    desired_status: RegistrationStatus,
    http_code: int,
    error_code: str,
    message: str,
    tx_id: Optional[str] = None,
    confirmed_round: Optional[int] = None,
) -> dict:
    """Persist a failure/uncertain result and return a safe actionable response."""
    # Capture response-safe values before any commit/rollback can expire ORM objects.
    internal_id = device.id
    public_id = device.public_id
    brand_name = device.brand_name
    device_status = _enum_value(device.status)

    persisted = _persist_registration_outcome(
        db,
        internal_id,
        desired_status,
        tx_id=tx_id,
        confirmed_round=confirmed_round,
        error_code=error_code,
    )

    # A failed update leaves the already-committed row at its last durable state,
    # normally PENDING. Say this explicitly rather than claiming the new state saved.
    actual_status = desired_status if persisted else RegistrationStatus.PENDING
    effective_code = http_code
    if response is not None:
        response.status_code = effective_code

    return standard_response(
        {
            "device_id": public_id,
            "public_id": public_id,
            "brand": brand_name,
            "device_status": device_status,
            "registration_status": _enum_value(actual_status),
            "state_persisted": persisted,
            "reconciliation_required": (
                desired_status == RegistrationStatus.RECONCILIATION_REQUIRED or not persisted
            ),
            "chain_tx_id": tx_id,
            "confirmed_round": confirmed_round,
            "message": message,
        },
        effective_code,
        result_status="error" if actual_status == RegistrationStatus.FAILED else "pending",
        error=message,
    )


class RegisterDevicePayload(BaseModel):
    brand_code: str = Field(..., min_length=2, max_length=4)
    # Kept for request compatibility. The backend uses its canonical brand name.
    brand_name: str = Field(..., max_length=40)
    serial_raw: str = Field(..., min_length=1, max_length=80)
    original_config: Dict[str, Any]


@router.post("/register", status_code=status.HTTP_201_CREATED)
def register_device(
    payload: RegisterDevicePayload,
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.FIRST_BUYER])),
    response: Response = None,
):
    """Register a device while retaining a local recovery record.

    A pending local row is committed before the external ledger call. Definite
    pre-submission failures are marked FAILED; once submission may have started,
    errors are classified as RECONCILIATION_REQUIRED. No automatic retry occurs.
    """
    try:
        brand = resolve_brand(payload.brand_code)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Unsupported brand code.",
        )

    serial_upper = (payload.serial_raw or "").strip().upper()
    if not serial_upper:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="A non-empty hardware serial is required.",
        )

    # The normalized serial and its hash are private matching values only.
    serial_hash = generate_sha256(serial_upper)

    existing_device = db.query(Device).filter(
        (Device.serial_hash == serial_hash) | (Device.serial_raw == serial_upper)
    ).first()
    if existing_device:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="This physical device is already registered in Trace-Loop or has a registration awaiting recovery.",
        )

    # Purchase proof remains bound to the normalized hardware serial.
    proof = db.query(KYCDocument).filter(
        KYCDocument.user_id == current_user.id,
        KYCDocument.doc_type == DocType.PURCHASE_PROOF,
        KYCDocument.status == DocStatus.ACCEPTED,
        KYCDocument.serial_for_device == serial_upper,
    ).first()
    if not proof:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=(
                "Admin-approved purchase proof is required for this device. "
                "Upload purchase proof through KYC and await admin approval."
            ),
        )

    owner_hash = generate_sha256(str(current_user.id))
    config_str = json.dumps(payload.original_config, sort_keys=True, separators=(",", ":"))
    config_hash = generate_sha256(config_str)

    internal_id = generate_internal_id()
    public_id = generate_public_id(brand.slug)

    # Persist a durable local row BEFORE making the external call. If the process
    # exits or the ledger outcome is uncertain, this row lets the subscriber map
    # the on-chain public_id back to the internal UUID.
    pending_device = Device(
        id=internal_id,
        public_id=public_id,
        serial_hash=serial_hash,
        serial_raw=serial_upper,
        brand_code=brand.code,
        brand_name=brand.name,
        original_config=payload.original_config,
        current_config=payload.original_config,
        current_owner_id=current_user.id,
        current_owner_hash=owner_hash,
        status=DeviceStatus.REGISTERED,
        stamp_valid=False,
        registration_status=RegistrationStatus.PENDING,
        registration_attempted_at=None,
        registration_error_code=None,
        registration_tx_id=None,
        registration_confirmed_round=None,
        registration_updated_at=datetime.now(timezone.utc),
    )

    try:
        db.add(pending_device)
        db.commit()
    except IntegrityError:
        db.rollback()
        # Resolve the most likely race without exposing serial/hash values.
        try:
            duplicate = db.query(Device).filter(
                (Device.serial_hash == serial_hash) | (Device.serial_raw == serial_upper)
            ).first()
            if duplicate:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail="This physical device is already registered in Trace-Loop or has a registration awaiting recovery.",
                )
            public_id_collision = db.query(Device).filter(Device.public_id == public_id).first()
        except HTTPException:
            raise
        except Exception as lookup_exc:
            logger.error(
                "Could not classify device record insert failure (exception_type=%s)",
                type(lookup_exc).__name__,
            )
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not safely create the device registration record. No ledger call was made.",
            )
        if public_id_collision:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Could not allocate a unique public passport ID. Please retry.",
            )
        logger.error("Device registration row insert failed due to an integrity constraint")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create the device registration record. No ledger call was made.",
        )
    except Exception as exc:
        db.rollback()
        logger.error(
            "Device registration row insert failed (exception_type=%s)",
            type(exc).__name__,
        )
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Could not create the device registration record. No ledger call was made.",
        )

    # Mark the attempt time durably before calling the ledger. If this write fails,
    # do not call Algorand; the pending row is retained for manual recovery.
    try:
        pending_device.registration_attempted_at = datetime.now(timezone.utc)
        pending_device.registration_updated_at = datetime.now(timezone.utc)
        db.commit()
    except Exception as exc:
        db.rollback()
        persisted = _persist_registration_outcome(
            db,
            internal_id,
            RegistrationStatus.FAILED,
            error_code="LOCAL_ATTEMPT_MARK_FAILED",
        )
        if response is not None:
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return standard_response(
            {
                "device_id": public_id,
                "public_id": public_id,
                "brand": brand.name,
                "device_status": DeviceStatus.REGISTERED.value,
                "registration_status": (
                    RegistrationStatus.FAILED.value if persisted else RegistrationStatus.PENDING.value
                ),
                "state_persisted": persisted,
                "reconciliation_required": not persisted,
                "message": "The registration attempt could not be recorded, so no ledger call was made. Contact support before retrying.",
            },
            status.HTTP_503_SERVICE_UNAVAILABLE,
            result_status="error",
            error="The registration attempt could not be recorded; no ledger call was made.",
        )

    # Only now begin the external side effect. A typed LedgerSubmissionError tells
    # us whether the SDK call could have submitted a transaction.
    try:
        chain_result = ledger_service.register_device(
            public_id=public_id,
            owner_hash=owner_hash,
            config_hash=config_hash,
        )
    except LedgerSubmissionError as exc:
        if exc.submission_attempted:
            return _registration_failure_response(
                response=response,
                db=db,
                device=pending_device,
                desired_status=RegistrationStatus.RECONCILIATION_REQUIRED,
                http_code=status.HTTP_202_ACCEPTED,
                error_code="LEDGER_OUTCOME_UNKNOWN",
                message=(
                    "The ledger submission outcome is uncertain. Do not retry registration; "
                    "the existing Passport ID must be reconciled first."
                ),
            )
        return _registration_failure_response(
            response=response,
            db=db,
            device=pending_device,
            desired_status=RegistrationStatus.FAILED,
            http_code=status.HTTP_502_BAD_GATEWAY,
            error_code="LEDGER_PRE_SUBMISSION_FAILED",
            message=(
                "The ledger transaction was not submitted. The registration record was retained; "
                "do not submit a second registration until this attempt is reviewed."
            ),
        )
    except Exception as exc:
        # Untyped/unexpected exceptions are conservative: the route cannot prove
        # the transaction was never broadcast, so it must not retry automatically.
        logger.error(
            "Ledger registration outcome is uncertain (exception_type=%s)",
            type(exc).__name__,
        )
        return _registration_failure_response(
            response=response,
            db=db,
            device=pending_device,
            desired_status=RegistrationStatus.RECONCILIATION_REQUIRED,
            http_code=status.HTTP_202_ACCEPTED,
            error_code="LEDGER_OUTCOME_UNKNOWN",
            message=(
                "The ledger submission outcome is uncertain. Do not retry registration; "
                "the existing Passport ID must be reconciled first."
            ),
        )

    # A return value without a transaction ID and confirmed round cannot safely
    # be represented as complete. Treat it as uncertain, not as success.
    tx_id = chain_result.get("tx_id") if isinstance(chain_result, dict) else None
    confirmed_round = chain_result.get("confirmed_round") if isinstance(chain_result, dict) else None
    valid_confirmation = (
        isinstance(tx_id, str)
        and bool(tx_id.strip())
        and isinstance(confirmed_round, int)
        and not isinstance(confirmed_round, bool)
        and confirmed_round > 0
    )
    if not valid_confirmation:
        return _registration_failure_response(
            response=response,
            db=db,
            device=pending_device,
            desired_status=RegistrationStatus.RECONCILIATION_REQUIRED,
            http_code=status.HTTP_202_ACCEPTED,
            error_code="LEDGER_CONFIRMATION_INCOMPLETE",
            message=(
                "A complete ledger confirmation could not be recorded. Do not retry registration; "
                "the existing Passport ID must be reconciled first."
            ),
            tx_id=tx_id if isinstance(tx_id, str) and tx_id.strip() else None,
            confirmed_round=(
                confirmed_round
                if isinstance(confirmed_round, int) and not isinstance(confirmed_round, bool) and confirmed_round > 0
                else None
            ),
        )

    # Persist the confirmed outcome. If this commit fails after chain confirmation,
    # the initial pending row remains and is explicitly marked for reconciliation
    # where possible. Never generate a replacement public ID or resubmit here.
    confirmed_saved = _persist_registration_outcome(
        db,
        internal_id,
        RegistrationStatus.CONFIRMED,
        tx_id=tx_id,
        confirmed_round=confirmed_round,
        error_code=None,
    )
    if not confirmed_saved:
        recon_saved = _persist_registration_outcome(
            db,
            internal_id,
            RegistrationStatus.RECONCILIATION_REQUIRED,
            tx_id=tx_id,
            confirmed_round=confirmed_round,
            error_code="CONFIRMATION_PERSISTENCE_FAILED",
        )
        actual_status = (
            RegistrationStatus.RECONCILIATION_REQUIRED if recon_saved else RegistrationStatus.PENDING
        )
        if response is not None:
            response.status_code = status.HTTP_202_ACCEPTED
        message = (
            "Algorand returned a confirmation, but the local confirmation update failed. "
            "Do not retry; reconcile this existing Passport ID."
        )
        return standard_response(
            {
                "device_id": public_id,
                "public_id": public_id,
                "brand": brand.name,
                "device_status": DeviceStatus.REGISTERED.value,
                "registration_status": actual_status.value,
                "state_persisted": recon_saved,
                "reconciliation_required": True,
                "chain_tx_id": tx_id,
                "confirmed_round": confirmed_round,
                "message": message,
            },
            status.HTTP_202_ACCEPTED,
            result_status="pending",
            error=message,
        )

    if response is not None:
        response.status_code = status.HTTP_201_CREATED
    return standard_response(
        {
            # Compatibility alias: device_id is the public ID, never the internal UUID.
            "device_id": public_id,
            "public_id": public_id,
            "brand": brand.name,
            "status": DeviceStatus.REGISTERED.value,
            "device_status": DeviceStatus.REGISTERED.value,
            "registration_status": RegistrationStatus.CONFIRMED.value,
            "chain_tx_id": tx_id,
            "confirmed_round": confirmed_round,
        },
        status.HTTP_201_CREATED,
    )


@router.get("/my-devices")
def get_my_devices(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch devices owned by the authenticated user without exposing private IDs."""
    devices = (
        db.query(Device)
        .filter(Device.current_owner_id == current_user.id)
        .order_by(Device.updated_at.desc())
        .all()
    )

    results = []
    for device in devices:
        registration_status = _enum_value(device.registration_status)
        device_status = _enum_value(device.status)
        # Do not present a row with an unconfirmed chain registration as a normal
        # REGISTERED device to older clients that only inspect the `status` key.
        if registration_status == RegistrationStatus.CONFIRMED.value:
            display_status = device_status
        elif registration_status == RegistrationStatus.RECONCILIATION_REQUIRED.value:
            display_status = "RECONCILIATION_REQUIRED"
        elif registration_status == RegistrationStatus.FAILED.value:
            display_status = "REGISTRATION_FAILED"
        else:
            display_status = "PENDING_REGISTRATION"
        results.append(
            {
                "device_id": device.public_id,
                "public_id": device.public_id,
                "brand": device.brand_name,
                "status": display_status,
                "device_status": device_status,
                "registration_status": registration_status,
                "stamp_valid": device.stamp_valid,
                "is_for_sale": device.is_for_sale,
                "asking_price": float(device.asking_price) if device.asking_price else None,
                "last_verified_at": (
                    make_aware(device.last_verified_at).isoformat()
                    if device.last_verified_at else None
                ),
                "current_config": device.current_config,
            }
        )

    return standard_response({"total_owned": len(results), "devices": results})


@router.get("/{device_id}")
def get_device(
    device_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """Fetch private device state by public ID for the current owner only."""
    db_device = db.query(Device).filter(Device.public_id == device_id).first()
    if not db_device:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Device not found.")

    if db_device.current_owner_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="You are not authorized to view this device's private details.",
        )

    registration_status = _enum_value(db_device.registration_status)
    # A definite pre-submission failure or a still-pending row is not represented
    # as an on-chain registration. Reconciliation-required rows may be inspected
    # against the chain, but this read does not automatically change DB status.
    if registration_status in (RegistrationStatus.PENDING.value, RegistrationStatus.FAILED.value):
        return standard_response(
            {
                "device_id": db_device.public_id,
                "public_id": db_device.public_id,
                "brand": db_device.brand_name,
                "serial_raw": db_device.serial_raw,
                "db_status": _enum_value(db_device.status),
                "registration_status": registration_status,
                "chain_status": (
                    "NOT_SUBMITTED" if registration_status == RegistrationStatus.FAILED.value else "NOT_CHECKED"
                ),
                "stamp_valid": db_device.stamp_valid,
                "current_config": db_device.current_config,
                "registered_at": make_aware(db_device.registered_at).isoformat() if db_device.registered_at else None,
                "registration_error_code": db_device.registration_error_code,
            }
        )

    try:
        chain_state = ledger_service.get_device_state(db_device.public_id)
        return standard_response(
            {
                "device_id": db_device.public_id,
                "public_id": db_device.public_id,
                "brand": db_device.brand_name,
                "serial_raw": db_device.serial_raw,
                "db_status": _enum_value(db_device.status),
                "registration_status": registration_status,
                "chain_status": chain_state["state_label"],
                "stamp_valid": chain_state["stamp_valid"],
                "current_config": db_device.current_config,
                "last_verified_at": (
                    make_aware(db_device.last_verified_at).isoformat()
                    if db_device.last_verified_at else None
                ),
                "registered_at": make_aware(db_device.registered_at).isoformat() if db_device.registered_at else None,
                "registration_tx_id": db_device.registration_tx_id,
                "registration_confirmed_round": db_device.registration_confirmed_round,
            }
        )
    except Exception as exc:
        logger.warning("Ledger state unavailable (exception_type=%s)", type(exc).__name__)
        return standard_response(
            {
                "device_id": db_device.public_id,
                "public_id": db_device.public_id,
                "brand": db_device.brand_name,
                "serial_raw": db_device.serial_raw,
                "db_status": _enum_value(db_device.status),
                "registration_status": registration_status,
                "chain_status": "UNREACHABLE",
                "stamp_valid": db_device.stamp_valid,
                "current_config": db_device.current_config,
                "last_verified_at": (
                    make_aware(db_device.last_verified_at).isoformat()
                    if db_device.last_verified_at else None
                ),
                "registered_at": make_aware(db_device.registered_at).isoformat() if db_device.registered_at else None,
                "registration_tx_id": db_device.registration_tx_id,
                "registration_confirmed_round": db_device.registration_confirmed_round,
                "error_note": "Could not fetch real-time blockchain state.",
            }
        )
