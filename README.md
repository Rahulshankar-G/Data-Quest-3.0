# AdPilot

AdPilot is an offline-capable advertising intelligence console for direct-to-consumer
brands. The shipped simulator connects campaign delivery, commerce data,
reconciliation, diagnostics, operator-approved decisions, simulated execution,
and measured learning. Simulator data is explicitly identified as simulation;
it is not a live advertising account or a production-grade connector.

## Run locally

Requirements: Python 3.11+ and Node.js 20+.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
cd frontend && npm install && cd ..
python app.py
```

`python app.py` starts the FastAPI service, waits for schema initialization and
demo seeding, then starts the Next.js console and opens
`http://127.0.0.1:3000`. Stop both services with Ctrl+C. For separate terminals,
run `make api-dev` and `make frontend-dev`; the frontend proxies API calls
server-side and does not expose the API key to browser JavaScript.

The default database is `sqlite+aiosqlite:///./data/adpilot.db`. The API
initializes the schema and idempotently seeds the eight scenarios on startup.
An OpenAI or Anthropic key is optional. Without one, evidence-bound deterministic
explanations are used. Configure `LLM_PROVIDER` and `LLM_API_KEY` in `.env` only
when a provider is desired.

## Docker Compose

The development Compose stack runs the frontend, API, PostgreSQL, and Redis:

```bash
cp .env.example .env
# Replace both local secrets before exposing any service.
docker compose up --build
```

The frontend is available at `http://127.0.0.1:3000`; the API is bound to
`127.0.0.1:8000`. PostgreSQL and Redis persist in named volumes. The API runs
Alembic migrations before startup and seeds the demo brand. The `.env.example`
defaults are for local development only. Production deployments must use unique
high-entropy API and database credentials, TLS, an identity provider, restricted
network access, managed secrets, database backups, and monitored migrations.
The current API-key middleware is not a replacement for user authentication,
authorization, or tenant isolation.

Useful commands: `make up`, `make down`, `make logs`, `make test`,
`make frontend-install`, `make api-dev`, `make frontend-dev`, and `make seed`.

## Operator workflow

1. **Ingest:** simulator connectors provide deterministic Meta, Google Ads,
   Amazon Ads, TikTok, Programmatic, Shopify, and GA4 observations. Import an
   operator-owned CSV or JSON record set through the Data Hub.
2. **Reconcile:** campaign revenue is proportionally capped and allocated
   against store orders by SKU and day. Contribution profit is net revenue less
   COGS, discounts, and ad spend.
3. **Diagnose:** robust median/MAD scoring, STL residuals, PELT change points,
   ROAS factor decomposition, inventory cover, attribution variance, tracking
   health, and creative fatigue evidence are available in the Diagnosis Studio.
4. **Decide:** review prioritized recommendations and their payload and
   constraint checklist. Approval is explicit and attributed to an operator.
5. **Execute:** execution is simulator-only. Campaign budgets change only in
   the local domain store; a rollback restores the exact prior simulator budget.
6. **Learn:** advance the simulation clock to generate counterfactuals, record
   MAPE and profit uplift, and register recalibrated response-model parameters.

The eight seeded scenarios are Meta creative fatigue, stockout risk, a price or
promotion change affecting CVR, Google Shopping / branded-search cannibalization,
TikTok marginal-ROAS headroom, a GA4 tracking outage, platform attribution
overstatement, and an under-promoted high-margin SKU. The Scenario Guide links
each case to the relevant workspace.

## Frontend

The Next.js App Router console includes Command Center, Unified Data Hub, a
multi-level Performance Explorer, Anomaly & Diagnosis Studio, Opportunity Radar,
Decision Center, Budget Optimizer, Learning & Calibration, and the Scenario
Guide. The interface is dark-mode-first and uses React Flow for lineage,
Recharts for measured trends, TanStack Query/Table, and Zustand for client
state.

## Connector and data boundaries

`src/adpilot/connectors.py` defines the asynchronous `BaseConnector` contract
and complete **simulator-mode** implementations. It does not claim to connect
to live Meta, Google, Amazon, TikTok, Programmatic, Shopify, or GA4 services.
Live provider ingestion and execution require provider-specific API
implementations, scoped credentials, webhook/backfill strategies, consent and
privacy review, rate limiting, retries, audit controls, and provider sandbox
certification. No live budget change is performed by this application.

Uploads accept CSV/JSON, validate file size and mapped fields, and persist
campaign-day facts. The seeded and uploaded records are not a substitute for
source-of-truth order attribution. Confirm source semantics, SKU mappings,
currency, COGS, discount handling, and reconciliation coverage before using
results in an operating decision.

## Project map

- `api.py`: FastAPI application, API-key middleware, startup migrations/seeding,
  and simulator ingestion scheduler.
- `src/adpilot/models.py`: SQLAlchemy domain schema.
- `src/adpilot/seed.py`: idempotent demo scenarios and observations.
- `src/adpilot/connectors.py`: connector interface, simulator implementations,
  and scenario catalog.
- `src/adpilot/analysis.py`: statistical methods, decomposition, response curve,
  and constrained budget optimization.
- `src/adpilot/routes.py`: `/api/v1` data, diagnosis, decision, optimizer,
  simulation, and learning API.
- `alembic/`: database migration configuration and revisions.
- `frontend/src/app/`: Next.js operator workspaces and API proxy.
- `tests/`: regression and provider-contract tests for the existing prototype.

The original Streamlit screens and legacy endpoints remain available when
started with `streamlit run app.py`; the supported `python app.py` launcher opens
the AdPilot Next.js console.
