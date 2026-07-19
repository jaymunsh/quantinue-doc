"""tb_strategist_signals.inv_type must name the persona that actually decided.

이 축은 장식이 아니라 원장의 유일성 축이다 — `UNIQUE (ticker, cycle_ts, inv_type)`.
지금까지는 기록 시점에 리터럴 "conservative"가 박혀 있어서, 실제로 aggressive
프로필로 판단하고도 원장에는 전부 conservative로 찍혔다. 성향 2종 팬아웃이
붙는 순간 두 페르소나가 같은 행을 두고 다투게 된다.
"""

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from quantinue.broker.mock import MockBroker
from quantinue.core.contracts import (
    PipelineContext,
    PipelineRequest,
    RoleEvidenceTrace,
    RunId,
)
from quantinue.db.domain_records import AccountWrite, CriticVerdictWrite, StrategistSignalWrite
from quantinue.db.postgres_lifecycle import persist_domain_stage
from quantinue.llm.provider import DeterministicAnalyzer
from quantinue.orchestration.factory import DEFAULT_PROFILE_NAME, build_roles
from quantinue.orchestration.policy import load_pipeline_policy
from quantinue.roles.role_07_strategist.service import Strategist


class RecordingDomain:
    """Duck-typed domain repository capturing the stage-08 signal write."""

    def __init__(self) -> None:
        self.signal: StrategistSignalWrite | None = None

    async def save_signal(self, value: StrategistSignalWrite) -> int:
        self.signal = value
        return 701

    async def save_account(self, value: AccountWrite) -> int:
        del value
        return 41

    async def save_verdict(self, value: CriticVerdictWrite) -> int:
        del value
        return 1


def _upstream_trace(run_id: RunId, cycle_ts: datetime) -> tuple[RoleEvidenceTrace, ...]:
    """Roles 01~06 provenance — role 07 cites entries 1, 4 and 5 as its parents."""
    return tuple(
        RoleEvidenceTrace(
            run_id=run_id,
            evidence_id=f"{run_id}:{component}:upstream",
            component=component,
            source="fixture",
            source_ref="fixture://upstream",
            observed_at=cycle_ts,
            captured_at=cycle_ts,
            confidence=1.0,
        )
        for component in ("01", "02", "03", "04", "05", "06")
    )


@pytest.mark.anyio
async def test_the_strategist_stamps_the_profile_it_decided_under() -> None:
    # Given: a strategist composed for the conservative persona.
    strategist = Strategist(DeterministicAnalyzer(), profile_name="conservative")
    cycle_ts = datetime(2026, 7, 17, 13, 0, tzinfo=UTC)
    request = PipelineRequest(ticker="NVDA", cycle_ts=cycle_ts)
    run_id = RunId("run-inv-type")
    context = PipelineContext(
        request=request,
        run_id=run_id,
        evidence_trace=_upstream_trace(run_id, cycle_ts),
        technical_score=0.8,
        news_score=0.7,
        disclosure_score=0.7,
        is_daily_pick=True,
    )

    # When
    result = await strategist.execute(context)

    # Then: the persona travels with the decision instead of being re-guessed later.
    assert result.inv_type == "conservative"


@pytest.mark.anyio
async def test_stage08_records_the_running_profile_instead_of_a_literal() -> None:
    # Given: a decision produced under the aggressive persona.
    domain = RecordingDomain()
    context = PipelineContext(
        request=PipelineRequest(ticker="NVDA", cycle_ts=datetime(2026, 7, 17, 13, 0, tzinfo=UTC)),
        last_price=128.4,
        side="hold",
        conviction=0.5,
        inv_type="aggressive",
    )

    # When
    _ = await persist_domain_stage(
        domain,
        AccountWrite("test", Decimal(1000), Decimal(1000), Decimal(1000)),
        "08",
        context,
    )

    # Then
    assert domain.signal is not None
    assert domain.signal.inv_type == "aggressive"


def test_the_composed_strategist_never_relies_on_a_guessed_profile_name() -> None:
    # Given / When: the real composition path, not a hand-built role.
    roles = build_roles(
        DeterministicAnalyzer(),
        MockBroker(),
        policy=load_pipeline_policy(Path("config/pipeline.yaml")),
    )

    # Then: the name the factory chose is the name the signal will carry.
    strategist = roles[6]
    assert isinstance(strategist, Strategist)
    assert strategist.profile_name == DEFAULT_PROFILE_NAME
