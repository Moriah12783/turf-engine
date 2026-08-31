"""Realistic race simulator for sandbox testing and backtesting."""

import random
from typing import Any, Dict, List, Tuple
from turf_lab.database import TurfDatabase
from turf_lab.engine import NewValueEngine
from turf_lab.baselines import ETPEEngineProxy, PressSynthesisEngine, MarketOddsEngine


class RaceSimulator:
    """Generates realistic synthetic turf race meetings for benchmarking."""

    HIPPODROMES = [
        ("VINCENNES", "TROT_ATTELE", "SABLE", 2700, False),
        ("ENGHIEN", "TROT_ATTELE", "SABLE", 2150, True),
        ("DEAUVILLE", "PLAT", "PSF", 1900, False),
        ("LONGCHAMP", "PLAT", "HERBE", 2400, False),
        ("CABOURG", "TROT_ATTELE", "SABLE", 2750, False),
        ("CHANTILLY", "PLAT", "HERBE", 1600, False),
        ("AUTEUIL", "OBSTACLE_HAIES", "HERBE", 3600, False),
    ]

    SHOEINGS = ["D4", "D4", "DP", "DA", "FERRE", "FERRE"]
    BLINKERS = ["SANS", "SANS", "OEILLERES", "OEILLERES_AUSTRALIENNES"]

    def __init__(self, db: TurfDatabase, seed: int = 42):
        self.db = db
        self.rng = random.Random(seed)
        self.new_engine = NewValueEngine()
        self.etpe_engine = ETPEEngineProxy()
        self.press_engine = PressSynthesisEngine()
        self.market_engine = MarketOddsEngine()

    def generate_single_race(self, race_idx: int) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
        """Generate a realistic race card with 14-16 runners."""
        hippo_spec = self.HIPPODROMES[race_idx % len(self.HIPPODROMES)]
        hippo_name, discipline, track_type, distance, autostart = hippo_spec

        race_id = f"R1C{race_idx + 1}_{hippo_name[:4]}_{race_idx}"
        race_data = {
            "race_id": race_id,
            "date": "2026-08-28",
            "meeting_number": 1,
            "race_number": (race_idx % 8) + 1,
            "name": f"Prix de {hippo_name.capitalize()} Classic #{race_idx + 1}",
            "hippodrome": hippo_name,
            "discipline": discipline,
            "distance": distance,
            "track_type": track_type,
            "track_condition": "BON",
            "rope": "GAUCHE" if "VINCENNES" in hippo_name or "ENGHIEN" in hippo_name else "DROITE",
            "autostart": autostart,
            "scheduled_start_time": f"2026-08-28T{13 + (race_idx % 6):02d}:50:00Z",
            "status": "SCHEDULED"
        }

        num_runners = self.rng.randint(14, 16)
        runners = []

        # Generate odds distribution
        base_odds = [2.8, 4.2, 6.5, 8.0, 11.0, 14.0, 18.0, 22.0, 28.0, 35.0, 45.0, 60.0, 75.0, 90.0, 110.0, 130.0]
        self.rng.shuffle(base_odds)

        for i in range(1, num_runners + 1):
            # Music patterns
            pos1 = self.rng.choice(["1", "2", "3", "4", "0", "D", "5", "1"])
            pos2 = self.rng.choice(["1", "2", "3", "0", "D", "6", "4"])
            pos3 = self.rng.choice(["2", "3", "5", "0", "D", "1"])
            music = f"{pos1}a{pos2}a{pos3}a" if "TROT" in discipline else f"{pos1}p{pos2}p{pos3}p"

            m_odds = base_odds[i - 1]
            # Market movement (fluctuation)
            drift = self.rng.uniform(0.85, 1.18)
            f_odds = round(max(1.5, m_odds * drift), 1)

            # Press citations: correlated with lower odds
            citation_pool = max(0, int((120.0 - min(100.0, f_odds)) / 4.0) + self.rng.randint(-3, 4))
            citations = max(0, min(30, citation_pool))

            # Reduction kilométrique or rating
            chrono = round(self.rng.uniform(71.8, 76.5), 1)
            rating = round(self.rng.uniform(30.0, 44.0), 1)

            runners.append({
                "num": i,
                "horse_name": f"Trotting_Star_{i}" if "TROT" in discipline else f"Royal_Gallop_{i}",
                "sex": self.rng.choice(["M", "F", "H"]),
                "age": self.rng.randint(4, 8),
                "driver_jockey": f"Driver_{i}",
                "trainer": f"Trainer_{i % 5}",
                "weight": round(self.rng.uniform(54.0, 61.0), 1),
                "draw": i,
                "shoeing": self.rng.choice(self.SHOEINGS),
                "blinkers": self.rng.choice(self.BLINKERS),
                "morning_odds": m_odds,
                "final_odds": f_odds,
                "press_citation_count": citations,
                "music": music,
                "earnings": round(self.rng.uniform(20000, 250000), 2),
                "record_chrono": chrono,
                "official_rating": rating
            })

        return race_data, runners

    def simulate_race_outcome(self, race_data: Dict[str, Any], runners: List[Dict[str, Any]]) -> Tuple[List[int], List[Dict[str, Any]]]:
        """Simulate realistic stochastic race outcome and calculate realistic dividends."""
        # True winning probability based on a mixture of odds, speed and form with noise
        weights = []
        for r in runners:
            inv_odds = 1.0 / max(1.5, r["final_odds"])
            perf_noise = self.rng.expovariate(1.0)
            score = inv_odds * 0.7 + perf_noise * 0.3
            weights.append(score)

        # Draw places without replacement
        runner_indices = list(range(len(runners)))
        arrival_indices = []

        for _ in range(len(runners)):
            total_w = sum(weights[i] for i in runner_indices)
            pick_val = self.rng.uniform(0, total_w)
            cum = 0.0
            chosen = runner_indices[0]
            for idx in runner_indices:
                cum += weights[idx]
                if cum >= pick_val:
                    chosen = idx
                    break
            arrival_indices.append(chosen)
            runner_indices.remove(chosen)

        arrival_nums = [runners[i]["num"] for i in arrival_indices]
        winner = arrival_nums[0]
        second = arrival_nums[1]
        third = arrival_nums[2]

        winner_odds = next(r["final_odds"] for r in runners if r["num"] == winner)
        second_odds = next(r["final_odds"] for r in runners if r["num"] == second)
        third_odds = next(r["final_odds"] for r in runners if r["num"] == third)

        # PMU-like payout modeling
        sg_dividend = max(1.10, round(winner_odds * 0.85, 2))  # Deduct PMU rake ~15%
        sp_dividend_1 = max(1.10, round(1.0 + (winner_odds - 1.0) * 0.28, 2))
        sp_dividend_2 = max(1.10, round(1.0 + (second_odds - 1.0) * 0.28, 2))
        sp_dividend_3 = max(1.10, round(1.0 + (third_odds - 1.0) * 0.28, 2))

        rapports = [
            {"bet_type": "SIMPLE_GAGNANT", "combination": str(winner), "dividend": sg_dividend},
            {"bet_type": "SIMPLE_PLACE", "combination": str(winner), "dividend": sp_dividend_1},
            {"bet_type": "SIMPLE_PLACE", "combination": str(second), "dividend": sp_dividend_2},
            {"bet_type": "SIMPLE_PLACE", "combination": str(third), "dividend": sp_dividend_3},
            {"bet_type": "COUPLE_GAGNANT", "combination": f"{winner}-{second}", "dividend": round(winner_odds * second_odds * 0.22, 2)},
            {"bet_type": "COUPLE_PLACE", "combination": f"{winner}-{second}", "dividend": round((sp_dividend_1 + sp_dividend_2) * 1.5, 2)},
        ]

        return arrival_nums, rapports

    def run_benchmark_simulation(self, num_races: int = 250) -> None:
        """Run full end-to-end sandbox simulation across all engines."""
        for idx in range(num_races):
            race_data, runners = self.generate_single_race(idx)
            race_id = race_data["race_id"]

            # 1. Ingest race & runners
            self.db.save_race(race_data)
            self.db.save_runners(race_id, runners)

            # 2. Run all engines and lock predictions
            p_new = self.new_engine.predict(race_data, runners)
            p_new["prediction_id"] = f"{race_id}_NEW"
            p_new["race_id"] = race_id
            self.db.save_prediction(p_new)

            p_etpe = self.etpe_engine.predict(race_data, runners)
            p_etpe["prediction_id"] = f"{race_id}_ETPE"
            p_etpe["race_id"] = race_id
            self.db.save_prediction(p_etpe)

            p_press = self.press_engine.predict(race_data, runners)
            p_press["prediction_id"] = f"{race_id}_PRESS"
            p_press["race_id"] = race_id
            self.db.save_prediction(p_press)

            p_market = self.market_engine.predict(race_data, runners)
            p_market["prediction_id"] = f"{race_id}_MARKET"
            p_market["race_id"] = race_id
            self.db.save_prediction(p_market)

            # 3. Simulate race completion and save official results & payouts
            arrival_order, rapports = self.simulate_race_outcome(race_data, runners)
            self.db.save_results(race_id, arrival_order, rapports=rapports)
