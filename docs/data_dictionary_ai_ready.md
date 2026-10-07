# ProfitPilot AI Data Dictionary

## Dataset overview

- **File:** `data/engineered_d2c_dataset.csv`
- **Rows:** 6,000
- **Columns:** 29
- **Purpose:** D2C advertising, profitability, inventory, and campaign-decision analysis.
- **Record grain:** One campaign/SKU observation at a timestamp.
- **Primary campaign key:** `campaign_id`
- **Product key:** `sku_id`

## Decision fields

| Business concept | Dataset column | Use in system |
|---|---|---|
| Observation timestamp | `date` | Trend analysis and anomaly detection |
| Region | `region` | Regional comparison |
| Sales channel | `channel` | Channel-level analysis |
| Product category | `product_category` | Product-group comparison |
| Customer segment | `customer_segment` | Segment-level diagnosis |
| Advertising platform | `ad_platform` | Cross-platform comparison |
| Campaign | `campaign_id` | Campaign aggregation and recommendations |
| Product/SKU | `sku_id` | Product-level decisions |
| Advertising spend | `ad_spend` | Cost, ROAS, and profit calculations |
| Impressions | `impressions` | Reach and CTR calculations |
| Estimated clicks | `estimated_clicks` | CTR and conversion-rate calculations |
| Click-through rate | `click_through_rate` | Engagement signal |
| Conversions | `conversions` | Conversion efficiency |
| Units sold | `units_sold` | Sales-volume analysis |
| Sales revenue | `sales_revenue` | Revenue and ROAS calculations |
| Product cost | `total_cogs` | Cost and profit calculations |
| Unit cost | `unit_cogs` | Product economics |
| Dataset gross profit | `gross_profit` | Reference profit field |
| Profit margin | `profit_margin_pct` | Margin constraint for recommendations |
| Stock level | `stock_level` | Inventory-risk constraint |
| Product price | `price` | Pricing context |
| Effective price | `effective_price` | Discount-adjusted price context |
| Discount rate | `discount_rate` | Offer and pricing diagnosis |
| Market reach | `market_reach` | Reach context |
| Competition | `competition_index` | Competitive-pressure diagnosis |
| Seasonality | `seasonality_index` | Demand/trend context |
| Campaign duration | `campaign_duration_days` | Campaign lifecycle context |
| Customer lifetime value | `customer_lifetime_value` | Customer-value context |

## Derived metrics

The analytics layer should calculate these metrics from the raw fields rather than asking the AI model to calculate them.

```python
roas = sales_revenue / ad_spend

conversion_rate = conversions / estimated_clicks

ctr = estimated_clicks / impressions

cpa = ad_spend / conversions

contribution_profit = sales_revenue - total_cogs - ad_spend
```

The implementation must protect against division by zero. Use null values or a safe denominator when spend, clicks, impressions, or conversions are zero.

## Profit definition

For budget decisions, use:

```text
contribution_profit = sales_revenue - total_cogs - ad_spend
```

This includes both product cost and advertising cost. Treat the dataset's `gross_profit` as a reference field until the team confirms exactly how it was calculated.

## Margin representation

Inspect the values in `profit_margin_pct` before applying thresholds. In the current dataset, values appear to be decimal fractions such as `0.4202`, representing approximately 42.02%, rather than whole-number percentages such as `42.02`.

Therefore, a 35% threshold should normally be represented as:

```python
margin_threshold = 0.35
```

Do not multiply or divide this field again without checking the data.

## Data-quality requirements

Before analysis, check:

- Missing dates and invalid date values.
- Zero values in `ad_spend`, `impressions`, `estimated_clicks`, and `conversions`.
- Duplicate or unexpected campaign/SKU identifiers.
- Negative contribution profit.
- Very low stock levels.
- Consistency between `gross_profit`, `sales_revenue`, and `total_cogs`.
- Whether platform, channel, region, and category labels have inconsistent capitalization.

