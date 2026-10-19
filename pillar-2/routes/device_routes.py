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
    EvidenceRecord,
    EvidenceSubjectType,
    EvidenceType,
    EvidenceVerificationStatus,
    ChainStatus,
    Device,
    DeviceStatus,
    IdentityVersion,
    KYCDocument,
    LedgerEvent,
    RegistrationStatus,
    User,
    UserRole,
    get_db,
)
from services.device_identity import (
    generate_internal_id,
    generate_public_id,
    is_public_id,
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
        identity_version=IdentityVersion.V2_PUBLIC_ID,
        legacy_chain_id=None,
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


@router.get("/registration-options")
def get_registration_options(
    db: Session = Depends(get_db),
    current_user: User = Depends(RequireRole([UserRole.FIRST_BUYER])),
):
    """Return only this user's approved, unregistered purchase-proof serials.

    This endpoint is a UI convenience, not an authorization shortcut. The register
    route continues to re-check the accepted proof and duplicate-device constraint.
    No document path, file hash, reviewer note, or other user's data is returned.
    """
    from routes.evidence_routes import normalize_serial

    candidate_serials: dict[str, str] = {}
    legacy_docs = db.query(KYCDocument).filter(
        KYCDocument.user_id == current_user.id,
        KYCDocument.doc_type == DocType.PURCHASE_PROOF,
        KYCDocument.status == DocStatus.ACCEPTED,
        KYCDocument.serial_for_device.isnot(None),
    ).all()
    for doc in legacy_docs:
        serial = normalize_serial(doc.serial_for_device)
        if serial:
            candidate_serials.setdefault(serial, "APPROVED_PURCHASE_PROOF")

    evidence_rows = db.query(EvidenceRecord).filter(
        EvidenceRecord.user_id == current_user.id,
        EvidenceRecord.evidence_type == EvidenceType.PURCHASE_PROOF,
        EvidenceRecord.subject_type == EvidenceSubjectType.DEVICE_CLAIM,
        EvidenceRecord.verification_status == EvidenceVerificationStatus.VERIFIED,
    ).all()
    for evidence in evidence_rows:
        serial = normalize_serial(evidence.subject_id)
        if serial:
            candidate_serials.setdefault(serial, "APPROVED_PURCHASE_PROOF")

    options = []
    for serial, source in sorted(candidate_serials.items()):
        serial_hash = generate_sha256(serial)
        registered = db.query(Device.id).filter(
            (Device.serial_hash == serial_hash) | (Device.serial_raw == serial)
        ).first()
        if not registered:
            options.append({"serial_for_device": serial, "source": source})
    return standard_response({"total": len(options), "options": options})


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
        # Legacy test rows retain their historical off-chain data but their
        # synthetic Phase 2 public_id was never registered on-chain.
        if registration_status == RegistrationStatus.LEGACY_TEST.value:
            display_status = "LEGACY_TEST"
        elif registration_status == RegistrationStatus.CONFIRMED.value:
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
                "identity_version": _enum_value(device.identity_version),
                "is_legacy_test": (
                    _enum_value(device.identity_version) == IdentityVersion.V1_LEGACY.value
                    or registration_status == RegistrationStatus.LEGACY_TEST.value
                ),
                "read_only": (
                    _enum_value(device.identity_version) != IdentityVersion.V2_PUBLIC_ID.value
                    or registration_status != RegistrationStatus.CONFIRMED.value
                ),
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


# Positive allowlist for the device-registration form. A denylist can miss a
# private value placed under an unexpected custom key in legacy configuration.
_PUBLIC_CONFIG_FIELDS = ("processor", "ram", "storage", "condition", "display", "gpu")


_PUBLIC_LIFECYCLE_EVENTS = {
    "REGISTERED",
    "VERIFIED",
    "TRANSFERRED",
    "HARDWARE_CHANGED",
    "RECYCLED",
    "EXPORTED",
}


def _public_config_value(value: Any) -> Dict[str, Any]:
    """Expose only known hardware fields; never recursively publish arbitrary JSON."""
    if not isinstance(value, dict):
        return {}

    normalized = {str(key).strip().lower(): item for key, item in value.items()}
    public: Dict[str, Any] = {}
    for key in _PUBLIC_CONFIG_FIELDS:
        item = normalized.get(key)
        if isinstance(item, str):
            # Keep display values bounded and prevent unusual objects from being serialized.
            item = item.strip()[:160]
            if item:
                public[key] = item
        elif item is None or isinstance(item, (int, float, bool)):
            if item is not None:
                public[key] = item
    return public


def _iso_datetime(value: Optional[datetime]) -> Optional[str]:
    normalized = make_aware(value)
    return normalized.isoformat() if normalized else None


@router.get("/public/{device_id}")
def get_public_passport(
    device_id: str,
    db: Session = Depends(get_db),
):
    """Return a public-safe passport summary without private dispute data.

    A synthetic public_id assigned to a V1 legacy record is only an off-chain
    lookup/display key. V1 chain state is resolved using legacy_chain_id stored
    on the database row; this value is never returned to the public caller.
    """
    if not is_public_id(device_id):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passport not found.")

    device = db.query(Device).filter(Device.public_id == device_id).first()
    if device is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passport not found.")

    identity_version = _enum_value(device.identity_version)
    registration_status = _enum_value(device.registration_status)
    is_legacy = (
        identity_version == IdentityVersion.V1_LEGACY.value
        or registration_status == RegistrationStatus.LEGACY_TEST.value
    )
    if not is_legacy and (
        identity_version != IdentityVersion.V2_PUBLIC_ID.value
        or registration_status != RegistrationStatus.CONFIRMED.value
    ):
        # Pending/failed/reconciliation-only records are not public passports.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Passport not found.")

    # Public site policy: never return dispute type, dispute status, comments,
    # resolution notes, raw event metadata, owner IDs/hashes, or internal UUIDs.
    mirrored_events = (
        db.query(LedgerEvent)
        .filter(
            LedgerEvent.device_id == device.id,
            LedgerEvent.chain_status == ChainStatus.CONFIRMED,
        )
        .order_by(LedgerEvent.timestamp.desc())
        .all()
    )
    visible_events = [
        event for event in mirrored_events
        if str(_enum_value(event.event_type)).upper() in _PUBLIC_LIFECYCLE_EVENTS
    ]

    chain_state = None
    chain_status = "LEGACY_CHAIN_STATE_UNAVAILABLE" if is_legacy else "NOT_SUBMITTED"
    if is_legacy:
        if device.legacy_chain_id:
            try:
                chain_state = ledger_service.get_legacy_device_state(device.legacy_chain_id)
                chain_status = chain_state.get("state_label", "UNKNOWN")
            except Exception as exc:
                logger.info(
                    "Legacy chain state unavailable (device_row_id_hash=%s, exception_type=%s)",
                    hashlib.sha256(device.id.encode("utf-8")).hexdigest()[:12],
                    type(exc).__name__,
                )
                chain_status = "LEGACY_CHAIN_STATE_UNAVAILABLE"
    elif registration_status in (
        RegistrationStatus.CONFIRMED.value,
        RegistrationStatus.RECONCILIATION_REQUIRED.value,
    ):
        try:
            chain_state = ledger_service.get_device_state(device.public_id)
            chain_status = chain_state.get("state_label", "UNKNOWN")
        except Exception as exc:
            logger.info(
                "Public passport chain state unavailable (exception_type=%s)", type(exc).__name__
            )
            chain_status = "UNREACHABLE"
    elif registration_status == RegistrationStatus.PENDING.value:
        chain_status = "PENDING_REGISTRATION"
    elif registration_status == RegistrationStatus.FAILED.value:
        chain_status = "NOT_SUBMITTED"

    db_status = _enum_value(device.status)
    dispute_state_hidden = (
        str(db_status).upper() == DeviceStatus.DISPUTED.value
        or str(chain_status).upper() == DeviceStatus.DISPUTED.value
    )
    # If either source says DISPUTED, omit both state labels rather than expose
    # or indirectly identify the private dispute state through a special label.
    # Preserve the actual historical lifecycle state for V1 passports; the
    # separate identity_version/registration_status fields carry the legacy label.
    # Only a disputed state itself is omitted from the public view.
    public_status = None if dispute_state_hidden else db_status
    public_chain_status = None if dispute_state_hidden else chain_status
    stamp_valid = (
        bool(chain_state.get("stamp_valid")) if chain_state is not None
        else bool(device.stamp_valid)
    )

    event_payload = []
    history_payload = []
    for event in visible_events:
        timestamp = _iso_datetime(event.confirmed_at or event.timestamp)
        row = {
            "event_type": str(_enum_value(event.event_type)),
            "tx_hash": event.tx_hash,
            "block_number": event.block_number,
            "timestamp": timestamp,
        }
        event_payload.append(row)
        history_payload.append({
            "event_type": row["event_type"],
            "status": row["event_type"],
            "timestamp": timestamp,
        })

    return standard_response({
        "device_id": device.public_id,
        "public_id": device.public_id,
        "identity_version": identity_version,
        "is_legacy_test": is_legacy,
        "read_only": is_legacy or registration_status != RegistrationStatus.CONFIRMED.value,
        "brand": device.brand_name,
        "brand_name": device.brand_name,
        "status": public_status,
        "registration_status": registration_status,
        "chain_status": public_chain_status,
        "stamp_valid": stamp_valid,
        "registered_at": _iso_datetime(device.registered_at),
        "last_verified_at": _iso_datetime(device.last_verified_at),
        "current_config": _public_config_value(device.current_config or {}),
        "history": history_payload,
        "transfers": history_payload,
        "events": event_payload,
        "total_events": len(event_payload),
        "message": (
            "V1 Legacy Test Passport — read-only. This synthetic passport ID is an off-chain display ID; "
            "historical chain state and mirrored lifecycle events are shown only where available."
            if is_legacy else None
        ),
    })


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
    identity_version = _enum_value(db_device.identity_version)
    is_legacy_test = (
        identity_version == IdentityVersion.V1_LEGACY.value
        or registration_status == RegistrationStatus.LEGACY_TEST.value
    )
    # Legacy identity is authoritative even if a partially migrated row has a
    # mismatched registration_status. Never query chain state with its synthetic
    # public_id. Pending/failed V2 rows also must not be represented as registered.
    if is_legacy_test or registration_status in (
        RegistrationStatus.PENDING.value,
        RegistrationStatus.FAILED.value,
    ):
        chain_status = (
            "LEGACY_CHAIN_STATE_UNAVAILABLE" if is_legacy_test else
            "NOT_SUBMITTED" if registration_status == RegistrationStatus.FAILED.value else
            "NOT_CHECKED"
        )
        chain_stamp_valid = db_device.stamp_valid
        if is_legacy_test and db_device.legacy_chain_id:
            try:
                legacy_state = ledger_service.get_legacy_device_state(db_device.legacy_chain_id)
                chain_status = legacy_state.get("state_label", "UNKNOWN")
                chain_stamp_valid = legacy_state.get("stamp_valid", db_device.stamp_valid)
            except Exception as exc:
                logger.info(
                    "Legacy chain state unavailable (exception_type=%s)",
                    type(exc).__name__,
                )
                chain_status = "LEGACY_CHAIN_STATE_UNAVAILABLE"

        return standard_response(
            {
                "device_id": db_device.public_id,
                "public_id": db_device.public_id,
                "identity_version": identity_version,
                "brand": db_device.brand_name,
                "status": _enum_value(db_device.status),
                "db_status": _enum_value(db_device.status),
                "serial_raw": db_device.serial_raw,
                "registration_status": registration_status,
                "chain_status": chain_status,
                "stamp_valid": chain_stamp_valid,
                "is_legacy_test": is_legacy_test,
                "read_only": is_legacy_test or registration_status != RegistrationStatus.CONFIRMED.value,
                "message": (
                    "V1 Legacy Test Passport: historical record is preserved for read-only viewing; "
                    "the synthetic public ID was not registered on-chain."
                    if is_legacy_test else None
                ),
                "current_config": db_device.current_config,
                "registered_at": make_aware(db_device.registered_at).isoformat() if db_device.registered_at else None,
                "registration_tx_id": None if is_legacy_test else db_device.registration_tx_id,
                "registration_confirmed_round": None if is_legacy_test else db_device.registration_confirmed_round,
                "legacy_registration_tx_id": db_device.registration_tx_id if is_legacy_test else None,
                "legacy_registration_confirmed_round": (
                    db_device.registration_confirmed_round if is_legacy_test else None
                ),
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
