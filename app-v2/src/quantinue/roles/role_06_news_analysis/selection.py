"""Deterministic ticker-aware selection over fetched RSS news."""

import re
from dataclasses import dataclass, replace
from hashlib import sha256
from typing import Final
from urllib.parse import urlsplit, urlunsplit

from quantinue.market_data.models import (
    NewsItem,
    NewsMatchReason,
    NewsMatchStatus,
    TickerNewsQuery,
)

TICKER_TITLE_SCORE: Final = 50
TICKER_SNIPPET_SCORE: Final = 25
COMPANY_TITLE_SCORE: Final = 40
COMPANY_SNIPPET_SCORE: Final = 20
MINIMUM_RELEVANCE_SCORE: Final = 20
_COMPANY_SUFFIXES: Final = frozenset(
    {"corp", "corporation", "inc", "incorporated", "ltd", "limited", "plc", "company", "co"}
)


@dataclass(frozen=True, slots=True)
class SelectedNewsItem:
    """One fetched item with its typed selection result."""

    item: NewsItem
    status: NewsMatchStatus
    score: int
    reasons: tuple[NewsMatchReason, ...]
    canonical_identity: str


@dataclass(frozen=True, slots=True)
class NewsSelectionResult:
    """Every fetched item plus an optional deterministic representative."""

    fetched: tuple[NewsItem, ...]
    items: tuple[SelectedNewsItem, ...]
    selected: SelectedNewsItem | None
    fetched_count: int
    relevant_count: int
    excluded_count: int


def select_ticker_news(items: tuple[NewsItem, ...], query: TickerNewsQuery) -> NewsSelectionResult:
    """Select one representative while preserving every fetched item."""
    scored = tuple(_score_item(item, query) for item in items)
    winner_by_identity: dict[str, int] = {}
    for index, item in enumerate(scored):
        prior_index = winner_by_identity.get(item.canonical_identity)
        if prior_index is None or _selection_key(item, index) < _selection_key(
            scored[prior_index], prior_index
        ):
            winner_by_identity[item.canonical_identity] = index

    unique_relevant = tuple(
        index
        for index, item in enumerate(scored)
        if winner_by_identity[item.canonical_identity] == index
        and item.score >= MINIMUM_RELEVANCE_SCORE
    )
    selected_index = (
        min(unique_relevant, key=lambda index: _selection_key(scored[index], index))
        if unique_relevant
        else None
    )
    evaluated = tuple(
        _evaluate_item(item, index, winner_by_identity, selected_index)
        for index, item in enumerate(scored)
    )
    selected = evaluated[selected_index] if selected_index is not None else None
    relevant_count = sum(
        item.status in {NewsMatchStatus.RELEVANT, NewsMatchStatus.SELECTED} for item in evaluated
    )
    return NewsSelectionResult(
        fetched=items,
        items=evaluated,
        selected=selected,
        fetched_count=len(items),
        relevant_count=relevant_count,
        excluded_count=len(items) - relevant_count,
    )


def _score_item(item: NewsItem, query: TickerNewsQuery) -> SelectedNewsItem:
    ticker = _normalized_words(query.ticker)
    company = _normalized_company_name(query.company_name)
    title = _normalized_words(item.title)
    snippet = _normalized_words(item.snippet)
    matches = (
        (NewsMatchReason.TICKER_TITLE, TICKER_TITLE_SCORE, _contains_phrase(title, ticker)),
        (
            NewsMatchReason.TICKER_SNIPPET,
            TICKER_SNIPPET_SCORE,
            _contains_phrase(snippet, ticker),
        ),
        (
            NewsMatchReason.COMPANY_TITLE,
            COMPANY_TITLE_SCORE,
            _contains_phrase(title, company),
        ),
        (
            NewsMatchReason.COMPANY_SNIPPET,
            COMPANY_SNIPPET_SCORE,
            _contains_phrase(snippet, company),
        ),
    )
    reasons = tuple(reason for reason, _, matched in matches if matched)
    return SelectedNewsItem(
        item=item,
        status=NewsMatchStatus.FETCHED,
        score=sum(score for _, score, matched in matches if matched),
        reasons=reasons,
        canonical_identity=_canonical_identity(item),
    )


def _evaluate_item(
    item: SelectedNewsItem,
    index: int,
    winner_by_identity: dict[str, int],
    selected_index: int | None,
) -> SelectedNewsItem:
    if winner_by_identity[item.canonical_identity] != index:
        return replace(item, status=NewsMatchStatus.EXCLUDED, reasons=(NewsMatchReason.DUPLICATE,))
    if item.score < MINIMUM_RELEVANCE_SCORE:
        return replace(
            item,
            status=NewsMatchStatus.EXCLUDED,
            reasons=(NewsMatchReason.BELOW_MINIMUM_SCORE,),
        )
    if index == selected_index:
        return replace(item, status=NewsMatchStatus.SELECTED)
    return replace(item, status=NewsMatchStatus.RELEVANT)


def _selection_key(item: SelectedNewsItem, index: int) -> tuple[int, float, str, int]:
    return (
        -item.score,
        -item.item.published_at.timestamp(),
        _canonical_url(item.item.url),
        index,
    )


def _normalized_company_name(value: str) -> str:
    words = _normalized_words(value).split()
    while words and words[-1] in _COMPANY_SUFFIXES:
        _ = words.pop()
    return " ".join(words)


def _normalized_words(value: str) -> str:
    return " ".join(re.findall(r"[a-z0-9]+", value.casefold()))


def _contains_phrase(text: str, phrase: str) -> bool:
    return bool(phrase) and re.search(rf"(?:^| )({re.escape(phrase)})(?:$| )", text) is not None


def _canonical_identity(item: NewsItem) -> str:
    if item.guid:
        return f"guid:{item.guid.strip()}"
    return f"url:{_canonical_url(item.url)}"


def _canonical_url(value: str) -> str:
    try:
        parsed = urlsplit(value)
        if parsed.scheme.casefold() not in {"http", "https"} or parsed.hostname is None:
            return _invalid_url_identity(value)
        port = parsed.port
    except ValueError:
        return _invalid_url_identity(value)
    host = (parsed.hostname or "").casefold()
    default_port = (parsed.scheme.casefold(), port) in {("http", 80), ("https", 443)}
    authority = host if port is None or default_port else f"{host}:{port}"
    path = parsed.path.rstrip("/") or "/"
    return urlunsplit((parsed.scheme.casefold(), authority, path, "", ""))


def _invalid_url_identity(value: str) -> str:
    return f"invalid-{sha256(value.encode()).hexdigest()}"
