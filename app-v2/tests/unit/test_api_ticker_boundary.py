"""Strict API ticker trust-boundary tests."""

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from quantinue.api.schemas import RunCreate
from quantinue.main import create_app


@pytest.mark.parametrize(
    "ticker",
    [
        "nvda",
        "NV DA",
        "NVDA\x00",
        "../NVDA",
        "<B>",
        "삼성",
        "A_B",
        "A/B",
        "A" * 13,
    ],
)
def test_run_create_rejects_noncanonical_ticker_after_strip(ticker: str) -> None:
    with pytest.raises(ValidationError):
        _ = RunCreate(ticker=ticker)


@pytest.mark.parametrize("ticker", ["NVDA", "BRK.B", "BF-B", " A1 "])
def test_run_create_accepts_uppercase_ascii_ticker_after_strip(ticker: str) -> None:
    request = RunCreate(ticker=ticker)

    assert request.ticker == ticker.strip()


@pytest.mark.parametrize("ticker", ["../NVDA", "NVDA\x00", "<B>", "삼성"])
def test_memory_api_returns_422_before_pipeline_for_untrusted_ticker(ticker: str) -> None:
    with TestClient(create_app()) as client:
        response = client.post("/api/runs", json={"ticker": ticker})

    assert response.status_code == 422
