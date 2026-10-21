from services.passport_explanation_service import PassportExplanationService


def test_model_text_validation_preserves_markdown_structure():
    markdown = (
        "### 📱 Device Passport Status\n"
        "The comparable current records match.\n\n"
        "### ⏱️ Lifecycle History\n"
        "- The device was registered (2026-10-17T11:00:00+00:00).\n"
        "- Ownership was transferred (2026-10-19T13:43:15+00:00).\n\n"
        "### ⚠️ System Notes\n"
        "- A ledger entry does not independently verify the physical event."
    )
    validated = PassportExplanationService._validate_model_text(markdown)
    assert validated is not None
    assert "\n\n### ⏱️ Lifecycle History" in validated
    assert "\n- The device was registered" in validated


def test_fallback_formats_only_supplied_facts():
    facts = {
        "summary": "The database and chain states agree for comparable fields.",
        "reconciliation": {"headline": "The comparable current records match."},
        "timeline": [
            {"event_type": "REGISTERED", "description": "The device was registered on the ledger.", "timestamp": "2026-10-17T11:00:00+00:00"},
            {"event_type": "VERIFIED", "description": "The device received a verification stamp.", "timestamp": "2026-10-19T12:43:31+00:00"},
        ],
        "limitations": ["The returned history covers only the scanned round window."],
    }
    result = PassportExplanationService._fallback(facts, "test")
    text = result["text"]
    assert result["generated"] is False
    assert "### Device passport status" in text
    assert "### Lifecycle history" in text
    assert "- The device was registered on the ledger." in text
    assert "### System notes" in text
    assert "The returned history covers only the scanned round window." in text
    assert "physically recycled" not in text.lower()
