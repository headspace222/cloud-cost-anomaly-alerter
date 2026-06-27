"""
Azure Function: fetch_spend_data
Runs on a daily timer trigger. Calls the Azure Cost Management API,
detects anomalies, and writes spend.json to blob storage.
The static dashboard reads this file on load.

Requirements:
  pip install azure-functions azure-identity azure-mgmt-costmanagement azure-storage-blob

Local development:
  func start

Environment variables (set in Azure Function App settings):
  AZURE_SUBSCRIPTION_ID   - Your subscription ID
  AZURE_STORAGE_CONN_STR  - Connection string for the storage account
  STORAGE_CONTAINER       - Blob container name (e.g. "dashboarddata")
  ANOMALY_THRESHOLD       - Multiplier above 7-day avg to flag anomaly (default: 1.8)
  ALERT_WEBHOOK_URL       - Optional: Slack/Teams webhook for alert notifications
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

import azure.functions as func
from azure.identity import DefaultAzureCredential
from azure.mgmt.costmanagement import CostManagementClient
from azure.mgmt.costmanagement.models import (
    QueryDefinition, QueryTimePeriod, QueryDataset,
    QueryAggregation, QueryGrouping, GranularityType
)
from azure.storage.blob import BlobServiceClient

app = func.FunctionApp()

SUBSCRIPTION_ID   = os.environ["AZURE_SUBSCRIPTION_ID"]
STORAGE_CONN_STR  = os.environ["AZURE_STORAGE_CONN_STR"]
CONTAINER_NAME    = os.environ.get("STORAGE_CONTAINER", "dashboarddata")
THRESHOLD         = float(os.environ.get("ANOMALY_THRESHOLD", "1.8"))
WEBHOOK_URL       = os.environ.get("ALERT_WEBHOOK_URL", "")


@app.timer_trigger(schedule="0 0 7 * * *", arg_name="timer", run_on_startup=False)
def fetch_spend_data(timer: func.TimerRequest) -> None:
    """Runs daily at 07:00 UTC. Fetches 30-day spend, detects anomalies,
    writes spend.json to blob storage."""
    logging.info("fetch_spend_data triggered at %s", datetime.now(timezone.utc).isoformat())

    try:
        spend_data = _fetch_cost_management_data()
        anomalies  = _detect_anomalies(spend_data)
        payload    = _build_payload(spend_data, anomalies)
        _write_to_blob(payload)

        if anomalies and WEBHOOK_URL:
            _send_alert_webhook(anomalies)

        logging.info("Done — %d resource groups, %d anomalies", len(spend_data), len(anomalies))

    except Exception as exc:
        logging.error("fetch_spend_data failed: %s", exc, exc_info=True)
        raise


def _fetch_cost_management_data() -> list[dict]:
    """Query Cost Management API for daily spend by resource group, last 30 days."""
    credential = DefaultAzureCredential()
    client     = CostManagementClient(credential)

    end_date   = datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=29)
    scope      = f"/subscriptions/{SUBSCRIPTION_ID}"

    query = QueryDefinition(
        type="ActualCost",
        timeframe="Custom",
        time_period=QueryTimePeriod(
            from_property=datetime.combine(start_date, datetime.min.time(), tzinfo=timezone.utc),
            to=datetime.combine(end_date, datetime.min.time(), tzinfo=timezone.utc),
        ),
        dataset=QueryDataset(
            granularity=GranularityType.DAILY,
            aggregation={"totalCost": QueryAggregation(name="Cost", function="Sum")},
            grouping=[QueryGrouping(type="Dimension", name="ResourceGroupName")],
        ),
    )

    result = client.query.usage(scope=scope, parameters=query)

    # Parse into {rg_name: [daily_spend]} structure
    rg_data: dict[str, dict] = {}
    columns = [col.name for col in result.columns]
    cost_idx = columns.index("Cost")
    date_idx = columns.index("UsageDate")
    rg_idx   = columns.index("ResourceGroupName")

    for row in result.rows:
        rg   = row[rg_idx] or "unassigned"
        date = str(row[date_idx])          # yyyymmdd integer from API
        cost = float(row[cost_idx])

        if rg not in rg_data:
            rg_data[rg] = {"name": rg, "daily": {}}
        rg_data[rg]["daily"][date] = round(cost, 2)

    # Fill missing days with 0 and sort
    all_dates = sorted({
        (start_date + timedelta(days=i)).strftime("%Y%m%d")
        for i in range(30)
    })
    for rg in rg_data.values():
        rg["daily"] = [rg["daily"].get(d, 0.0) for d in all_dates]
        rg["dates"] = all_dates
        rg["mtd"]   = round(sum(rg["daily"]), 2)

    return list(rg_data.values())


def _detect_anomalies(spend_data: list[dict]) -> list[dict]:
    """Flag resource groups where today's spend is more than THRESHOLD x 7-day average."""
    anomalies = []
    for rg in spend_data:
        daily = rg["daily"]
        if len(daily) < 8:
            continue
        today_spend = daily[-1]
        avg_7d      = sum(daily[-8:-1]) / 7
        if avg_7d > 0 and today_spend > avg_7d * THRESHOLD:
            pct_increase = round((today_spend / avg_7d - 1) * 100)
            anomalies.append({
                "resource_group": rg["name"],
                "today_spend":    today_spend,
                "avg_7d":         round(avg_7d, 2),
                "pct_increase":   pct_increase,
                "reason":         (
                    f"Spend of £{today_spend:.2f} is {pct_increase}% above the "
                    f"7-day average of £{avg_7d:.2f}. Investigate recent deployments, "
                    f"unattached disks, or running VMs in this resource group."
                ),
                "remediation": (
                    f"az resource list --resource-group {rg['name']} "
                    f"--query \"[?tags.Environment!='Production']\" --output table"
                ),
            })
    return anomalies


