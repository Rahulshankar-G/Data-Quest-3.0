from abc import ABC, abstractmethod
from datetime import date, timedelta
from typing import Any


class BaseConnector(ABC):
    key: str
    name: str

    @abstractmethod
    async def authenticate(self) -> bool:
        """Validate credentials or simulator availability."""

    @abstractmethod
    async def fetch_incremental(self, since: date) -> list[dict[str, Any]]:
        """Return provider records newer than the supplied date."""

    @abstractmethod
    async def normalize(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Normalize provider records into canonical campaign observations."""

    @abstractmethod
    async def push_changes(self, external_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        """Apply changes only through the connector's configured execution mode."""


class SimulatorConnector(BaseConnector):
    key = "simulator"
    name = "Simulator"
    scenario_key = "demo"
    platform_key = "simulator"
    baseline = {
        "spend": 1000.0,
        "impressions": 10000,
        "clicks": 250,
        "conversions": 12,
        "revenue": 2400.0,
        "daily_budget": 1200.0,
        "sku": "SKU-CORE",
        "unit_cost": 24.0,
        "price": 60.0,
        "inventory": 240,
        "units_per_day": 14.0,
        "discount": 0.0,
        "scenario_title": "Simulator baseline",
        "summary": "Baseline campaign with healthy delivery and measurable commerce data.",
        "drivers": ["Baseline measurements are consistent across ad and order sources."],
        "severity": "low",
        "priority": 30,
        "change_pct": 0.0,
    }

    async def authenticate(self) -> bool:
        return True

    async def fetch_incremental(self, since: date) -> list[dict[str, Any]]:
        return [{"since": since.isoformat(), **self.baseline}]

    async def normalize(self, records: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return records

    async def push_changes(self, external_id: str, changes: dict[str, Any]) -> dict[str, Any]:
        return {
            "mode": "simulator",
            "external_id": external_id,
            "changes": changes,
            "result": "simulated",
        }


class MetaSimulator(SimulatorConnector):
    key = "meta"
    name = "Meta"
    platform_key = "meta"
    scenario_key = "meta_creative_fatigue"
    baseline = {
        **SimulatorConnector.baseline,
        "spend": 1800.0,
        "impressions": 70000,
        "clicks": 620,
        "conversions": 19,
        "revenue": 2660.0,
        "daily_budget": 2000.0,
        "sku": "SKU-LAUNCH",
        "inventory": 180,
        "scenario_title": "Meta creative fatigue",
        "summary": "Frequency rose while click-through rate fell and acquisition cost increased.",
        "drivers": ["CTR fell 42% over the measured window.", "Frequency reached 5.8."],
        "severity": "high",
        "priority": 91,
        "change_pct": -15.0,
    }


class GoogleSimulator(SimulatorConnector):
    key = "google"
    name = "Google Ads"
    platform_key = "google"
    scenario_key = "shopping_brand_cannibalization"
    baseline = {
        **SimulatorConnector.baseline,
        "spend": 3200.0,
        "impressions": 49000,
        "clicks": 1250,
        "conversions": 31,
        "revenue": 5480.0,
        "daily_budget": 4000.0,
        "sku": "SKU-SEARCH",
        "inventory": 340,
        "scenario_title": "Shopping / branded-search overlap",
        "summary": "Shopping spend expanded while branded-search attributed revenue shifted between campaigns.",
        "drivers": ["Branded-search attributed revenue declined 28%.", "Shopping auction share increased."],
        "severity": "medium",
        "priority": 73,
        "change_pct": -10.0,
    }


class AmazonSimulator(SimulatorConnector):
    key = "amazon"
    name = "Amazon Ads"
    platform_key = "amazon"


class TikTokSimulator(SimulatorConnector):
    key = "tiktok"
    name = "TikTok"
    platform_key = "tiktok"
    scenario_key = "tiktok_uncapped_headroom"
    baseline = {
        **SimulatorConnector.baseline,
        "spend": 920.0,
        "impressions": 62000,
        "clicks": 1450,
        "conversions": 47,
        "revenue": 7050.0,
        "daily_budget": 1000.0,
        "sku": "SKU-TREND",
        "inventory": 940,
        "scenario_title": "TikTok marginal-ROAS headroom",
        "summary": "Spend is below the daily cap while the measured marginal return is rising.",
        "drivers": ["Three sequential spend cohorts show improving marginal ROAS.", "Budget utilization is 92%."],
        "severity": "opportunity",
        "priority": 86,
        "change_pct": 18.0,
    }


class ProgrammaticSimulator(SimulatorConnector):
    key = "programmatic"
    name = "Programmatic"
    platform_key = "programmatic"


class ShopifySimulator(SimulatorConnector):
    key = "shopify"
    name = "Shopify"
    platform_key = "shopify"
    scenario_key = "stockout_risk"
    baseline = {
        **SimulatorConnector.baseline,
        "spend": 2550.0,
        "impressions": 94000,
        "clicks": 2280,
        "conversions": 69,
        "revenue": 10350.0,
        "daily_budget": 2700.0,
        "sku": "SKU-HERO",
        "unit_cost": 21.0,
        "price": 70.0,
        "inventory": 26,
        "units_per_day": 11.5,
        "scenario_title": "Heavily advertised stockout risk",
        "summary": "Inventory cover is below three days while paid spend remains high.",
        "drivers": ["Inventory cover is 2.3 days.", "Campaign spend is 94% of daily budget."],
        "severity": "critical",
        "priority": 99,
        "change_pct": -45.0,
    }


class Ga4Simulator(SimulatorConnector):
    key = "ga4"
    name = "GA4"
    platform_key = "ga4"
    scenario_key = "ga4_tracking_outage"
    baseline = {
        **SimulatorConnector.baseline,
        "spend": 1680.0,
        "impressions": 46000,
        "clicks": 1310,
        "conversions": 4,
        "revenue": 520.0,
        "daily_budget": 1900.0,
        "sku": "SKU-TRACK",
        "inventory": 410,
        "scenario_title": "GA4 tracking outage",
        "summary": "Analytics conversion events fell but commerce orders and platform conversions remained steady.",
        "drivers": ["GA4 purchase events fell 83%.", "Shopify orders did not show a comparable drop."],
        "severity": "high",
        "priority": 94,
        "change_pct": 0.0,
    }


class MetaPriceSimulator(MetaSimulator):
    key = "meta_price"
    scenario_key = "price_promo_cvr_drop"
    baseline = {
        **MetaSimulator.baseline,
        "spend": 1260.0,
        "impressions": 56000,
        "clicks": 1540,
        "conversions": 18,
        "revenue": 2520.0,
        "daily_budget": 1500.0,
        "sku": "SKU-PRICE",
        "inventory": 510,
        "scenario_title": "Price rise / promotion removal",
        "summary": "Conversion rate dropped after a price increase and promotion ended.",
        "drivers": ["Price increased 16%.", "Promotion ended before CVR declined 34%."],
        "severity": "high",
        "priority": 84,
        "change_pct": -12.0,
    }


class AmazonMarginSimulator(AmazonSimulator):
    key = "amazon_margin"
    scenario_key = "high_margin_underpromoted"
    baseline = {
        **AmazonSimulator.baseline,
        "spend": 260.0,
        "impressions": 21000,
        "clicks": 490,
        "conversions": 21,
        "revenue": 2730.0,
        "daily_budget": 900.0,
        "sku": "SKU-MARGIN",
        "unit_cost": 18.0,
        "price": 90.0,
        "inventory": 1250,
        "units_per_day": 6.0,
        "scenario_title": "High-margin, high-stock under-promotion",
        "summary": "A high-contribution-margin SKU has abundant inventory and unused budget capacity.",
        "drivers": ["Contribution margin is 80%.", "Inventory cover exceeds 200 days.", "Budget utilization is 29%."],
        "severity": "opportunity",
        "priority": 79,
        "change_pct": 25.0,
    }


CONNECTORS: dict[str, BaseConnector] = {
    connector.key: connector()
    for connector in (
        MetaSimulator,
        GoogleSimulator,
        AmazonSimulator,
        TikTokSimulator,
        ProgrammaticSimulator,
        ShopifySimulator,
        Ga4Simulator,
        MetaPriceSimulator,
        AmazonMarginSimulator,
    )
}

PLATFORMS = (
    ("meta", "Meta"),
    ("google", "Google Ads"),
    ("amazon", "Amazon Ads"),
    ("tiktok", "TikTok"),
    ("programmatic", "Programmatic"),
    ("shopify", "Shopify"),
    ("ga4", "GA4"),
)

SCENARIO_CATALOG = (
    {"key": "meta_creative_fatigue", "title": "Meta creative fatigue", "category": "Creative", "connector": "meta"},
    {"key": "stockout_risk", "title": "Advertised SKU stockout risk", "category": "Inventory", "connector": "shopify"},
    {"key": "price_promo_cvr_drop", "title": "Price hike / promo removal", "category": "Conversion", "connector": "meta_price"},
    {"key": "shopping_brand_cannibalization", "title": "Shopping cannibalizes branded search", "category": "Attribution", "connector": "google"},
    {"key": "tiktok_uncapped_headroom", "title": "TikTok rising marginal ROAS", "category": "Scale", "connector": "tiktok"},
    {"key": "ga4_tracking_outage", "title": "GA4 tracking outage", "category": "Measurement", "connector": "ga4"},
    {"key": "attribution_overstatement", "title": "Platform attribution exceeds store orders", "category": "Reconciliation", "connector": "google"},
    {"key": "high_margin_underpromoted", "title": "High-margin SKU under-promoted", "category": "Opportunity", "connector": "amazon_margin"},
)
