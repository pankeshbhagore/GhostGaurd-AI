"""Unit tests for OpenAI API integration, fallbacks, and token metering."""
import os
import unittest
from deletion_audit.openai_client import OpenAIClient, AI_CLIENT
from deletion_audit.usage import Meter


class TestOpenAIIntegration(unittest.TestCase):
    def test_pricing_calculation(self):
        client = OpenAIClient()
        # 10,000 prompt tokens and 5,000 completion tokens on gpt-4o:
        # prompt: 10,000 / 1M * $2.50 = $0.025
        # completion: 5,000 / 1M * $10.00 = $0.050
        # total: $0.075
        cost = client._calculate_cost("gpt-4o", 10000, 5000)
        self.assertAlmostEqual(cost, 0.075, places=4)

    def test_deterministic_fallback_when_disabled(self):
        # Create a client instance with invalid key
        orig_key = os.environ.get("OPENAI_API_KEY")
        try:
            client = OpenAIClient()
            client._initialized = True
            client._client = None  # simulate disabled

            auth, reason, used = client.analyze_ambiguous_intent("Random text", (False, "fallback"))
            self.assertEqual(auth, False)
            self.assertEqual(reason, "fallback")
            self.assertFalse(used)

            nar = client.explain_investigation_and_remediation("case_00", "COMPLETE", [], "request")
            self.assertEqual(nar["case_id"], "case_00")
            self.assertEqual(nar["source"], "deterministic_template")
        finally:
            if orig_key:
                os.environ["OPENAI_API_KEY"] = orig_key

    def test_live_ai_client_available(self):
        # When OPENAI_API_KEY is configured in .env
        if os.getenv("OPENAI_API_KEY"):
            self.assertTrue(AI_CLIENT.is_available())
            self.assertEqual(AI_CLIENT.model, os.getenv("OPENAI_MODEL", "gpt-4o"))

    def test_meter_tracking(self):
        meter = Meter()
        meter.record("OpenAI", "gpt-4o", 100, 50, 0.00075)
        meter.record("OpenAI", "gpt-4o", 200, 100, 0.00150)
        t = meter.totals()
        self.assertEqual(t["calls"], 2)
        self.assertEqual(t["input_tokens"], 300)
        self.assertEqual(t["output_tokens"], 150)
        self.assertAlmostEqual(t["cost_usd"], 0.00225, places=5)


if __name__ == "__main__":
    unittest.main()
