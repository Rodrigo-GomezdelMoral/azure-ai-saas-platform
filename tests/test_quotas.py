from typing import Any

import pytest

from app.models import PLANS, Plan, Quota
from app.quotas import QuotaGate

QUOTA = Quota(requests_per_minute=3, monthly_cents=20, burst_cents=6, estimated_cents=2)


class Clock:
    def __init__(self, start: float = 1_800_000_000.0) -> None:
        self.now = start

    async def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def gate(redis: Any, clock: Clock) -> QuotaGate:
    return QuotaGate(redis, clock)


async def test_burst_bucket_rejects_at_its_ceiling_and_refills_with_time(gate: QuotaGate, clock: Clock) -> None:
    quota = QUOTA.model_copy(update={"requests_per_minute": 100})
    decisions = [await gate.reserve("acme", quota) for _ in range(4)]
    assert [decision.allowed for decision, _ in decisions] == [True, True, True, False]
    assert decisions[3][0].reason == "burst" and decisions[3][0].retry_after >= 1
    clock.now += decisions[3][0].retry_after * 100_000
    decision, _ = await gate.reserve("acme", quota)
    assert decision.allowed and decision.remaining_cents == quota.burst_cents - quota.estimated_cents


async def test_rate_limit_rejects_within_the_minute(gate: QuotaGate, clock: Clock) -> None:
    quota = QUOTA.model_copy(update={"burst_cents": 1_000, "monthly_cents": 100_000})
    outcomes = [(await gate.reserve("acme", quota))[0] for _ in range(4)]
    assert [outcome.allowed for outcome in outcomes] == [True, True, True, False] and outcomes[3].reason == "rate"
    clock.now += 61
    assert (await gate.reserve("acme", quota))[0].allowed


async def test_monthly_cap_rejects_once_measured_spend_reaches_the_plan(gate: QuotaGate) -> None:
    quota = QUOTA.model_copy(update={"burst_cents": 1_000, "requests_per_minute": 1_000})
    decision, reservation = await gate.reserve("acme", quota)
    assert decision.allowed and reservation is not None
    await gate.settle(reservation, actual_cents=19)
    assert await gate.spent_cents("acme", reservation.month) == 19
    decision, _ = await gate.reserve("acme", quota)
    assert not decision.allowed and decision.reason == "monthly"


async def test_settlement_refunds_the_difference_and_release_refunds_everything(gate: QuotaGate) -> None:
    quota = QUOTA.model_copy(update={"requests_per_minute": 100})
    _, reservation = await gate.reserve("acme", quota)
    assert reservation is not None
    await gate.settle(reservation, actual_cents=1)
    assert (await gate.reserve("acme", quota))[0].remaining_cents == quota.burst_cents - 1 - quota.estimated_cents
    _, second = await gate.reserve("acme", quota)
    assert second is not None
    await gate.release(second)
    assert (await gate.reserve("acme", quota))[0].remaining_cents == quota.burst_cents - 1 - 2 * quota.estimated_cents
    assert await gate.spent_cents("acme", reservation.month) == 1


async def test_plans_differ_and_every_plan_admits_its_first_request(gate: QuotaGate) -> None:
    assert PLANS[Plan.STARTER].monthly_cents < PLANS[Plan.BUSINESS].monthly_cents < PLANS[Plan.ENTERPRISE].monthly_cents
    for plan, quota in PLANS.items():
        decision, reservation = await gate.reserve(f"tenant-{plan}", quota)
        assert decision.allowed and reservation is not None and reservation.cents == quota.estimated_cents
