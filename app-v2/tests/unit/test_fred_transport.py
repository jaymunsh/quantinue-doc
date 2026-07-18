# pyright: reportPrivateUsage=false

from email.message import Message
from io import BytesIO
from urllib.error import HTTPError
from urllib.request import Request

import pytest

from quantinue.core.errors import HttpFailureError, TransientFailureError, ValidationFailureError
from quantinue.market_data import http_source


class _Response:
    def __init__(self, *, status: int = 200, payload: bytes = b"ok") -> None:
        self.status = status
        self.payload = payload
        self.closed = False

    def read(self) -> bytes:
        return self.payload

    def close(self) -> None:
        self.closed = True


class _UnexpectedResponse:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def test_fred_success_closes_response(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _Response(payload=b"observation_date,DFF\n2026-07-10,4.25\n")

    def open_response(_request: Request, **_kwargs: float) -> _Response:
        return response

    monkeypatch.setattr(http_source, "urlopen", open_response)
    payload = http_source._download_fred_csv(
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF"
    )
    assert payload == response.payload
    assert response.closed is True


@pytest.mark.parametrize("status_code", [400, 429, 503])
def test_fred_http_error_maps_status_and_closes_body(
    monkeypatch: pytest.MonkeyPatch,
    status_code: int,
) -> None:
    body = BytesIO(b"provider failure")
    error = HTTPError(
        "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF",
        status_code,
        "provider failure",
        Message(),
        body,
    )

    def raise_http_error(_request: Request, **_kwargs: float) -> _Response:
        raise error

    monkeypatch.setattr(http_source, "urlopen", raise_http_error)
    with pytest.raises(HttpFailureError) as captured:
        _ = http_source._download_fred_csv("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF")
    assert captured.value.status_code == status_code
    assert body.closed is True


def test_fred_explicit_failure_response_closes_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _Response(status=503)

    def open_response(_request: Request, **_kwargs: float) -> _Response:
        return response

    monkeypatch.setattr(http_source, "urlopen", open_response)
    with pytest.raises(HttpFailureError) as captured:
        _ = http_source._download_fred_csv("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF")
    assert captured.value.status_code == 503
    assert response.closed is True


def test_fred_unexpected_response_closes_resource(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = _UnexpectedResponse()

    def open_response(_request: Request, **_kwargs: float) -> _UnexpectedResponse:
        return response

    monkeypatch.setattr(http_source, "urlopen", open_response)
    with pytest.raises(ValidationFailureError):
        _ = http_source._download_fred_csv("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF")
    assert response.closed is True


@pytest.mark.parametrize("transport_error", [TimeoutError(), OSError()])
def test_fred_transport_error_remains_transient(
    monkeypatch: pytest.MonkeyPatch,
    transport_error: TimeoutError | OSError,
) -> None:
    def raise_transport_error(_request: Request, **_kwargs: float) -> _Response:
        raise transport_error

    monkeypatch.setattr(http_source, "urlopen", raise_transport_error)
    with pytest.raises(TransientFailureError):
        _ = http_source._download_fred_csv("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF")


def test_fred_request_preserves_identity_and_bounded_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def open_response(request: Request, *, timeout: float) -> _Response:
        assert request.get_header("User-agent") == "quantinue/0.1"
        assert timeout == http_source.FRED_TIMEOUT_SECONDS
        return _Response()

    monkeypatch.setattr(http_source, "urlopen", open_response)
    _ = http_source._download_fred_csv("https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF")


@pytest.mark.parametrize(
    "url",
    [
        "http://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF",
        "https://example.com/graph/fredgraph.csv?id=DFF",
    ],
)
def test_fred_rejects_urls_outside_https_allowlist(
    monkeypatch: pytest.MonkeyPatch,
    url: str,
) -> None:
    opened = False

    def open_response(_request: Request, **_kwargs: float) -> _Response:
        nonlocal opened
        opened = True
        return _Response()

    monkeypatch.setattr(http_source, "urlopen", open_response)
    with pytest.raises(ValidationFailureError):
        _ = http_source._download_fred_csv(url)
    assert opened is False
