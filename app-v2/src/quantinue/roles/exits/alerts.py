"""Human-readable defence-line alerts shared by daily and intraday exits."""

from datetime import date
from typing import Final

from quantinue.roles.exits.contracts import ExitDecision

EXIT_REASON_LABELS: Final[dict[str, str]] = {
    "stop": "손절",
    "take_profit": "익절",
    "time": "시간 청산",
    "thesis_break": "논지 붕괴(하드)",
    "thesis_soft": "매도 판단",
}


def format_exit_alert(as_of: date, decisions: tuple[ExitDecision, ...]) -> str:
    """Format one alert only after closes have durably completed."""
    lines = [f"🛡 {as_of} 방어선 발동 {len(decisions)}건"]
    lines.extend(
        f"- {decision.position.ticker} {decision.position.quantity}주 · "
        f"{EXIT_REASON_LABELS.get(decision.reason.value, decision.reason.value)}"
        f" @ ${decision.reference_price}"
        for decision in decisions
    )
    return "\n".join(lines)
