"""Canonical device identity helpers shared by TraceLoop API and ledger code.

Identity contract:
- Device.id is a private internal database UUID.
- Device.public_id is ``TL-{BRAND_SLUG}-{24 uppercase hex characters}``.
- The random suffix is generated independently of the hardware serial.
- Raw serials and serial fingerprints must never be passed as public IDs.

The brand prefix is a user-readable label, not proof of OEM authenticity.
"""

from __future__ import annotations

import re
import secrets
import uuid
from dataclasses import dataclass
from typing import Any, Dict


@dataclass(frozen=True)
class BrandIdentity:
    """Canonical backend-controlled identity for a supported manufacturer."""

    code: str
    slug: str
    name: str


# Keep the current frontend's short brand codes and accepted Dell alias working.
# All aliases map to one canonical code, public slug, and display name.
_BRAND_ALIASES: Dict[str, BrandIdentity] = {
    "DL": BrandIdentity(code="DL", slug="DELL", name="Dell"),
    "DELL": BrandIdentity(code="DL", slug="DELL", name="Dell"),
    "HP": BrandIdentity(code="HP", slug="HP", name="HP"),
    "LN": BrandIdentity(code="LN", slug="LENOVO", name="Lenovo"),
    "AS": BrandIdentity(code="AS", slug="ASUS", name="ASUS"),
    "AC": BrandIdentity(code="AC", slug="ACER", name="Acer"),
    "AP": BrandIdentity(code="AP", slug="APPLE", name="Apple"),
    "MS": BrandIdentity(code="MS", slug="MSI", name="MSI"),
    "SG": BrandIdentity(code="SG", slug="SAMSUNG", name="Samsung"),
}

_SUPPORTED_SLUGS = tuple(
    sorted({brand.slug for brand in _BRAND_ALIASES.values()})
)
_PUBLIC_ID_PATTERN = re.compile(
    rf"^TL-(?:{'|'.join(map(re.escape, _SUPPORTED_SLUGS))})-[0-9A-F]{{24}}$"
)
PUBLIC_ID_SUFFIX_HEX_LENGTH = 24
PUBLIC_ID_MAX_LENGTH = 40


def resolve_brand(brand_code: str) -> BrandIdentity:
    """Resolve a submitted code/alias to canonical backend-controlled brand data.

    Raises:
        ValueError: when the code is missing or not in the allowlist.
    """
    if not isinstance(brand_code, str):
        raise ValueError("Unsupported brand code.")

    normalized = brand_code.strip().upper()
    brand = _BRAND_ALIASES.get(normalized)
    if brand is None:
        raise ValueError("Unsupported brand code.")
    return brand


def generate_internal_id() -> str:
    """Generate the private primary key used by Device and related foreign keys."""
    return str(uuid.uuid4())


def generate_public_id(brand_slug: str) -> str:
    """Generate a serial-independent, brand-labelled public Passport ID.

    The suffix contains 96 random bits (12 cryptographically random bytes). The
    returned value is validated before use so generator and validator cannot
    silently drift apart.
    """
    if not isinstance(brand_slug, str):
        raise ValueError("Unsupported brand slug.")

    slug = brand_slug.strip().upper()
    if slug not in _SUPPORTED_SLUGS:
        raise ValueError("Unsupported brand slug.")

    public_id = f"TL-{slug}-{secrets.token_hex(12).upper()}"
    if not is_public_id(public_id):  # defensive invariant; should never fail
        raise RuntimeError("Generated Passport ID did not match the canonical format.")
    if len(public_id) > PUBLIC_ID_MAX_LENGTH:
        raise RuntimeError("Generated Passport ID exceeds the database field length.")
    return public_id


def is_public_id(value: Any) -> bool:
    """Return whether value has the canonical public Passport ID format.

    This is format validation only; it does not prove that the device or brand is
    authentic, registered, or authorized for the requester.
    """
    return isinstance(value, str) and _PUBLIC_ID_PATTERN.fullmatch(value) is not None


def validate_public_id(public_id: str) -> str:
    """Validate and return a public Passport ID, raising ValueError otherwise."""
    if not is_public_id(public_id):
        raise ValueError("Invalid public Passport ID format.")
    return public_id
