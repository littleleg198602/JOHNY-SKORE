"""Matched layer comparisons must not treat label timestamps as price proof."""
from datetime import datetime, timezone
import json
import unittest
from unittest.mock import patch

from market_checker_app.services.layer_ablation_service import evaluate_layer_ablation
from market_checker_app.services.specialist_label_availability_service import prepare_label_observations
from tests.test_candidate_model_evaluation_service import _snapshots


class Batch5LayerPriceGateTests(unittest.TestCase):
    def test_legacy_labels_without_price_captures_cannot_enter_strict_layer_cohorts(self):
        frame = _snapshots()
        enriched = []
        for encoded in frame["feature_payload_json"]:
            payload = json.loads(encoded)
            payload["news"] = {"news_count_total": 1, "weighted_sentiment_avg": 0.2, "news_confidence": 0.8}
            enriched.append(json.dumps(payload))
        frame["feature_payload_json"] = enriched
        knobs = {"minimum_training_samples": 8, "minimum_evaluation_samples": 1, "minimum_weeks": 1, "iterations": 20}
        legacy = evaluate_layer_ablation(frame, **knobs)
        self.assertGreater(legacy["variants"]["news"]["sample_count"], 0)
        self.assertFalse(legacy["price_label_gate_applied"])
        self.assertFalse(legacy["oos_verified"])
        before = frame.copy(deep=True)
        with patch("market_checker_app.services.specialist_label_availability_service.prepare_label_observations",
                   wraps=prepare_label_observations) as prepare:
            strict = evaluate_layer_ablation(frame, strict_price_provenance=True,
                as_of=datetime(2026, 3, 1, tzinfo=timezone.utc), label_observations=[], **knobs)
        self.assertTrue(strict["strict_price_provenance_requested"])
        self.assertEqual("INSUFFICIENT_DATA", strict["status"])
        self.assertEqual(0, strict["variants"]["news"]["sample_count"])
        self.assertFalse(strict["activation_allowed"])
        self.assertFalse(strict["ranking_modified"])
        self.assertFalse(strict["oos_verified"])
        self.assertEqual(1, prepare.call_count)
        self.assertTrue(before.equals(frame))


if __name__ == "__main__":
    unittest.main()
