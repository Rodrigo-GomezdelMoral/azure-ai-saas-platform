"""Folds usage events from the Service Bus queue into one BillingPeriod document per tenant and month in Cosmos.

Self-contained on purpose: the Flex Consumption package carries this folder only, so nothing here imports the gateway.
"""

import json
import os
from collections import defaultdict
from datetime import UTC, datetime
from functools import cache
from typing import Any, Protocol

import azure.functions as func
from azure.cosmos import CosmosClient
from azure.cosmos.exceptions import CosmosResourceExistsError, CosmosResourceNotFoundError
from azure.identity import DefaultAzureCredential

bp = func.Blueprint()  # type: ignore[no-untyped-call]
COUNTERS = ("requests", "cached", "cents", "upstream_ms")
Store = tuple[str, str, str, str]


class PeriodStore(Protocol):
    def patch_item(self, item: str, partition_key: list[str], patch_operations: list[dict[str, Any]]) -> Any: ...
    def create_item(self, body: dict[str, Any]) -> Any: ...


def aggregate(events: list[dict[str, Any]]) -> dict[Store, dict[str, int]]:
    """Per billing store (database, container), tenant and month: the increments a batch of events adds."""
    totals: dict[Store, dict[str, int]] = defaultdict(lambda: dict.fromkeys(COUNTERS, 0))
    for event in events:
        where = (str(event["database"]), str(event["container"]), str(event["tenant_id"]))
        delta = totals[(*where, str(event["occurred"])[:7])]
        delta["requests"] += 1
        delta["cached"] += int(event.get("cached", 0))
        delta["cents"] += int(event["cents"])
        delta["upstream_ms"] += int(event.get("upstream_ms", 0))
    return totals


def apply(container: PeriodStore, tenant_id: str, month: str, delta: dict[str, int]) -> None:
    """Atomic increments on the period document; a month's first batch creates it, or loses the race and patches."""
    stamp = datetime.now(UTC).isoformat()
    operations: list[dict[str, Any]] = [{"op": "incr", "path": f"/{name}", "value": v} for name, v in delta.items()]
    operations.append({"op": "set", "path": "/updated", "value": stamp})
    try:
        container.patch_item(item=month, partition_key=[tenant_id, month], patch_operations=operations)
    except CosmosResourceNotFoundError:
        try:
            header = {"id": month, "tenantId": tenant_id, "month": month, "rejected": 0, "updated": stamp}
            container.create_item(header | delta)
        except CosmosResourceExistsError:
            container.patch_item(item=month, partition_key=[tenant_id, month], patch_operations=operations)


@cache
def cosmos() -> CosmosClient:
    return CosmosClient(os.environ["COSMOS_ENDPOINT"], DefaultAzureCredential())


@bp.service_bus_queue_trigger(
    arg_name="messages", queue_name="%USAGE_QUEUE%", connection="ServiceBusConnection", cardinality="many"
)
def usage(messages: list[func.ServiceBusMessage]) -> None:
    events = [json.loads(message.get_body()) for message in messages]
    for (database, container, tenant_id, month), delta in aggregate(events).items():
        apply(cosmos().get_database_client(database).get_container_client(container), tenant_id, month, delta)
