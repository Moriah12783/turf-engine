"""Unit tests for the Turf Prediction Engine & Benchmarking Lab."""

import os
import unittest
from turf_lab.database import TurfDatabase
from turf_lab.features import parse_music, compute_shoeing_score, compute_smart_money_velocity, compute_track_bias_score
from turf_lab.engine import NewValueEngine
from turf_lab.benchmark import TurfBenchmarkLab
from turf_lab.simulator import RaceSimulator


class TestTurfLab(unittest.TestCase):

    def setUp(self):
        self.test_db_path = "/tmp/test_turf.db"
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)
        self.db = TurfDatabase(self.test_db_path)

    def tearDown(self):
        if os.path.exists(self.test_db_path):
            os.remove(self.test_db_path)

    def test_parse_music(self):
        res = parse_music("1a2a3a0a(25)Da")
        self.assertGreater(res["form_score"], 50.0)
        self.assertGreater(res["regularity"], 0.4)
        self.assertGreater(res["dai_rate"], 0.1)

    def test_shoeing_score(self):
        score_d4 = compute_shoeing_score("D4", "TROT_ATTELE")
        score_ferre = compute_shoeing_score("FERRE", "TROT_ATTELE")
        self.assertGreater(score_d4, score_ferre)

    def test_smart_money_velocity(self):
        score_backed, sig_backed = compute_smart_money_velocity(16.0, 9.0)
        score_drifted, sig_drifted = compute_smart_money_velocity(8.0, 16.0)
        self.assertGreater(score_backed, score_drifted)
        self.assertIn("BAISSE", sig_backed)

    def test_track_bias(self):
        # Inside draw in soft sprint should beat outer draw
        score_in = compute_track_bias_score(2, 1400, "DROITE", "DEAUVILLE", "SOUPLE", "PLAT", False)
        score_out = compute_track_bias_score(15, 1400, "DROITE", "DEAUVILLE", "SOUPLE", "PLAT", False)
        self.assertGreater(score_in, score_out)

    def test_non_partant_handling_and_tickets(self):
        sim = RaceSimulator(self.db, seed=123)
        race_data, runners = sim.generate_single_race(1)
        runners[2]["is_non_partant"] = True

        engine = NewValueEngine()
        pred = engine.predict(race_data, runners)

        self.assertNotIn(runners[2]["num"], pred["selection"])
        self.assertIn("smart_tickets", pred)
        self.assertIn("ticket_securite", pred["smart_tickets"])
        self.assertIn("quinte_champ_reduit", pred["smart_tickets"])
        self.assertGreaterEqual(pred["confidence_stars"], 1)

    def test_full_benchmark_pipeline(self):
        sim = RaceSimulator(self.db, seed=42)
        sim.run_benchmark_simulation(num_races=20)

        lab = TurfBenchmarkLab(self.db)
        report = lab.generate_comparative_report()

        self.assertEqual(report["total_finished_races"], 20)
        self.assertIn("NEW_VALUE_ENGINE", report["evaluations"])


if __name__ == "__main__":
    unittest.main()
