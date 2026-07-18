"""Reason payloads are per-score maps, not opaque prose."""

import pytest

from quantinue.db.reason import ReasonPayload, reason_payload


def test_reason_payload_keys_are_score_columns() -> None:
    payload = reason_payload(sentiment_score="긍정 실적", importance="1차 촉매")

    assert payload == {"sentiment_score": "긍정 실적", "importance": "1차 촉매"}


def test_reason_payload_rejects_unknown_score_key() -> None:
    with pytest.raises(ValueError, match="unknown score"):
        _ = reason_payload(made_up_score="x")


def test_reason_payload_allows_empty() -> None:
    assert reason_payload() == {}


def test_reason_payload_model_validates_values_are_text() -> None:
    model = ReasonPayload.model_validate({"risk_score": "규제 리스크"})

    assert model.root["risk_score"] == "규제 리스크"
