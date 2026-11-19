from types import SimpleNamespace

from app.services.gemini_explainer import _build_prompt, _redact_receipt_refs


def test_real_duplicate_detector_template():
    assert (
        _redact_receipt_refs(
            "Receipt image is nearly identical to receipt #7 (similarity distance: 0/64)."
        )
        == "Receipt image is nearly identical to another receipt (similarity distance: 0/64)."
    )


def test_real_short_window_template():
    assert (
        _redact_receipt_refs(
            "Employee submitted a similar receipt (#34) from 'Cafe' within 24 hours "
            "(rule: 'Short window')."
        )
        == "Employee submitted a similar receipt from 'Cafe' within 24 hours "
        "(rule: 'Short window')."
    )


def test_same_merchant_example():
    assert (
        _redact_receipt_refs(
            "Same merchant, date and total as receipt #12, submitted by another employee"
        )
        == "Same merchant, date and total as another receipt, submitted by another employee"
    )


def test_case_and_spacing_variants():
    assert _redact_receipt_refs("like RECEIPT #5") == "like another receipt"
    assert _redact_receipt_refs("like receipt# 5 ok") == "like another receipt ok"
    assert _redact_receipt_refs("like receipt  #  15") == "like another receipt"
    assert _redact_receipt_refs("a Receipt ( # 8 ) from X") == "a receipt from X"


def test_leaves_other_text_untouched():
    for text in (
        "Amount $75.00 on 2026-01-05 exceeds limit of $50.00",
        "item #2 of 5",
        "written in C# today",
        "",
    ):
        assert _redact_receipt_refs(text) == text


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
    assert "#" not in prompt
    for digits in ("7", "12", "34"):
        assert f"receipt {digits}" not in prompt and f"({digits})" not in prompt
    assert "nearly identical to another receipt" in prompt
    assert "similar receipt from 'Acme Diner'" in prompt
    assert "submitted by another employee" in prompt
    assert "Acme Diner" in prompt and "$75.00" in prompt and "82/100" in prompt
    assert "receipt numbers" in prompt
