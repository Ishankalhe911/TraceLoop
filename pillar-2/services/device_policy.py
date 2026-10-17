"""Shared lifecycle policy for TraceLoop device identities."""

from fastapi import HTTPException, status

from database import Device, IdentityVersion, RegistrationStatus


def _enum_value(value):
    return value.value if hasattr(value, "value") else value


def is_confirmed_v2_device(device: Device) -> bool:
    """True only for an active Phase 2 identity with confirmed chain registration."""
    identity_version = _enum_value(getattr(device, "identity_version", None))
    registration_status = _enum_value(getattr(device, "registration_status", None))
    return (
        identity_version == IdentityVersion.V2_PUBLIC_ID.value
        and registration_status == RegistrationStatus.CONFIRMED.value
    )


def require_confirmed_v2_device(device: Device, action: str) -> None:
    """Reject writes unless the device has a confirmed V2 public identity."""
    if is_confirmed_v2_device(device):
        return

    identity_version = _enum_value(getattr(device, "identity_version", None))
    registration_status = _enum_value(getattr(device, "registration_status", None))
    if (
        identity_version == IdentityVersion.V1_LEGACY.value
        or registration_status == RegistrationStatus.LEGACY_TEST.value
    ):
        detail = (
            f"V1 legacy test passports are read-only; {action} is disabled. "
            "Their historical records remain available for viewing."
        )
    else:
        detail = (
            f"Device does not have a confirmed V2 public-ID registration; "
            f"{action} is blocked."
        )
    raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=detail)
