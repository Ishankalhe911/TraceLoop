from services.device_identity import (
    generate_internal_id,
    generate_public_id,
    is_public_id,
    resolve_brand,
    validate_public_id,
)


def test_generated_public_id_is_canonical_and_serial_independent():
    public_id = generate_public_id(resolve_brand("DL").slug)

    assert is_public_id(public_id)
    assert public_id.startswith("TL-DELL-")
    assert len(public_id.rsplit("-", 1)[1]) == 24


def test_supported_brand_aliases_use_canonical_slugs():
    assert generate_public_id(resolve_brand("LN").slug).startswith("TL-LENOVO-")
    assert generate_public_id(resolve_brand("DELL").slug).startswith("TL-DELL-")


def test_invalid_legacy_ids_are_not_public_ids():
    assert not is_public_id("TL-DL-REAL_OR_TEST_SERIAL")
    assert not is_public_id("8f4c2b91-2db2-43d1-91ad-000000000001")


def test_internal_id_is_uuid_and_distinct_from_public_id():
    internal_id = generate_internal_id()
    public_id = generate_public_id("HP")

    assert len(internal_id) == 36
    assert internal_id != public_id


def test_validate_public_id_rejects_legacy_format():
    try:
        validate_public_id("TL-DL-SERIAL123")
    except ValueError:
        pass
    else:
        raise AssertionError("Legacy ID must not be accepted as a Phase 2 public ID")
