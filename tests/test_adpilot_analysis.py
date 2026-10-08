import unittest
from types import SimpleNamespace
from unittest.mock import patch

from src.adpilot.analysis import (
    decompose_roas,
    fit_response_curve,
    optimize_budget,
    robust_anomaly_scores,
)


class AdPilotAnalysisTests(unittest.TestCase):
    def test_robust_score_ranks_a_large_outlier_above_baseline(self):
        scores = robust_anomaly_scores([10, 10, 11, 9, 10, 100])

        self.assertEqual(len(scores), 6)
        self.assertGreater(scores[-1], max(scores[:-1]))

    def test_roas_decomposition_reports_measured_rate_changes(self):
        result = decompose_roas(
            {"ctr": 0.02, "cvr": 0.04, "aov": 80, "cpc": 1.5},
            {"ctr": 0.01, "cvr": 0.05, "aov": 75, "cpc": 1.0},
        )

        self.assertEqual(set(result["components"]), {"ctr", "cvr", "aov", "cpc"})
        self.assertNotEqual(result["total_change_pct"], 0)

    def test_low_inventory_throttle_overrides_incompatible_shift_floor(self):
        result = optimize_budget(
            [
                {
                    "campaign_id": "stockout",
                    "name": "Stockout campaign",
                    "spend": 1000,
                    "profit": 900,
                    "roas": 3.0,
                    "inventory_cover_days": 2,
                },
                {
                    "campaign_id": "healthy",
                    "name": "Healthy campaign",
                    "spend": 1000,
                    "profit": 700,
                    "roas": 2.5,
                    "inventory_cover_days": 20,
                },
            ],
            total_budget=2400,
            max_shift_pct=20,
            target_roas=None,
        )

        stockout = next(
            row for row in result["allocations"] if row["campaign_id"] == "stockout"
        )
        self.assertTrue(stockout["inventory_guardrail_applied"])
        self.assertLessEqual(stockout["optimized_spend"], 550)
        self.assertTrue(result["converged"])

    def test_fitted_revenue_curve_is_used_and_profit_includes_cogs(self):
        result = optimize_budget(
            [
                {
                    "campaign_id": "campaign",
                    "name": "Campaign",
                    "daily_budget": 100,
                    "roas": 2,
                    "revenue_curve": {"scale": 400, "half_saturation": 100},
                    "cogs_rate": 0.4,
                    "inventory_cover_days": 20,
                }
            ],
            total_budget=100,
            max_shift_pct=0,
            target_roas=2,
        )

        self.assertTrue(result["converged"])
        self.assertAlmostEqual(result["predicted_revenue"], 200)
        self.assertAlmostEqual(result["predicted_profit"], 20)
        self.assertAlmostEqual(result["predicted_roas"], 2)
        self.assertTrue(result["target_roas_met"])

    def test_response_curve_fits_observed_spend_and_revenue(self):
        points = [
            {"spend": spend, "value": 1000 * spend / (500 + spend)}
            for spend in (100, 200, 500, 1000, 2000)
        ]

        curve = fit_response_curve(points)

        self.assertAlmostEqual(curve["scale"], 1000, delta=1)
        self.assertAlmostEqual(curve["half_saturation"], 500, delta=1)
        self.assertAlmostEqual(curve["r_squared"], 1, places=3)

    def test_response_curve_falls_back_when_solver_does_not_converge(self):
        points = [
            {"spend": spend, "value": 1000 * spend / (500 + spend)}
            for spend in (100, 200, 500, 1000, 2000)
        ]

        with patch(
            "src.adpilot.analysis.minimize",
            return_value=SimpleNamespace(success=False, message="ABNORMAL", x=[0, 0]),
        ):
            curve = fit_response_curve(points)

        self.assertAlmostEqual(curve["scale"], 1000, delta=5)
        self.assertAlmostEqual(curve["half_saturation"], 500, delta=10)
        self.assertGreater(curve["r_squared"], 0.999)

    def test_budget_is_adjusted_to_respect_campaign_bounds(self):
        result = optimize_budget(
            [
                {
                    "campaign_id": "campaign",
                    "name": "Campaign",
                    "daily_budget": 100,
                    "roas": 2,
                    "revenue_curve": {"scale": 400, "half_saturation": 100},
                    "cogs_rate": 0.2,
                    "inventory_cover_days": 20,
                }
            ],
            total_budget=150,
            max_shift_pct=20,
            target_roas=None,
        )

        self.assertAlmostEqual(result["budget_optimized"], 120)
        self.assertTrue(result["budget_adjusted"])


if __name__ == "__main__":
    unittest.main()
