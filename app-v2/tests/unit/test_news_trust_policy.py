"""Source grading decides which outlets may influence a decision at all."""

from pathlib import Path

import pytest

from quantinue.core.news_trust import NewsTrustPolicy, load_news_trust_policy
from quantinue.orchestration.policy import GatesConfig

POLICY = load_news_trust_policy(Path("config/news_trust_policy.yaml"))


def test_known_wire_services_are_allowed() -> None:
    assert POLICY.grade_for("https://www.reuters.com/markets/nvda-story") == "allow"
    assert POLICY.grade_for("https://apnews.com/article/x") == "allow"


def test_aggregators_are_gray() -> None:
    assert POLICY.grade_for("https://seekingalpha.com/article/1") == "gray"


def test_social_and_rumour_sites_are_blocked() -> None:
    assert POLICY.grade_for("https://www.reddit.com/r/wallstreetbets/x") == "block"
    assert POLICY.grade_for("https://stocktwits.com/symbol/NVDA") == "block"


def test_unknown_domain_falls_back_to_the_default_grade() -> None:
    # 모르는 매체를 신뢰하지 않는 것이 기본값이어야 한다.
    assert POLICY.grade_for("https://some-random-blog.example/post") == "gray"


def test_subdomains_inherit_the_registered_domain_grade() -> None:
    assert POLICY.grade_for("https://feeds.reuters.com/x") == "allow"
    assert POLICY.grade_for("https://old.reddit.com/r/x") == "block"


def test_trust_score_tracks_the_grade() -> None:
    assert POLICY.trust_for("https://www.reuters.com/x") == 0.95
    assert POLICY.trust_for("https://seekingalpha.com/x") == 0.50
    assert POLICY.trust_for("https://reddit.com/x") == 0.0


def test_blocked_sources_are_droppable_before_any_model_call() -> None:
    assert POLICY.is_blocked("https://reddit.com/x") is True
    assert POLICY.is_blocked("https://reuters.com/x") is False


def test_allow_trust_clears_the_strategist_vote_floor() -> None:
    floor = GatesConfig().source_trust_min

    assert POLICY.trust_for("https://reuters.com/x") >= floor
    # gray는 투표 문턱을 넘지 못한다 — 07에서 투표권을 잃는다.
    assert POLICY.trust_for("https://seekingalpha.com/x") < floor


def test_malformed_url_is_treated_as_unknown() -> None:
    assert POLICY.grade_for("not-a-url") == "gray"
    assert POLICY.grade_for("") == "gray"


def test_policy_rejects_an_unknown_grade_name() -> None:
    with pytest.raises(ValueError, match="grade"):
        _ = NewsTrustPolicy.model_validate(
            {"version": 1, "default_grade": "sometimes", "trust_scores": {}, "domains": {}}
        )
