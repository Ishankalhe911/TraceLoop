"""Public, read-only deterministic reconciliation for a TraceLoop passport."""
from __future__ import annotations
import logging
import time
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from database import Device, IdentityVersion, RegistrationStatus, get_db
from services.device_identity import is_public_id
from services.passport_facts_service import PassportFactsService
from services.passport_explanation_service import PassportExplanationService

router = APIRouter(prefix="/passport-intelligence", tags=["Passport Intelligence"])
logger = logging.getLogger(__name__)

def _enum_value(value):
    return value.value if hasattr(value, "value") else value


@router.get("/{device_id}")
def get_passport_intelligence(device_id: str, db: Session = Depends(get_db)):
    """Explain public lifecycle facts and compare sources without exposing private IDs.

    The path ID is used only to resolve a Device row. In particular, callers
    cannot supply a V1 chain key or internal database UUID to choose chain data.
    """
    if not is_public_id(device_id):
        raise HTTPException(status_code=404, detail="Passport not found.")

    device = db.query(Device).filter(Device.public_id == device_id).first()
    if device is None:
        raise HTTPException(status_code=404, detail="Passport not found.")

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
        # Match the public passport route: pending or failed V2 records are not public.
        raise HTTPException(status_code=404, detail="Passport not found.")

    facts = PassportFactsService(db).build(device)
    # The wording service is an implementation detail. Expose only the final
    # text to public clients, never provider/model/generation metadata. It cannot
    # alter the deterministic reconciliation status, checks, or lifecycle facts.
        # Diagnose Gemini generation versus deterministic fallback.
    explanation_started = time.monotonic()

    explanation_result = PassportExplanationService().explain(facts)

    explanation_elapsed_ms = round(
        (time.monotonic() - explanation_started) * 1000
    )

    if isinstance(explanation_result, dict):
        provider = explanation_result.get("provider", "unknown")
        generated = explanation_result.get("generated", False)
        model = explanation_result.get("model") or "-"
        fallback_reason = explanation_result.get("fallback_reason") or "-"
    else:
        provider = "invalid_result"
        generated = False
        model = "-"
        fallback_reason = "unexpected_result_type"

    logger.info(
        "PASSPORT_EXPLANATION provider=%s generated=%s "
        "model=%s fallback_reason=%s elapsed_ms=%s",
        provider,
        generated,
        model,
        fallback_reason,
        explanation_elapsed_ms,
    )
    explanation_text = (
        explanation_result.get("text")
        if isinstance(explanation_result, dict)
        else None
    )
    facts["explanation"] = {
        "text": explanation_text or facts.get("summary") or "The passport explanation is unavailable right now."
    }
    return {
        "status": "success",
        "code": 200,
        "data": facts,
        "error": None,
        "traceloop_version": "v1",
    }
