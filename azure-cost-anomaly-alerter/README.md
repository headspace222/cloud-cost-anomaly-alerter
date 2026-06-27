# Azure Cost Anomaly Alerter

A live dashboard that detects when Azure spending spikes above normal patterns, explains *why* it happened, and tells engineers exactly what to do about it — in plain English.

**Live demo:** [headspace222.github.io/cloud-cost-anomaly-alerter](https://headspace222.github.io/git add .
git commit -m "update"
git push
)

---

## The problem this solves

Every organisation running Azure has the same conversation in their monthly engineering meeting:

> *"Why did our Azure bill go up 40% last month — and why did nobody notice until the invoice arrived?"*

Azure Cost Management shows you spending. It does not tell you which specific resource caused a spike, why it happened, or what to do about it. This tool does.

---

## What it does

- Fetches 30 days of daily spend per resource group from the Azure Cost Management API
- Detects anomalies using a rolling 7-day average baseline — any day more than 1.8× the average is flagged
- Explains each anomaly in plain English with the likely root cause
- Provides the exact Azure CLI remediation command for each finding
- Projects month-end spend per resource group vs budget
- Sends a Slack or Teams alert when a new anomaly is detected
- Displays everything on a live public dashboard — no login required

---

## Architecture

```
Azure Function (timer trigger — daily 07:00 UTC)
        │
        ▼
Cost Management API  ──►  Anomaly detection logic
        │
        ▼
  Blob Storage (spend.json)
        │
        ▼
  GitHub Pages (index.html reads spend.json on load)
        │
        ▼
  Optional: Slack / Teams webhook alert
```

**Why this architecture:**
- Zero always-on compute cost — Function runs once daily on consumption plan
- No database — spend.json in blob storage is the entire data layer
- No backend server — dashboard is a static file served by GitHub Pages
- Managed identity on the Function App — no stored credentials anywhere

---

## Repository structure

```
cloud-cost-anomaly-alerter/
├── index.html                        # Static dashboard (GitHub Pages)
├── infra/
│   └── main.bicep                    # IaC — deploys Function App + Storage
├── function/
│   ├── function_app.py               # Azure Function (timer trigger)
│   ├── requirements.txt              # Python dependencies
│   ├── host.json                     # Function runtime config
│   └── local.settings.json.template  # Local dev config template
├── .github/
│   └── workflows/
│       ├── deploy.yml                # GitHub Pages deploy on push to main
│       └── deploy-function.yml       # Azure Function deploy on push to main
├── docs/
│   └── architecture.png              # Architecture diagram
└── README.md
```

---

## Deployment guide

### Prerequisites

- Azure subscription (free tier works for dev/demo)
- Azure CLI installed and logged in (`az login`)
- Python 3.11+
- Azure Functions Core Tools v4

### Step 1 — Deploy the infrastructure

```bash
# Create a resource group
az group create \
  --name rg-cost-anomaly-alerter \
  --location uksouth

# Deploy the Bicep template
az deployment group create \
  --resource-group rg-cost-anomaly-alerter \
  --template-file infra/main.bicep \
  --parameters subscriptionId=$(az account show --query id -o tsv)
```

The deployment outputs the Function App name, Storage Account name, and the public URL for `spend.json`. Note these down.

### Step 2 — Configure GitHub secrets

In your GitHub repo → Settings → Secrets → Actions, add:

| Secret | Value |
|---|---|
| `AZURE_CREDENTIALS` | Output of `az ad sp create-for-rbac --sdk-auth` |
| `FUNCTION_APP_NAME` | Function App name from Step 1 output |

### Step 3 — Deploy the Function App

```bash
cd function
pip install -r requirements.txt
func azure functionapp publish <your-function-app-name>
```

Or push to `main` — the GitHub Actions workflow deploys automatically.

### Step 4 — Enable GitHub Pages

Repo → Settings → Pages → Source: GitHub Actions

Your dashboard goes live at `https://headspace222.github.io/cloud-cost-anomaly-alerter`

### Step 5 — Point the dashboard at your live data

In `index.html`, find the `loadLiveData()` function and update the fetch path if your blob storage URL differs from `data/spend.json`. Alternatively, configure CORS on your storage account:

```bash
az storage cors add \
  --methods GET \
  --origins "https://headspace222.github.io" \
  --services b \
  --account-name <your-storage-account-name>
```

### Optional — Slack or Teams alerts

Set the `ALERT_WEBHOOK_URL` environment variable on the Function App to a Slack Incoming Webhook or Teams connector URL. The function sends a plain-text alert when anomalies are detected.

```bash
az functionapp config appsettings set \
  --name <your-function-app-name> \
  --resource-group rg-cost-anomaly-alerter \
  --settings ALERT_WEBHOOK_URL=https://hooks.slack.com/services/YOUR/WEBHOOK/URL
```

---

## Anomaly detection logic

The function calculates a 7-day rolling average for each resource group. If today's spend exceeds `ANOMALY_THRESHOLD × avg_7d` (default 1.8×), the resource group is flagged.

The threshold is configurable via the `ANOMALY_THRESHOLD` environment variable. 1.8× is tuned to avoid false positives from normal weekend/weekday variation while catching genuine spikes.

---

## Security considerations

| Control | Implementation |
|---|---|
| No stored credentials | Function App uses system-assigned managed identity |
| Least-privilege RBAC | Identity granted `Cost Management Reader` only — cannot modify resources |
| HTTPS only | Enforced on Function App and Storage Account |
| TLS 1.2 minimum | Enforced on all resources |
| No public IP on Function | Consumption plan — no inbound exposure |

---

## Cost to run this

| Resource | Monthly cost |
|---|---|
| Azure Function (Consumption plan) | Free — well within 1M free executions/month |
| Storage Account (LRS, Hot) | ~£0.02/month for spend.json |
| Blob egress (dashboard reads) | Negligible |
| **Total** | **~£0.02/month** |

---

## What I would add with more time

- **Azure Monitor integration** — correlate cost spikes with deployment events from Activity Log
- **Per-resource drill-down** — identify the specific VM, disk, or service driving the spike, not just the resource group
- **Budget management** — create and update budgets via the dashboard, not just read them
- **Historical anomaly log** — track anomalies over time to identify recurring patterns
- **Microsoft Sentinel integration** — cross-reference cost anomalies with security events

---

## Technologies used

- Azure Cost Management API
- Azure Functions (Python, timer trigger)
- Azure Blob Storage
- Azure Managed Identity + RBAC
- Bicep (Infrastructure as Code)
- Chart.js
- GitHub Actions (CI/CD)
- GitHub Pages (static hosting)

---

## Author

Built as part of an Azure cloud engineering portfolio targeting financial services organisations. Demonstrates IaC, serverless architecture, managed identity, least-privilege RBAC, and FinOps thinking.

[LinkedIn](https://linkedin.com/in/yourprofile) | [Portfolio](https://yoursite.dev)
