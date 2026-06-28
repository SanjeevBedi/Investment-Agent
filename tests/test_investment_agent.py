import json
import tempfile
import unittest
from pathlib import Path

import investment_agent


class InvestmentAgentTests(unittest.TestCase):
    def test_score_alignment(self):
        weights = {"autonomous": 4, "agentic": 3, "manufacturing": 2}
        score, matched = investment_agent.score_alignment("Autonomous agentic systems in manufacturing", weights)
        self.assertGreater(score, 0)
        self.assertIn("autonomous", matched)

    def test_trend_direction(self):
        self.assertEqual(investment_agent.trend_direction([100.0, 110.0])[0], "up")
        self.assertEqual(investment_agent.trend_direction([100.0, 90.0])[0], "down")
        self.assertEqual(investment_agent.trend_direction([100.0, 101.0])[0], "stable")

    def test_monthly_summary(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            daily_dir = root / "data" / "daily"
            daily_dir.mkdir(parents=True, exist_ok=True)
            (daily_dir / "2026-06-01.json").write_text(json.dumps({
                "entities": [
                    {"name": "Acme Robotics", "alignment_score": 8.0, "trend_direction": "up"},
                    {"name": "Beta AI", "alignment_score": 5.0, "trend_direction": "stable"},
                ]
            }), encoding="utf-8")
            (daily_dir / "2026-06-02.json").write_text(json.dumps({
                "entities": [
                    {"name": "Acme Robotics", "alignment_score": 7.0, "trend_direction": "up"},
                ]
            }), encoding="utf-8")

            out = investment_agent.build_monthly_summary(root, "2026-06")
            report = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(report["days_processed"], 2)
            self.assertEqual(report["entity_count"], 2)
            self.assertEqual(report["entities"][0]["name"], "Acme Robotics")


if __name__ == "__main__":
    unittest.main()
