import unittest

from src.adpilot.analysis import (
    decompose_roas,
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


if __name__ == "__main__":
    unittest.main()
