import unittest

import pandas as pd

from src.anomaly import detect_anomalies, forecast_campaign_anomalies


def make_campaign_history():
    rows = []
    for campaign_number in range(8):
        for observation in range(30):
            roas = 2.2 if (observation // 2) % 2 == 0 else 0.55
            if campaign_number % 2:
                roas = 0.55 if (observation // 2) % 2 == 0 else 2.2
            spend = 100 + campaign_number * 4
            revenue = spend * roas
            conversions = 10 if roas > 1 else 2
            rows.append({
                "date": pd.Timestamp("2024-01-01") + pd.Timedelta(days=observation),
                "ad_platform": "Search" if campaign_number % 2 else "Social",
                "campaign_id": f"campaign-{campaign_number}",
                "ad_spend": spend,
                "sales_revenue": revenue,
                "total_cogs": revenue * 0.4,
                "conversions": conversions,
                "estimated_clicks": 100,
                "impressions": 1000,
                "profit_margin_pct": 0.6,
                "stock_level": 180 - observation,
                "discount_rate": 0.1,
                "competition_index": 3.0,
            })
    return pd.DataFrame(rows)


class AnomalyModelTests(unittest.TestCase):
    def test_multivariate_detector_returns_ranked_evidence(self):
        scored = detect_anomalies(make_campaign_history())

        self.assertEqual(len(scored), 8 * 30)
        self.assertTrue(scored["anomaly_risk_score"].between(0, 100).all())
        self.assertTrue(scored["is_anomaly"].any())
        self.assertTrue(scored.loc[scored["is_anomaly"], "likely_drivers"].notna().all())

    def test_forecast_predicts_next_campaign_observation_and_evaluates_holdout(self):
        result = forecast_campaign_anomalies(make_campaign_history())

        self.assertTrue(result["trained"])
        self.assertEqual(len(result["predictions"]), 8)
        self.assertIn("balanced_accuracy", result["evaluation"])
        self.assertIn("next recorded campaign observation", result["horizon"])
        self.assertTrue(
            all(0 <= row["risk_score"] <= 100 for row in result["predictions"])
        )


if __name__ == "__main__":
    unittest.main()
