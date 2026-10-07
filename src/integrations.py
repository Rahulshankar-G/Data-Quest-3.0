import json
import os
import tempfile
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import requests

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MAPPING_PATH = ROOT / "data" / "campaign_sku_mapping.json"
DEFAULT_LIVE_DATA_PATH = ROOT / "data" / "live_campaign_data.csv"
DEFAULT_LIVE_METADATA_PATH = ROOT / "data" / "live_campaign_data.json"
DEFAULT_LIVE_ORDERS_PATH = ROOT / "data" / "live_shopify_orders.csv"
HTTP_TIMEOUT = (5, 45)

SHOPIFY_VARIANTS_QUERY = """
query ProductVariants($after: String) {
  shop { currencyCode }
  productVariants(first: 100, after: $after) {
    nodes {
      sku
      price
      inventoryQuantity
      product { title productType }
      inventoryItem { unitCost { amount currencyCode } }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

SHOPIFY_ORDERS_QUERY = """
query RecentOrders($after: String, $search: String!) {
  orders(first: 100, after: $after, query: $search, sortKey: CREATED_AT) {
    nodes {
      id
      createdAt
      currencyCode
      cancelledAt
      lineItems(first: 100) {
        nodes {
          sku
          quantity
          currentQuantity
          discountedTotalSet { shopMoney { amount currencyCode } }
        }
        pageInfo { hasNextPage endCursor }
      }
    }
    pageInfo { hasNextPage endCursor }
  }
}
"""

SHOPIFY_ORDER_LINE_ITEMS_QUERY = """
query OrderLineItems($id: ID!, $after: String) {
  order(id: $id) {
    lineItems(first: 100, after: $after) {
      nodes {
        sku
        quantity
        currentQuantity
        discountedTotalSet { shopMoney { amount currencyCode } }
      }
      pageInfo { hasNextPage endCursor }
    }
  }
}
"""


class IntegrationConfigurationError(ValueError):
    pass


class IntegrationRequestError(RuntimeError):
    pass


def _response_json(response: requests.Response, provider: str) -> Any:
    try:
        return response.json()
    except ValueError as exc:
        raise IntegrationRequestError(
            f"{provider} returned a response that was not valid JSON."
        ) from exc


def _settings() -> dict[str, str]:
    names = (
        "SHOPIFY_SHOP_DOMAIN",
        "SHOPIFY_ACCESS_TOKEN",
        "GOOGLE_ADS_DEVELOPER_TOKEN",
        "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_REFRESH_TOKEN",
        "GOOGLE_ADS_CUSTOMER_ID",
    )
    return {name: os.getenv(name, "").strip() for name in names}


def integration_status() -> dict[str, Any]:
    settings = _settings()
    missing = [name for name, value in settings.items() if not value]
    mapping_path = Path(os.getenv("GOOGLE_ADS_CAMPAIGN_SKU_MAP_PATH", DEFAULT_MAPPING_PATH))
    mapping_configured = mapping_path.is_file() or bool(
        os.getenv("GOOGLE_ADS_CAMPAIGN_SKU_MAP", "").strip()
    )
    if not mapping_configured:
        missing.append("GOOGLE_ADS_CAMPAIGN_SKU_MAP or GOOGLE_ADS_CAMPAIGN_SKU_MAP_PATH")

    metadata_path = Path(os.getenv("PROFITPILOT_LIVE_METADATA_PATH", DEFAULT_LIVE_METADATA_PATH))
    metadata: dict[str, Any] = {}
    if metadata_path.is_file():
        try:
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(f"Could not read live data sync metadata: {exc}") from exc
    if metadata.get("synced_at"):
        try:
            synced_at = datetime.fromisoformat(
                str(metadata["synced_at"]).replace("Z", "+00:00")
            )
            if synced_at.tzinfo is None:
                synced_at = synced_at.replace(tzinfo=timezone.utc)
            age_hours = max(
                0.0,
                (datetime.now(timezone.utc) - synced_at.astimezone(timezone.utc)).total_seconds()
                / 3600,
            )
            metadata["age_hours"] = round(age_hours, 1)
            metadata["stale"] = age_hours > 24
        except ValueError as exc:
            raise RuntimeError("Live data sync metadata has an invalid synced_at timestamp.") from exc

    return {
        "shopify_configured": bool(settings["SHOPIFY_SHOP_DOMAIN"] and settings["SHOPIFY_ACCESS_TOKEN"]),
        "google_ads_configured": all(
            settings[name]
            for name in (
                "GOOGLE_ADS_DEVELOPER_TOKEN",
                "GOOGLE_ADS_CLIENT_ID",
                "GOOGLE_ADS_CLIENT_SECRET",
                "GOOGLE_ADS_REFRESH_TOKEN",
                "GOOGLE_ADS_CUSTOMER_ID",
            )
        ),
        "campaign_sku_mapping_configured": mapping_configured,
        "ready_to_sync": not missing,
        "missing_configuration": missing,
        "live_data_active": os.getenv("PROFITPILOT_DATA_SOURCE", "sample").lower() == "live",
        "last_sync": metadata or None,
    }


def _request_with_retries(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json_body: dict[str, Any] | None = None,
    form_data: dict[str, str] | None = None,
) -> requests.Response:
    for attempt in range(3):
        try:
            response = requests.request(
                method,
                url,
                headers=headers,
                json=json_body,
                data=form_data,
                timeout=HTTP_TIMEOUT,
            )
        except requests.RequestException as exc:
            if attempt == 2:
                raise IntegrationRequestError(f"Could not reach integration provider: {exc}") from exc
            time.sleep(0.5 * (attempt + 1))
            continue

        if response.status_code == 429 or response.status_code >= 500:
            if attempt == 2:
                raise IntegrationRequestError(
                    f"Integration provider returned HTTP {response.status_code} after retries."
                )
            retry_after = response.headers.get("Retry-After", "")
            try:
                delay = min(max(float(retry_after), 0.0), 10.0)
            except ValueError:
                delay = 0.5 * (attempt + 1)
            time.sleep(delay)
            continue
        if not response.ok:
            raise IntegrationRequestError(
                f"Integration provider returned HTTP {response.status_code}: "
                f"{response.text[:500]}"
            )
        return response
    raise IntegrationRequestError("Integration provider request retries were exhausted.")


def _required_settings(*names: str) -> dict[str, str]:
    settings = _settings()
    missing = [name for name in names if not settings[name]]
    if missing:
        raise IntegrationConfigurationError(
            f"Missing required integration configuration: {', '.join(missing)}."
        )
    return settings


def fetch_shopify_products() -> dict[str, dict[str, Any]]:
    settings = _required_settings("SHOPIFY_SHOP_DOMAIN", "SHOPIFY_ACCESS_TOKEN")
    shop_domain = settings["SHOPIFY_SHOP_DOMAIN"].removeprefix("https://").rstrip("/")
    if "/" in shop_domain or not shop_domain:
        raise IntegrationConfigurationError("SHOPIFY_SHOP_DOMAIN must be a shop domain only.")
    version = os.getenv("SHOPIFY_API_VERSION", "2026-07").strip()
    if not version or "/" in version:
        raise IntegrationConfigurationError("SHOPIFY_API_VERSION must be a version identifier.")
    url = f"https://{shop_domain}/admin/api/{version}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": settings["SHOPIFY_ACCESS_TOKEN"],
        "Content-Type": "application/json",
    }

    products: dict[str, dict[str, Any]] = {}
    cursor = None
    while True:
        response = _request_with_retries(
            "POST",
            url,
            headers=headers,
            json_body={"query": SHOPIFY_VARIANTS_QUERY, "variables": {"after": cursor}},
        )
        payload = _response_json(response, "Shopify")
        if not isinstance(payload, dict):
            raise IntegrationRequestError("Shopify returned an unexpected response format.")
        if payload.get("errors"):
            raise IntegrationRequestError(
                f"Shopify GraphQL returned errors: {str(payload['errors'])[:500]}"
            )
        connection = payload.get("data", {}).get("productVariants", {})
        if not isinstance(connection, dict):
            raise IntegrationRequestError("Shopify response did not include product variant data.")
        shop_currency = str(
            (payload.get("data", {}).get("shop") or {}).get("currencyCode") or ""
        )
        for variant in connection.get("nodes", []):
            sku = str(variant.get("sku") or "").strip()
            if not sku:
                continue
            if sku in products:
                raise IntegrationConfigurationError(
                    f"Shopify contains multiple product variants with SKU {sku!r}; "
                    "resolve duplicate SKUs before syncing."
                )
            unit_cost = (variant.get("inventoryItem") or {}).get("unitCost") or {}
            products[sku] = {
                "sku_id": sku,
                "product_name": (variant.get("product") or {}).get("title") or sku,
                "product_category": (variant.get("product") or {}).get("productType") or "Uncategorized",
                "price": float(variant.get("price") or 0),
                "unit_cogs": float(unit_cost.get("amount") or 0),
                "stock_level": int(variant.get("inventoryQuantity") or 0),
                "currency_code": shop_currency,
                "unit_cost_currency": str(unit_cost.get("currencyCode") or ""),
            }
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            return products
        cursor = page_info.get("endCursor")
        if not cursor:
            raise IntegrationRequestError("Shopify pagination reported another page without a cursor.")


def fetch_shopify_orders(days: int) -> pd.DataFrame:
    if not 1 <= days <= 60:
        raise IntegrationConfigurationError(
            "Shopify order history sync is limited to 60 days."
        )
    settings = _required_settings("SHOPIFY_SHOP_DOMAIN", "SHOPIFY_ACCESS_TOKEN")
    shop_domain = settings["SHOPIFY_SHOP_DOMAIN"].removeprefix("https://").rstrip("/")
    if "/" in shop_domain or not shop_domain:
        raise IntegrationConfigurationError("SHOPIFY_SHOP_DOMAIN must be a shop domain only.")
    version = os.getenv("SHOPIFY_API_VERSION", "2026-07").strip()
    if not version or "/" in version:
        raise IntegrationConfigurationError("SHOPIFY_API_VERSION must be a version identifier.")
    url = f"https://{shop_domain}/admin/api/{version}/graphql.json"
    headers = {
        "X-Shopify-Access-Token": settings["SHOPIFY_ACCESS_TOKEN"],
        "Content-Type": "application/json",
    }
    end_date = date.today()
    start_date = end_date - timedelta(days=days - 1)
    search = (
        f"created_at:>={start_date.isoformat()} "
        f"created_at:<{(end_date + timedelta(days=1)).isoformat()} status:any"
    )
    cursor = None
    line_records: list[dict[str, Any]] = []
    order_ids: set[str] = set()
    while True:
        response = _request_with_retries(
            "POST",
            url,
            headers=headers,
            json_body={
                "query": SHOPIFY_ORDERS_QUERY,
                "variables": {"after": cursor, "search": search},
            },
        )
        payload = _response_json(response, "Shopify")
        if not isinstance(payload, dict):
            raise IntegrationRequestError("Shopify returned an unexpected order response format.")
        if payload.get("errors"):
            raise IntegrationRequestError(
                f"Shopify orders query returned errors: {str(payload['errors'])[:500]}"
            )
        connection = payload.get("data", {}).get("orders", {})
        if not isinstance(connection, dict):
            raise IntegrationRequestError("Shopify response did not include order data.")
        for order in connection.get("nodes", []):
            if order.get("cancelledAt"):
                continue
            order_id = str(order.get("id") or "")
            created_at = order.get("createdAt")
            if not order_id or not created_at:
                raise IntegrationRequestError(
                    "Shopify returned an order without its ID or creation timestamp."
                )
            line_items = order.get("lineItems") or {}
            item_nodes = list(line_items.get("nodes") or [])
            item_page = line_items.get("pageInfo") or {}
            item_cursor = item_page.get("endCursor")
            while item_page.get("hasNextPage"):
                if not item_cursor:
                    raise IntegrationRequestError(
                        "Shopify order line-item pagination omitted its cursor."
                    )
                item_response = _request_with_retries(
                    "POST",
                    url,
                    headers=headers,
                    json_body={
                        "query": SHOPIFY_ORDER_LINE_ITEMS_QUERY,
                        "variables": {"id": order_id, "after": item_cursor},
                    },
                )
                item_payload = _response_json(item_response, "Shopify")
                if not isinstance(item_payload, dict) or item_payload.get("errors"):
                    raise IntegrationRequestError(
                        "Shopify could not return all line items for an order."
                    )
                item_connection = (
                    item_payload.get("data", {}).get("order", {}).get("lineItems", {})
                )
                if not isinstance(item_connection, dict):
                    raise IntegrationRequestError(
                        "Shopify order line-item page had an invalid response format."
                    )
                item_nodes.extend(item_connection.get("nodes") or [])
                item_page = item_connection.get("pageInfo") or {}
                item_cursor = item_page.get("endCursor")

            order_date = pd.to_datetime(created_at, utc=True).date().isoformat()
            for item in item_nodes:
                sku = str(item.get("sku") or "").strip()
                quantity = int(item.get("quantity") or 0)
                current_quantity = int(
                    item.get("currentQuantity")
                    if item.get("currentQuantity") is not None
                    else quantity
                )
                if not sku or quantity <= 0 or current_quantity <= 0:
                    continue
                price_set = (item.get("discountedTotalSet") or {}).get("shopMoney") or {}
                currency_code = str(
                    price_set.get("currencyCode") or order.get("currencyCode") or ""
                )
                if not currency_code:
                    raise IntegrationConfigurationError(
                        "Shopify order line items must include their store currency."
                    )
                line_revenue = float(price_set.get("amount") or 0)
                line_records.append({
                    "date": order_date,
                    "sku_id": sku,
                    "order_id": order_id,
                    "units_sold": current_quantity,
                    "sales_revenue": line_revenue * current_quantity / quantity,
                    "currency_code": currency_code,
                })
                order_ids.add(order_id)
        page_info = connection.get("pageInfo") or {}
        if not page_info.get("hasNextPage"):
            break
        cursor = page_info.get("endCursor")
        if not cursor:
            raise IntegrationRequestError("Shopify order pagination omitted its cursor.")

    if not line_records:
        orders = pd.DataFrame(
            columns=["date", "sku_id", "order_count", "units_sold", "sales_revenue", "currency_code"]
        )
        orders.attrs["total_order_count"] = 0
        return orders
    lines = pd.DataFrame(line_records)
    currencies = set(lines["currency_code"].unique())
    if len(currencies) != 1:
        raise IntegrationConfigurationError(
            "Shopify orders include multiple currencies; configure a single store currency."
        )
    orders = (
        lines.groupby(["date", "sku_id", "currency_code"], as_index=False)
        .agg(
            order_count=("order_id", "nunique"),
            units_sold=("units_sold", "sum"),
            sales_revenue=("sales_revenue", "sum"),
        )
    )
    orders.attrs["total_order_count"] = len(order_ids)
    return orders


def _google_ads_access_token(settings: dict[str, str]) -> str:
    response = _request_with_retries(
        "POST",
        "https://oauth2.googleapis.com/token",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        form_data={
            "client_id": settings["GOOGLE_ADS_CLIENT_ID"],
            "client_secret": settings["GOOGLE_ADS_CLIENT_SECRET"],
            "refresh_token": settings["GOOGLE_ADS_REFRESH_TOKEN"],
            "grant_type": "refresh_token",
        },
    )
    payload = _response_json(response, "Google OAuth")
    if not isinstance(payload, dict):
        raise IntegrationRequestError("Google OAuth returned an unexpected response format.")
    access_token = payload.get("access_token")
    if not isinstance(access_token, str) or not access_token:
        raise IntegrationRequestError("Google OAuth response did not include an access token.")
    return access_token


def fetch_google_ads_campaigns(days: int) -> list[dict[str, Any]]:
    settings = _required_settings(
        "GOOGLE_ADS_DEVELOPER_TOKEN",
        "GOOGLE_ADS_CLIENT_ID",
        "GOOGLE_ADS_CLIENT_SECRET",
        "GOOGLE_ADS_REFRESH_TOKEN",
        "GOOGLE_ADS_CUSTOMER_ID",
    )
    customer_id = settings["GOOGLE_ADS_CUSTOMER_ID"].replace("-", "")
    if not customer_id.isdigit():
        raise IntegrationConfigurationError("GOOGLE_ADS_CUSTOMER_ID must contain digits only.")
    api_version = os.getenv("GOOGLE_ADS_API_VERSION", "v25").strip()
    if not api_version.startswith("v") or not api_version[1:].isdigit():
        raise IntegrationConfigurationError("GOOGLE_ADS_API_VERSION must use a format such as v25.")
    access_token = _google_ads_access_token(settings)
    headers = {
        "Authorization": f"Bearer {access_token}",
        "developer-token": settings["GOOGLE_ADS_DEVELOPER_TOKEN"],
        "Content-Type": "application/json",
    }
    login_customer_id = os.getenv("GOOGLE_ADS_LOGIN_CUSTOMER_ID", "").replace("-", "").strip()
    if login_customer_id:
        headers["login-customer-id"] = login_customer_id

    end_date = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=days - 1)
    query = f"""
      SELECT segments.date, campaign.id, campaign.name,
        campaign.advertising_channel_type, customer.currency_code,
        metrics.cost_micros,
        metrics.conversions, metrics.conversions_value, metrics.clicks,
        metrics.impressions
      FROM campaign
      WHERE segments.date BETWEEN '{start_date.isoformat()}' AND '{end_date.isoformat()}'
        AND campaign.status != 'REMOVED'
    """
    url = (
        f"https://googleads.googleapis.com/{api_version}/customers/"
        f"{customer_id}/googleAds:searchStream"
    )
    response = _request_with_retries(
        "POST",
        url,
        headers=headers,
        json_body={"query": query},
    )
    payload = _response_json(response, "Google Ads")
    if not isinstance(payload, list):
        raise IntegrationRequestError("Google Ads searchStream response was not a result stream.")
    rows = []
    for batch in payload:
        for result in batch.get("results", []):
            metrics = result.get("metrics") or {}
            campaign = result.get("campaign") or {}
            segments = result.get("segments") or {}
            rows.append({
                "date": segments.get("date"),
                "campaign_id": str(campaign.get("id") or ""),
                "campaign_name": campaign.get("name") or "",
                "channel": campaign.get("advertisingChannelType") or "UNKNOWN",
                "currency_code": str((result.get("customer") or {}).get("currencyCode") or ""),
                "ad_spend": float(metrics.get("costMicros") or 0) / 1_000_000,
                "conversions": float(metrics.get("conversions") or 0),
                "sales_revenue": float(metrics.get("conversionsValue") or 0),
                "estimated_clicks": int(metrics.get("clicks") or 0),
                "impressions": int(metrics.get("impressions") or 0),
            })
    if not rows:
        raise IntegrationRequestError(
            "Google Ads returned no campaign metrics for the selected period."
        )
    return rows


def _campaign_sku_mapping() -> dict[str, str]:
    raw_mapping = os.getenv("GOOGLE_ADS_CAMPAIGN_SKU_MAP", "").strip()
    if raw_mapping:
        try:
            mapping = json.loads(raw_mapping)
        except json.JSONDecodeError as exc:
            raise IntegrationConfigurationError(
                "GOOGLE_ADS_CAMPAIGN_SKU_MAP must be a JSON object of campaign IDs to SKUs."
            ) from exc
    else:
        path = Path(os.getenv("GOOGLE_ADS_CAMPAIGN_SKU_MAP_PATH", DEFAULT_MAPPING_PATH))
        if not path.is_file():
            raise IntegrationConfigurationError(
                "Create data/campaign_sku_mapping.json to map each Google Ads campaign ID "
                "to exactly one Shopify SKU."
            )
        try:
            mapping = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise IntegrationConfigurationError(
                f"Could not read campaign-to-SKU mapping file: {exc}"
            ) from exc
    if not isinstance(mapping, dict) or not mapping:
        raise IntegrationConfigurationError("Campaign-to-SKU mapping must be a non-empty JSON object.")
    normalized = {str(key).strip(): str(value).strip() for key, value in mapping.items()}
    if any(not key or not value for key, value in normalized.items()):
        raise IntegrationConfigurationError("Campaign IDs and mapped SKU values cannot be empty.")
    return normalized


def build_live_campaign_rows(
    campaigns: list[dict[str, Any]],
    products: dict[str, dict[str, Any]],
    mapping: dict[str, str],
) -> pd.DataFrame:
    campaign_ids = {str(campaign["campaign_id"]) for campaign in campaigns}
    unmapped = sorted(campaign_ids - mapping.keys())
    if unmapped:
        raise IntegrationConfigurationError(
            "Add explicit Shopify SKU mappings for Google Ads campaign IDs: "
            + ", ".join(unmapped[:20])
        )
    missing_products = sorted({
        mapping[campaign_id]
        for campaign_id in campaign_ids
        if mapping[campaign_id] not in products
    })
    if missing_products:
        raise IntegrationConfigurationError(
            "Mapped SKUs were not found in the Shopify product catalog: "
            + ", ".join(missing_products[:20])
        )
    currency_pairs = {
        (
            str(campaign.get("currency_code") or ""),
            str(products[mapping[str(campaign["campaign_id"])]].get("currency_code") or ""),
            str(products[mapping[str(campaign["campaign_id"])]].get("unit_cost_currency") or ""),
        )
        for campaign in campaigns
    }
    if any(not ads_currency or not shop_currency or not cost_currency for ads_currency, shop_currency, cost_currency in currency_pairs):
        raise IntegrationConfigurationError(
            "Google Ads, Shopify, and Shopify product-cost currency codes must all be available."
        )
    if any(len({ads_currency, shop_currency, cost_currency}) != 1 for ads_currency, shop_currency, cost_currency in currency_pairs):
        raise IntegrationConfigurationError(
            "Google Ads account currency, Shopify store currency, and Shopify unit-cost currency must match. "
            "Automatic currency conversion is not supported."
        )

    rows: list[dict[str, Any]] = []
    for campaign in campaigns:
        campaign_id = str(campaign["campaign_id"])
        product = products[mapping[campaign_id]]
        unit_cost = float(product["unit_cogs"])
        unit_price = float(product["price"])
        if unit_price <= 0:
            raise IntegrationConfigurationError(
                f"Shopify SKU {product['sku_id']} must have a positive price to estimate COGS."
            )
        revenue = float(campaign["sales_revenue"])
        conversions = float(campaign["conversions"])
        rows.append({
            "date": pd.to_datetime(campaign["date"], errors="raise"),
            "region": "All",
            "channel": str(campaign.get("channel") or "UNKNOWN"),
            "product_category": product["product_category"],
            "customer_segment": "All",
            "ad_spend": float(campaign["ad_spend"]),
            "price": unit_price,
            "discount_rate": 0.0,
            "market_reach": 0,
            "impressions": int(campaign["impressions"]),
            "click_through_rate": (
                int(campaign["estimated_clicks"]) / int(campaign["impressions"])
                if int(campaign["impressions"]) > 0 else 0.0
            ),
            "competition_index": 0.0,
            "seasonality_index": 0.0,
            "campaign_duration_days": 0,
            "customer_lifetime_value": 0.0,
            "estimated_clicks": int(campaign["estimated_clicks"]),
            "effective_price": unit_price,
            "sku_id": product["sku_id"],
            "ad_platform": "Google Ads",
            "campaign_id": campaign_id,
            "unit_cogs": unit_cost,
            "conversions": conversions,
            "units_sold": conversions,
            "sales_revenue": revenue,
            "total_cogs": conversions * unit_cost,
            "gross_profit": revenue - conversions * unit_cost,
            "contribution_profit": revenue - conversions * unit_cost - float(campaign["ad_spend"]),
            "net_profit": revenue - conversions * unit_cost - float(campaign["ad_spend"]),
            "profit_margin_pct": (unit_price - unit_cost) / unit_price,
            "stock_level": product["stock_level"],
        })
    return pd.DataFrame(rows)


def load_shopify_orders_snapshot() -> pd.DataFrame:
    path = Path(os.getenv("PROFITPILOT_LIVE_ORDERS_PATH", DEFAULT_LIVE_ORDERS_PATH))
    if not path.is_file():
        raise FileNotFoundError(f"No synchronized Shopify order snapshot exists at {path}.")
    return pd.read_csv(path, parse_dates=["date"])


def sync_integrations(days: int) -> dict[str, Any]:
    if not 1 <= days <= 60:
        raise IntegrationConfigurationError(
            "The sync window must be between 1 and 60 days because Shopify order history is limited."
        )
    mapping = _campaign_sku_mapping()
    products = fetch_shopify_products()
    shopify_orders = fetch_shopify_orders(days)
    campaigns = fetch_google_ads_campaigns(days)
    live_data = build_live_campaign_rows(campaigns, products, mapping)
    if not shopify_orders.empty:
        product_currencies = {
            str(product.get("currency_code") or "") for product in products.values()
        }
        if set(shopify_orders["currency_code"].unique()) != product_currencies:
            raise IntegrationConfigurationError(
                "Shopify order currency and Shopify product currency must match."
            )

    data_path = Path(os.getenv("PROFITPILOT_LIVE_DATA_PATH", DEFAULT_LIVE_DATA_PATH))
    metadata_path = Path(os.getenv("PROFITPILOT_LIVE_METADATA_PATH", DEFAULT_LIVE_METADATA_PATH))
    orders_path = Path(os.getenv("PROFITPILOT_LIVE_ORDERS_PATH", DEFAULT_LIVE_ORDERS_PATH))
    data_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    orders_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_paths = []
    sync_time = datetime.now(timezone.utc)
    metadata = {
        "synced_at": sync_time.isoformat(),
        "window_days": days,
        "record_count": len(live_data),
        "campaign_count": int(live_data["campaign_id"].nunique()),
        "sku_count": int(live_data["sku_id"].nunique()),
        "date_start": live_data["date"].min().date().isoformat(),
        "date_end": live_data["date"].max().date().isoformat(),
        "shopify_order_count": (
            int(shopify_orders.attrs.get("total_order_count", 0))
        ),
        "shopify_units_sold": (
            int(shopify_orders["units_sold"].sum()) if not shopify_orders.empty else 0
        ),
        "shopify_sales_revenue": (
            round(float(shopify_orders["sales_revenue"].sum()), 2)
            if not shopify_orders.empty else 0.0
        ),
        "currency_code": next(iter(shopify_orders["currency_code"].unique()))
        if not shopify_orders.empty else next(iter(products.values()))["currency_code"],
        "notes": (
            "Revenue and conversions are Google Ads attributed metrics. "
            "COGS uses current Shopify unit cost multiplied by attributed conversions; "
            "it is an estimate, not order-level attribution. Shopify order revenue is "
            "reported separately and is not added to Google Ads campaign revenue."
        ),
    }
    try:
        for destination, writer in (
            (data_path, lambda path: live_data.to_csv(path, index=False)),
            (orders_path, lambda path: shopify_orders.to_csv(path, index=False)),
            (
                metadata_path,
                lambda path: path.write_text(
                    json.dumps(metadata, indent=2) + "\n",
                    encoding="utf-8",
                ),
            ),
        ):
            with tempfile.NamedTemporaryFile(
                "w",
                encoding="utf-8",
                dir=destination.parent,
                delete=False,
                suffix=destination.suffix,
            ) as temporary:
                temp_path = Path(temporary.name)
            temporary_paths.append((temp_path, destination))
            writer(temp_path)
        for temporary_path, destination in temporary_paths:
            os.replace(temporary_path, destination)
    except Exception as exc:
        for temporary_path, _ in temporary_paths:
            temporary_path.unlink(missing_ok=True)
        if isinstance(exc, OSError):
            raise RuntimeError(f"Could not persist the live integration snapshot: {exc}") from exc
        raise

    os.environ["PROFITPILOT_DATA_SOURCE"] = "live"
    return metadata
