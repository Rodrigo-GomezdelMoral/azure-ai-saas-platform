"""Monthly cost attribution: what each tenant consumed, measured in the application, against what Azure billed, by tag.

The two sources never agree: application metering carries the inference the upstream reported per request, while Cost
Management bills the shared infrastructure no single tenant causes. The report states both numbers and the gap.
"""

import argparse
import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
from azure.core.credentials_async import AsyncTokenCredential
from azure.cosmos.aio import CosmosClient
from azure.cosmos.exceptions import CosmosResourceNotFoundError
from azure.identity.aio import DefaultAzureCredential

from app.config import Settings
from app.models import PRICE_EUR, BillingPeriod, Plan, Tenant

ARM, COST_API, REPO_TAG = "https://management.azure.com", "2026-06-01", "azure-ai-saas-platform"
COLUMNS = (
    "| Tenant | Plan | Requests | Metered inference | Attributed Azure | Shared Azure | Cost | Price | Gross margin |"
)


@dataclass(frozen=True)
class CostRow:
    service: str
    tenant: str
    eur: float


@dataclass(frozen=True)
class Line:
    tenant: Tenant
    period: BillingPeriod
    attributed_eur: float
    shared_eur: float

    @property
    def metered_eur(self) -> float:
        return self.period.cents / 100

    @property
    def cost_eur(self) -> float:
        return self.metered_eur + self.attributed_eur + self.shared_eur

    @property
    def margin(self) -> float:
        return (PRICE_EUR[self.tenant.plan] - self.cost_eur) / PRICE_EUR[self.tenant.plan]


def query(month: str) -> dict[str, Any]:
    start = datetime.strptime(month, "%Y-%m").replace(tzinfo=UTC)
    end = (start + timedelta(days=32)).replace(day=1) - timedelta(seconds=1)
    period = {"from": start.strftime("%Y-%m-%dT%H:%M:%SZ"), "to": end.strftime("%Y-%m-%dT%H:%M:%SZ")}
    grouping = [{"type": "Dimension", "name": "ServiceName"}, {"type": "TagKey", "name": "tenant"}]
    dataset: dict[str, Any] = {"granularity": "None", "aggregation": {"totalCost": {"name": "Cost", "function": "Sum"}}}
    dataset |= {"grouping": grouping, "filter": {"tags": {"name": "repo", "operator": "In", "values": [REPO_TAG]}}}
    return {"type": "ActualCost", "timeframe": "Custom", "timePeriod": period, "dataset": dataset}


async def azure_costs(http: httpx.AsyncClient, cred: AsyncTokenCredential, scope: str, month: str) -> list[CostRow]:
    token = await cred.get_token(f"{ARM}/.default")
    url = f"{ARM}{scope}/providers/Microsoft.CostManagement/query?api-version={COST_API}"
    response = await http.post(url, json=query(month), headers={"Authorization": f"Bearer {token.token}"})
    response.raise_for_status()
    columns = [str(column["name"]) for column in response.json()["properties"]["columns"]]
    rows = [dict(zip(columns, row, strict=True)) for row in response.json()["properties"]["rows"]]
    return [CostRow(str(row["ServiceName"]), str(row.get("TagValue") or ""), float(row["Cost"])) for row in rows]


async def metered(tenants: list[Tenant], cosmos: CosmosClient, month: str) -> dict[str, BillingPeriod]:
    periods: dict[str, BillingPeriod] = {}
    for tenant in tenants:
        database = cosmos.get_database_client(tenant.isolation.database)
        container = database.get_container_client(tenant.isolation.container)
        try:
            document = dict(await container.read_item(item=month, partition_key=[tenant.id, month]))
        except CosmosResourceNotFoundError:
            document = {}
        periods[tenant.id] = BillingPeriod.model_validate(document | {"tenant_id": tenant.id, "month": month})
    return periods


