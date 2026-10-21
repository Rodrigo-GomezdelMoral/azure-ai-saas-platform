from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, Field

SECONDS_PER_MONTH = 30 * 24 * 3600


class Plan(StrEnum):
    STARTER = "starter"
    BUSINESS = "business"
    ENTERPRISE = "enterprise"


class PiiPolicy(StrEnum):
    NONE = "none"
    BEFORE_UPSTREAM = "before_upstream"
    BEFORE_STORAGE = "before_storage"


class Quota(BaseModel):
    """What a plan buys per month, in euro cents, and how fast it may be consumed."""

    requests_per_minute: int = Field(gt=0)
    monthly_cents: int = Field(gt=0)
    burst_cents: int = Field(gt=0)
    estimated_cents: int = Field(gt=0)

    @property
    def refill_per_second(self) -> float:
        return self.monthly_cents / SECONDS_PER_MONTH


PLANS: dict[Plan, Quota] = {
    Plan.STARTER: Quota(requests_per_minute=20, monthly_cents=2_000, burst_cents=50, estimated_cents=2),
    Plan.BUSINESS: Quota(requests_per_minute=120, monthly_cents=20_000, burst_cents=500, estimated_cents=2),
    Plan.ENTERPRISE: Quota(requests_per_minute=600, monthly_cents=200_000, burst_cents=5_000, estimated_cents=2),
}
PRICE_EUR: dict[Plan, int] = {Plan.STARTER: 49, Plan.BUSINESS: 490, Plan.ENTERPRISE: 2_900}


class Isolation(BaseModel):
    """Where a tenant's data lives; the plan decides what is shared and what is dedicated."""

    namespace: str
    index: str
    database: str
    container: str

    @classmethod
    def for_plan(cls, plan: Plan, tenant_id: str, shared_index: str, database: str) -> "Isolation":
        if plan is Plan.STARTER:
            return cls(namespace="tenants-starter", index=shared_index, database=database, container="usage")
        dedicated = f"t-{tenant_id}"
        if plan is Plan.BUSINESS:
            return cls(namespace=dedicated, index=dedicated, database=database, container=f"usage-{tenant_id}")
        return cls(namespace=dedicated, index=dedicated, database=dedicated, container="usage")


class Tenant(BaseModel):
    id: str = Field(pattern=r"^[a-z][a-z0-9-]{2,30}$")
    name: str = Field(min_length=2, max_length=80)
    plan: Plan
    pii: PiiPolicy = PiiPolicy.NONE
    isolation: Isolation
    client_id: str | None = None
    created: datetime
    suspended: bool = False

    @property
    def quota(self) -> Quota:
        return PLANS[self.plan]


class Credential(BaseModel):
    """The record of an issued API credential: its hash and its key id, never the credential itself."""

    tenant_id: str
    kid: str
    digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    issued: datetime
    revoked: datetime | None = None


class UsageEvent(BaseModel):
    tenant_id: str
    trace_id: str
    cents: int = Field(ge=0)
    cached: bool = False
    redacted: bool = False
    upstream_ms: int = Field(default=0, ge=0)
    occurred: datetime


class BillingPeriod(BaseModel):
    """One tenant's month, as the aggregation Function keeps it in Cosmos under /tenantId then /month."""

    tenant_id: str
    month: str = Field(pattern=r"^\d{4}-\d{2}$")
    requests: int = 0
    cached: int = 0
    rejected: int = 0
    cents: int = 0
    upstream_ms: int = 0
