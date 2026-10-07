import json
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

import pandas as pd

from src.integrations import (
    IntegrationConfigurationError,
    build_live_campaign_rows,
    fetch_google_ads_campaigns,
    fetch_shopify_orders,
    fetch_shopify_products,
    integration_status,
    sync_integrations,
)
from src.metrics import load_data


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.settings = {
            "SHOPIFY_SHOP_DOMAIN": "example-shop.myshopify.com",
            "SHOPIFY_ACCESS_TOKEN": "shopify-test-token",
            "GOOGLE_ADS_DEVELOPER_TOKEN": "google-test-developer-token",
            "GOOGLE_ADS_CLIENT_ID": "google-test-client",
            "GOOGLE_ADS_CLIENT_SECRET": "google-test-secret",
            "GOOGLE_ADS_REFRESH_TOKEN": "google-test-refresh",
            "GOOGLE_ADS_CUSTOMER_ID": "123-456-7890",
        }

    def test_integration_status_reports_setup_without_exposing_secrets(self):
        with patch.dict(os.environ, self.settings, clear=True):
            status = integration_status()

        self.assertTrue(status["shopify_configured"])
        self.assertTrue(status["google_ads_configured"])
        self.assertFalse(status["ready_to_sync"])
        self.assertNotIn("google-test-secret", json.dumps(status))

    def test_shopify_fetch_uses_pagination_and_keeps_cost_inventory_fields(self):
        first_response = Mock()
        first_response.json.return_value = {
            "data": {
                "shop": {"currencyCode": "USD"},
                "productVariants": {
                    "nodes": [{
                        "sku": "SKU-1",
                        "price": "25.00",
                        "inventoryQuantity": 8,
                        "product": {"title": "Bottle", "productType": "Drinkware"},
                        "inventoryItem": {
                            "unitCost": {"amount": "7.50", "currencyCode": "USD"}
                        },
                    }],
                    "pageInfo": {"hasNextPage": True, "endCursor": "cursor-1"},
                }
            }
        }
        second_response = Mock()
        second_response.json.return_value = {
            "data": {
                "shop": {"currencyCode": "USD"},
                "productVariants": {
                    "nodes": [],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }
        with patch.dict(os.environ, self.settings, clear=True), patch(
            "src.integrations._request_with_retries",
            side_effect=[first_response, second_response],
        ) as request:
            products = fetch_shopify_products()

        self.assertEqual(products["SKU-1"]["unit_cogs"], 7.5)
        self.assertEqual(products["SKU-1"]["stock_level"], 8)
        self.assertEqual(request.call_count, 2)
        self.assertEqual(
            request.call_args_list[1].kwargs["json_body"]["variables"]["after"],
            "cursor-1",
        )

    def test_google_ads_fetch_refreshes_auth_and_normalizes_micros(self):
        oauth_response = Mock()
        oauth_response.ok = True
        oauth_response.status_code = 200
        oauth_response.headers = {}
        oauth_response.json.return_value = {"access_token": "short-lived-test-token"}
        ads_response = Mock()
        ads_response.ok = True
        ads_response.status_code = 200
        ads_response.headers = {}
        ads_response.json.return_value = [{
            "results": [{
                "segments": {"date": "2026-10-01"},
                "customer": {"currencyCode": "USD"},
                "campaign": {
                    "id": "42",
                    "name": "Search SKU 1",
                    "advertisingChannelType": "SEARCH",
                },
                "metrics": {
                    "costMicros": "1234567",
                    "conversions": 2.0,
                    "conversionsValue": 50.0,
                    "clicks": "30",
                    "impressions": "300",
                },
            }]
        }]
        with patch.dict(os.environ, self.settings, clear=True), patch(
            "src.integrations.requests.request",
            side_effect=[oauth_response, ads_response],
        ) as request:
            campaigns = fetch_google_ads_campaigns(14)

        self.assertAlmostEqual(campaigns[0]["ad_spend"], 1.234567)
        self.assertEqual(campaigns[0]["sales_revenue"], 50)
        self.assertEqual(campaigns[0]["campaign_id"], "42")
        self.assertEqual(request.call_args_list[0].kwargs["data"]["grant_type"], "refresh_token")
        self.assertEqual(request.call_args_list[1].kwargs["headers"]["developer-token"], self.settings["GOOGLE_ADS_DEVELOPER_TOKEN"])

    def test_shopify_orders_are_aggregated_without_order_identifiers(self):
        response = Mock()
        response.json.return_value = {
            "data": {
                "orders": {
                    "nodes": [{
                        "id": "gid://shopify/Order/private-id",
                        "createdAt": "2026-10-01T12:00:00Z",
                        "currencyCode": "USD",
                        "cancelledAt": None,
                        "lineItems": {
                            "nodes": [{
                                "sku": "SKU-1",
                                "quantity": 2,
                                "currentQuantity": 2,
                                "discountedTotalSet": {
                                    "shopMoney": {
                                        "amount": "50.00",
                                        "currencyCode": "USD",
                                    }
                                },
                            }],
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                        },
                    }],
                    "pageInfo": {"hasNextPage": False, "endCursor": None},
                }
            }
        }
        with patch.dict(os.environ, self.settings, clear=True), patch(
            "src.integrations._request_with_retries",
            return_value=response,
        ):
            orders = fetch_shopify_orders(30)

        self.assertEqual(orders.loc[0, "sku_id"], "SKU-1")
        self.assertEqual(orders.loc[0, "units_sold"], 2)
        self.assertEqual(orders.loc[0, "sales_revenue"], 50)
        self.assertEqual(orders.attrs["total_order_count"], 1)
        self.assertNotIn("order_id", orders.columns)

    def test_campaign_mapping_is_required_and_never_guessed(self):
        campaign = {
            "campaign_id": "42",
            "date": "2026-10-01",
            "ad_spend": 10,
            "conversions": 2,
            "sales_revenue": 50,
            "estimated_clicks": 30,
            "impressions": 300,
            "channel": "SEARCH",
            "currency_code": "USD",
        }
        product = {
            "SKU-1": {
                "sku_id": "SKU-1",
                "product_category": "Drinkware",
                "price": 25,
                "unit_cogs": 7.5,
                "stock_level": 8,
                "currency_code": "USD",
                "unit_cost_currency": "USD",
            }
        }
        with self.assertRaises(IntegrationConfigurationError):
            build_live_campaign_rows([campaign], product, {})

        result = build_live_campaign_rows([campaign], product, {"42": "SKU-1"})
        self.assertEqual(result.loc[0, "sku_id"], "SKU-1")
        self.assertEqual(result.loc[0, "total_cogs"], 15)
        self.assertEqual(result.loc[0, "contribution_profit"], 25)
        self.assertIsInstance(result.loc[0, "date"], pd.Timestamp)

    def test_currency_mismatch_blocks_live_profit_data(self):
        campaign = {
            "campaign_id": "42",
            "date": "2026-10-01",
            "ad_spend": 10,
            "conversions": 2,
            "sales_revenue": 50,
            "estimated_clicks": 30,
            "impressions": 300,
            "currency_code": "USD",
        }
        product = {
            "SKU-1": {
                "sku_id": "SKU-1",
                "product_category": "Drinkware",
                "price": 25,
                "unit_cogs": 7.5,
                "stock_level": 8,
                "currency_code": "CAD",
                "unit_cost_currency": "CAD",
            }
        }
        with self.assertRaisesRegex(IntegrationConfigurationError, "must match"):
            build_live_campaign_rows([campaign], product, {"42": "SKU-1"})

    def test_live_mode_fails_instead_of_falling_back_to_sample(self):
        with TemporaryDirectory() as temp_dir:
            with patch.dict(os.environ, {"PROFITPILOT_DATA_SOURCE": "live"}), patch(
                "src.metrics.LIVE_DATA_PATH", Path(temp_dir) / "missing.csv"
            ):
                with self.assertRaisesRegex(RuntimeError, "no synchronized snapshot"):
                    load_data()

    def test_sync_persists_snapshot_and_activates_live_data(self):
        campaign_rows = [{
            "campaign_id": "42",
            "date": "2026-10-01",
            "ad_spend": 10,
            "conversions": 2,
            "sales_revenue": 50,
            "estimated_clicks": 30,
            "impressions": 300,
            "channel": "SEARCH",
            "currency_code": "USD",
        }]
        products = {
            "SKU-1": {
                "sku_id": "SKU-1",
                "product_category": "Drinkware",
                "price": 25,
                "unit_cogs": 7.5,
                "stock_level": 8,
                "currency_code": "USD",
                "unit_cost_currency": "USD",
            }
        }
        shopify_orders = pd.DataFrame([{
            "date": "2026-10-01",
            "sku_id": "SKU-1",
            "order_count": 1,
            "units_sold": 2,
            "sales_revenue": 50,
            "currency_code": "USD",
        }])
        shopify_orders.attrs["total_order_count"] = 1
        with TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            mapping_path = root / "mapping.json"
            mapping_path.write_text(json.dumps({"42": "SKU-1"}), encoding="utf-8")
            with patch.dict(os.environ, {
                "GOOGLE_ADS_CAMPAIGN_SKU_MAP_PATH": str(mapping_path),
                "PROFITPILOT_LIVE_DATA_PATH": str(root / "live.csv"),
                "PROFITPILOT_LIVE_METADATA_PATH": str(root / "live.json"),
                "PROFITPILOT_LIVE_ORDERS_PATH": str(root / "live_shopify_orders.csv"),
                "PROFITPILOT_DATA_SOURCE": "sample",
            }), patch(
                "src.integrations.fetch_shopify_products", return_value=products
            ), patch(
                "src.integrations.fetch_shopify_orders", return_value=shopify_orders
            ), patch(
                "src.integrations.fetch_google_ads_campaigns", return_value=campaign_rows
            ):
                metadata = sync_integrations(30)
                active_source = os.environ["PROFITPILOT_DATA_SOURCE"]

            saved = pd.read_csv(root / "live.csv")
            saved_metadata = json.loads((root / "live.json").read_text(encoding="utf-8"))
            saved_orders = pd.read_csv(root / "live_shopify_orders.csv")

        self.assertEqual(metadata["record_count"], 1)
        self.assertEqual(saved_metadata["window_days"], 30)
        self.assertEqual(saved.loc[0, "campaign_id"], 42)
        self.assertEqual(saved_metadata["shopify_order_count"], 1)
        self.assertEqual(saved_orders.loc[0, "sku_id"], "SKU-1")
        self.assertEqual(active_source, "live")
        self.assertEqual(os.getenv("PROFITPILOT_DATA_SOURCE", "sample"), "sample")


if __name__ == "__main__":
    unittest.main()