def _build_payload(spend_data: list[dict], anomalies: list[dict]) -> dict:
    """Build the JSON payload written to blob and consumed by the dashboard."""
    total_daily = [
        round(sum(rg["daily"][i] for rg in spend_data), 2)
        for i in range(len(spend_data[0]["daily"]) if spend_data else 0)
    ]
    return {
        "generated_at":   datetime.now(timezone.utc).isoformat(),
        "subscription_id": SUBSCRIPTION_ID,
        "anomaly_count":  len(anomalies),
        "total_mtd":      round(sum(rg["mtd"] for rg in spend_data), 2),
        "total_daily":    total_daily,
        "resource_groups": spend_data,
        "anomalies":      anomalies,
    }


def _write_to_blob(payload: dict) -> None:
    """Write spend.json to Azure Blob Storage so the static dashboard can read it."""
    blob_service = BlobServiceClient.from_connection_string(STORAGE_CONN_STR)
    container    = blob_service.get_container_client(CONTAINER_NAME)

    try:
        container.create_container()
    except Exception:
        pass  # Already exists

    blob = container.get_blob_client("spend.json")
    blob.upload_blob(
        json.dumps(payload, indent=2),
        overwrite=True,
        content_settings=None,
    )
    logging.info("spend.json written to blob container '%s'", CONTAINER_NAME)


def _send_alert_webhook(anomalies: list[dict]) -> None:
    """Send a concise anomaly alert to a Slack or Teams webhook."""
    import urllib.request
    lines = [f"*Azure Cost Anomaly Alert* — {len(anomalies)} issue(s) detected\n"]
    for a in anomalies:
        lines.append(
            f"• *{a['resource_group']}*: £{a['today_spend']:.2f} today "
            f"(+{a['pct_increase']}% vs 7-day avg)\n  {a['reason']}"
        )
    body = json.dumps({"text": "\n".join(lines)}).encode()
    req  = urllib.request.Request(WEBHOOK_URL, data=body, headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10)
    logging.info("Webhook alert sent for %d anomalies", len(anomalies))