def reconcile(tenants: list[Tenant], periods: dict[str, BillingPeriod], rows: list[CostRow]) -> list[Line]:
    """Tagged cost goes to its tenant; the rest is shared and allocated by each tenant's share of metered usage."""
    attributed = {tenant.id: sum(row.eur for row in rows if row.tenant == tenant.id) for tenant in tenants}
    shared = sum(row.eur for row in rows) - sum(attributed.values())
    weights = {tenant.id: float(periods[tenant.id].cents) for tenant in tenants}
    if not sum(weights.values()):
        weights = dict.fromkeys(weights, 1.0)
    total = sum(weights.values())
    return [Line(t, periods[t.id], attributed[t.id], shared * weights[t.id] / total) for t in tenants]


def by_plan(lines: list[Line]) -> str:
    table = "\n| Plan | Tenants | Price | Average cost | Average gross margin |\n|---|---:|---:|---:|---:|\n"
    for plan in Plan:
        members = [line for line in lines if line.tenant.plan is plan]
        if members:
            cost = sum(line.cost_eur for line in members) / len(members)
            margin = (PRICE_EUR[plan] - cost) / PRICE_EUR[plan]
            table += f"| {plan.value} | {len(members)} | {PRICE_EUR[plan]} € | {cost:.2f} € | {margin:.0%} |\n"
    return table


def render(month: str, lines: list[Line], rows: list[CostRow]) -> str:
    total, metered_eur = sum(row.eur for row in rows), sum(line.metered_eur for line in lines)
    tagged = sum(line.attributed_eur for line in lines)
    report = f"# Chargeback {month}\n\n{COLUMNS}\n|---|---|---:|---:|---:|---:|---:|---:|---:|\n"
    for line in lines:
        cells = [line.tenant.id, line.tenant.plan.value, str(line.period.requests), f"{line.metered_eur:.2f} €"]
        cells += [f"{line.attributed_eur:.2f} €", f"{line.shared_eur:.2f} €", f"{line.cost_eur:.2f} €"]
        cells += [f"{PRICE_EUR[line.tenant.plan]} €", f"{line.margin:.0%}"]
        report += f"| {' | '.join(cells)} |\n"
    report += by_plan(lines)
    report += (
        f"\nAzure billed {total:.2f} € to the resource group for {month}: {tagged:.2f} € carries a `tenant` tag and "
        f"{total - tagged:.2f} € is shared infrastructure no single tenant causes (the cluster, the cache, the data "
        f"plane, messaging), allocated above by each tenant's share of metered usage. Application metering recorded "
        f"{metered_eur:.2f} € of inference, which Azure bills to the RAG platform's resource group rather than to this "
        "one. The two sources measure different things, which is why both are reported and the gap is explained.\n"
    )
    return report


async def main() -> None:
    parser = argparse.ArgumentParser(description="Reconcile application metering with Azure Cost Management")
    previous = (datetime.now(UTC).replace(day=1) - timedelta(days=1)).strftime("%Y-%m")
    parser.add_argument("--month", default=previous, help="YYYY-MM; the previous month by default")
    parser.add_argument("--output", type=Path, help="also write the Markdown report to this file")
    args, settings, credential = parser.parse_args(), Settings(), DefaultAzureCredential()
    cosmos = CosmosClient(settings.cosmos_endpoint, credential)
    async with credential, httpx.AsyncClient(timeout=60) as http, cosmos:
        database = cosmos.get_database_client(settings.cosmos_database)
        catalogue = database.get_container_client(settings.tenants_container)
        tenants = [Tenant.model_validate(dict(item)) async for item in catalogue.query_items("SELECT * FROM c")]
        scope = settings.cosmos_account_id.split("/providers/")[0]
        rows = await azure_costs(http, credential, scope, args.month)
        report = render(args.month, reconcile(tenants, await metered(tenants, cosmos, args.month), rows), rows)
    if args.output:
        args.output.write_text(report, encoding="utf-8")
    print(report)


if __name__ == "__main__":
    asyncio.run(main())
