from types import SimpleNamespace

from app.services.gemini_explainer import _build_prompt, _redact_receipt_refs


def test_redacts_receipt_hash_form():
    assert (
        _redact_receipt_refs("identical to receipt #7 (similarity distance: 0/64)")
        == "identical to another receipt (similarity distance: 0/64)"
    )


def test_redacts_case_insensitive():
    assert _redact_receipt_refs("Same as Receipt #12, submitted") == "Same as another receipt, submitted"


def test_redacts_parenthesised_form():
    out = _redact_receipt_refs("similar receipt (#34) from 'Cafe'")
    assert "#" not in out and "34" not in out
    assert "from 'Cafe'" in out


def test_redacts_bare_hash():
    out = _redact_receipt_refs("matches #99 closely")
    assert "99" not in out and "#" not in out


def test_leaves_other_text_untouched():
    text = "Amount $75.00 on 2026-01-05 exceeds limit of $50.00"
    assert _redact_receipt_refs(text) == text
    assert _redact_receipt_refs("") == ""


def _flag(desc, ftype="duplicate"):
    return SimpleNamespace(severity="high", flag_type=ftype, description=desc)


def test_build_prompt_strips_receipt_numbers():
    receipt = SimpleNamespace(
        merchant="Acme Diner", amount=75, transaction_date="2026-01-05",
        fraud_score=82, risk_level="high",
    )
    flags = [
        _flag("Receipt image is nearly identical to receipt #7 (similarity distance: 0/64)"),
        _flag("Same merchant, date and total as receipt #12, submitted by another employee"),
        _flag("similar receipt (#34) from 'Acme Diner'"),
    ]
    prompt = _build_prompt(receipt, flags)
    for needle in ("#7", "#12", "#34", "(#", "#"):
        assert needle not in prompt
    assert "nearly identical to another receipt" in prompt
    assert "submitted by another employee" in prompt
    assert "from 'Acme Diner'" in prompt
    assert "Acme Diner" in prompt and "$75.00" in prompt and "82/100" in prompt
    assert "receipt numbers" in prompt