The original CSV must not be modified. Perform cleaning in Python and keep the raw file unchanged.

## AI Diagnostic Context

The AI diagnostics API must receive only aggregated, decision-relevant facts. It must not receive the entire raw dataset.

### Alert identity fields

| Field | Purpose |
|---|---|
| `day` | Date of the detected performance shift |
| `ad_platform` | Platform where the alert occurred |
| `campaign_id` | Campaign being diagnosed |
| `severity` | `Warning` or `Critical` anomaly level |

### Performance evidence fields

| Field | Purpose |
|---|---|
| `spend` | Daily advertising spend |
| `revenue` | Daily attributed sales revenue |
| `conversions` | Daily conversions |
| `clicks` | Daily estimated clicks |
| `roas` | Revenue divided by spend |
| `previous_roas` | Previous available ROAS for the same campaign |
| `roas_change_pct` | ROAS change from the previous available day |
| `conversion_rate` | Conversions divided by clicks |
| `previous_conversion_rate` | Previous available conversion rate |
| `conversion_change_pct` | Conversion-rate change from the previous available day |

### Diagnostic context fields

| Field | Purpose |
|---|---|
| `avg_competition` | Possible auction or competitive-pressure signal |
| `avg_discount` | Pricing or offer context |
| `avg_stock` | Inventory-availability context |
| `avg_margin` | Product-margin context |

## AI payload contract

Person 3's anomaly module should create a JSON-ready payload similar to:

```json
{
  "day": "2011-05-16",
  "ad_platform": "Search",
  "campaign_id": "CAMP_SEARCH_SKU-GEN-104",
  "severity": "Critical",
  "spend": 458.0,
  "revenue": 74.36,
  "conversions": 11,
  "clicks": 208,
  "roas": 0.1624,
  "previous_roas": 0.45,
  "roas_change_pct": -0.639,
  "conversion_rate": 0.0529,
  "previous_conversion_rate": 0.10,
  "conversion_change_pct": -0.471,
  "avg_competition": 9.19,
  "avg_discount": 0.2625,
  "avg_stock": 273.0,
  "avg_margin": 0.2899
}
```

The exact values will be generated from the current data at runtime. The example only shows the required structure.

## AI guardrails

- Send only the aggregated alert payload, never all raw CSV rows.
- The deterministic Python metrics and anomaly rules are the source of truth.
- The AI must use only the supplied metrics and context.
- The AI must not invent figures, campaign names, or causes.
- The AI should say `likely`, `possible`, or `consistent with` when describing causes.
- The AI should distinguish evidence from interpretation.
- The AI should return a concise diagnosis, evidence bullets, risk, and recommended investigation.
- Do not let the AI directly change an advertising budget during the hackathon demo.
- API keys must be stored in `.env` and never committed to GitHub.
- Add `.env` to `.gitignore`.

## Recommended AI response structure

```json
{
  "diagnosis": "Likely post-click conversion issue",
  "evidence": [
    "ROAS declined by 63.9% compared with the previous available day.",
    "Conversion rate declined by 47.1%.",
    "The campaign continued to spend while producing low revenue."
  ],
  "possible_drivers": [
    "Landing-page friction",
    "Price or offer issue",
    "Checkout problem"
  ],
  "risk": "The data does not prove causality; investigate the product page and checkout events.",
  "recommended_investigation": "Check landing-page conversion, price changes, and checkout failures.",
  "confidence": 0.82
}
```

## Team handoff

- **Person 2:** Owns this data dictionary, profiling, and data-quality documentation.
- **Person 3:** Produces deterministic anomaly records and the AI-ready alert payload.
- **AI/API person:** Sends the payload to the chosen model API and validates the structured response.
- **Decision-engine person:** Uses the measured metrics and AI explanation to create budget recommendations.
- **Dashboard person:** Displays alerts, explanations, recommendations, and decision status.
- **Integration lead:** Ensures all modules use the same field names and payload contract.
