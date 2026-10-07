import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import api
from api import DecisionOutcome, ExecutionRequest
from src.decision_learning import (
    predict_decision,
    train_decision_model,
    train_outcome_model,
)


class DecisionLearningTests(unittest.TestCase):
    def setUp(self):
        self.records = [
            {
                "human_decision": "Approve",
                "proposed_action": "Increase budget",
                "model_features": {
                    "action": "Increase budget",
                    "ad_platform": "Search",
                    "decision_priority": "Scale profitable growth",
                    "contribution_profit": 900,
                    "avg_margin_pct": 0.48,
                    "avg_inventory": 800,
                },
            }
            for _ in range(5)
        ] + [
            {
                "human_decision": "Reject",
                "proposed_action": "Reduce budget",
                "model_features": {
                    "action": "Reduce budget",
                    "ad_platform": "Social",
                    "decision_priority": "Stop waste",
                    "contribution_profit": -700,
                    "avg_margin_pct": 0.12,
                    "avg_inventory": 70,
                },
            }
        ]

    def test_waits_for_sufficient_labeled_history(self):
        status = train_decision_model(self.records[:5])

        self.assertFalse(status["ready"])
        self.assertEqual(status["training_examples"], 5)
        self.assertIsNone(predict_decision(status, self.records[0]))

    def test_trains_and_predicts_reviewer_preference(self):
        model = train_decision_model(self.records)
        prediction = predict_decision(model, self.records[0])

        self.assertTrue(model["ready"])
        self.assertEqual(prediction["predicted_decision"], "Approve")
        self.assertEqual(prediction["training_examples"], 6)
        self.assertAlmostEqual(sum(prediction["probabilities"].values()), 1.0)

    def test_requires_more_than_one_decision_class(self):
        one_class = [dict(record, human_decision="Approve") for record in self.records]

        status = train_decision_model(one_class)

        self.assertFalse(status["ready"])
        self.assertIn("two different reviewer choices", status["message"])

    def test_api_persists_decisions_and_reports_learning_progress(self):
        with TemporaryDirectory() as temp_dir:
            decision_file = Path(temp_dir) / "decision_log.json"
            with patch.object(api, "DECISIONS_PATH", decision_file):
                for record in self.records:
                    saved = api.save_decision({
                        "timestamp": "2026-10-07T15:00:00",
                        "proposed_action": record["proposed_action"],
                        "budget_change_pct": -20,
                        "human_decision": record["human_decision"],
                        "decision_source": "human_in_the_loop",
                        "model_features": record["model_features"],
                    })
                    self.assertTrue(saved["decision"]["decision_id"])

                status = api.get_learning_status()
                saved_records = api.list_decisions()

            self.assertTrue(status["ready"])
            self.assertEqual(status["training_examples"], 6)
            self.assertEqual(len(saved_records), 6)
            self.assertTrue(decision_file.exists())

    def test_outcome_classifier_trains_only_on_measured_results(self):
        records = [
            dict(
                record,
                outcome={
                    "profitability_label": (
                        "Profitable" if index < 15 else "Unprofitable"
                    ),
                },
            )
            for index, record in enumerate(self.records * 5)
        ]

        model = train_outcome_model(records)
        prediction = predict_decision(model, self.records[0])

        self.assertTrue(model["ready"])
        self.assertEqual(model["training_examples"], 30)
        self.assertIn(prediction["predicted_decision"], ("Profitable", "Unprofitable"))

    def test_outcome_endpoint_persists_derived_profitability_label(self):
        with TemporaryDirectory() as temp_dir:
            decision_file = Path(temp_dir) / "decision_log.json"
            with patch.object(api, "DECISIONS_PATH", decision_file):
                saved = api.save_decision({
                    "timestamp": "2026-10-07T15:00:00",
                    "proposed_action": "Increase budget",
                    "budget_change_pct": 20,
                    "human_decision": "Approve",
                    "decision_source": "human_in_the_loop",
                    "model_features": self.records[0]["model_features"],
                })
                decision_id = saved["decision"]["decision_id"]
                outcome_result = api.save_decision_outcome(
                    decision_id,
                    DecisionOutcome(
                        measured_from="2026-10-01",
                        measured_to="2026-10-07",
                        actual_spend=100,
                        actual_revenue=250,
                        actual_cogs=100,
                    ),
                )
                saved_record = api.list_decisions()[0]

            self.assertEqual(outcome_result["status"], "outcome_saved")
            self.assertEqual(saved_record["outcome"]["actual_contribution_profit"], 50)
            self.assertEqual(saved_record["outcome"]["profitability_label"], "Profitable")

    def test_simulator_execution_requires_approval_and_supports_one_rollback(self):
        with TemporaryDirectory() as temp_dir:
            decision_file = Path(temp_dir) / "decision_log.json"
            with patch.object(api, "DECISIONS_PATH", decision_file):
                rejected = api.save_decision({
                    "timestamp": "2026-10-07T15:00:00",
                    "proposed_action": "Increase budget",
                    "budget_change_pct": 20,
                    "human_decision": "Reject",
                    "decision_source": "human_in_the_loop",
                })["decision"]
                approved = api.save_decision({
                    "timestamp": "2026-10-07T15:00:00",
                    "proposed_action": "Increase budget",
                    "budget_change_pct": 20,
                    "human_decision": "Approve",
                    "decision_source": "human_in_the_loop",
                })["decision"]

                with self.assertRaises(api.HTTPException) as rejected_error:
                    api.execute_decision(rejected["decision_id"])
                self.assertEqual(rejected_error.exception.status_code, 409)

                execution_result = api.execute_decision(
                    approved["decision_id"], ExecutionRequest()
                )
                self.assertEqual(execution_result["execution"]["status"], "simulated")
                self.assertTrue(execution_result["execution"]["rollback_available"])

                with self.assertRaises(api.HTTPException) as duplicate_error:
                    api.execute_decision(approved["decision_id"])
                self.assertEqual(duplicate_error.exception.status_code, 409)

                rollback_result = api.rollback_decision(approved["decision_id"])
                self.assertEqual(rollback_result["execution"]["status"], "rolled_back")
                self.assertFalse(rollback_result["execution"]["rollback_available"])

                with self.assertRaises(api.HTTPException) as repeated_rollback_error:
                    api.rollback_decision(approved["decision_id"])
                self.assertEqual(repeated_rollback_error.exception.status_code, 409)


if __name__ == "__main__":
    unittest.main()
