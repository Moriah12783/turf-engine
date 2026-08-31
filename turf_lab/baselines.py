"""Baseline engines used for benchmarking and comparison against the new engine."""

from typing import Any, Dict, List
from turf_lab.features import extract_runner_features


class PressSynthesisEngine:
    """Consensus baseline: Ranks runners by press citation count."""

    def __init__(self, engine_name: str = "PRESS_SYNTHESIS"):
        self.engine_name = engine_name

    def predict(self, race: Dict[str, Any], runners: List[Dict[str, Any]]) -> Dict[str, Any]:
        valid_runners = [r for r in runners if not r.get("is_non_partant", False)]
        if not valid_runners:
            return {"engine_name": self.engine_name, "selection": [], "bases": [], "outsider_num": None}

        sorted_runners = sorted(
            valid_runners,
            key=lambda r: (r.get("press_citation_count", 0), -r.get("morning_odds", 99.0)),
            reverse=True
        )

        selection = [r["num"] for r in sorted_runners[:8]]
        bases = [r["num"] for r in sorted_runners[:2]]
        outsider = sorted_runners[-1]["num"] if len(sorted_runners) >= 8 else None

        return {
            "engine_name": self.engine_name,
            "selection": selection,
            "bases": bases,
            "outsider_num": outsider,
            "probabilities": {},
            "metadata": {"type": "press_consensus"}
        }


class MarketOddsEngine:
    """Market baseline: Ranks runners strictly by lowest PMU final/live odds (Favorites)."""

    def __init__(self, engine_name: str = "MARKET_BASELINE"):
        self.engine_name = engine_name

    def predict(self, race: Dict[str, Any], runners: List[Dict[str, Any]]) -> Dict[str, Any]:
        valid_runners = [r for r in runners if not r.get("is_non_partant", False)]
        if not valid_runners:
            return {"engine_name": self.engine_name, "selection": [], "bases": [], "outsider_num": None}

        sorted_runners = sorted(
            valid_runners,
            key=lambda r: r.get("odds_t15", r.get("final_odds", r.get("morning_odds", 99.0)))
        )

        selection = [r["num"] for r in sorted_runners[:8]]
        bases = [r["num"] for r in sorted_runners[:2]]
        outsider = sorted_runners[7]["num"] if len(sorted_runners) >= 8 else None

        # Implied probabilities from odds
        inv_odds = [1.0 / max(1.1, r.get("odds_t15", r.get("final_odds", 10.0))) for r in valid_runners]
        sum_inv = sum(inv_odds) or 1.0
        prob_dict = {str(r["num"]): round(inv / sum_inv, 4) for r, inv in zip(valid_runners, inv_odds)}

        return {
            "engine_name": self.engine_name,
            "selection": selection,
            "bases": bases,
            "outsider_num": outsider,
            "probabilities": prob_dict,
            "metadata": {"type": "market_odds"}
        }


class ETPEEngineProxy:
    """ETPE (Elite Turf Prediction Engine) baseline proxy."""

    def __init__(self, engine_name: str = "ETPE_ENGINE"):
        self.engine_name = engine_name

    def predict(self, race: Dict[str, Any], runners: List[Dict[str, Any]]) -> Dict[str, Any]:
        valid_runners = [r for r in runners if not r.get("is_non_partant", False)]
        if not valid_runners:
            return {"engine_name": self.engine_name, "selection": [], "bases": [], "outsider_num": None}

        scored = []
        for r in valid_runners:
            feats = extract_runner_features(r, race)
            etpe_score = (
                feats["form_score"] * 0.40 +
                feats["speed_score"] * 0.35 +
                feats["press_score"] * 0.25
            )
            scored.append((r["num"], etpe_score, r.get("final_odds", 10.0)))

        scored_sorted = sorted(scored, key=lambda x: x[1], reverse=True)

        selection = [x[0] for x in scored_sorted[:8]]
        bases = [x[0] for x in scored_sorted[:2]]
        outsider = scored_sorted[6][0] if len(scored_sorted) >= 7 else None

        return {
            "engine_name": self.engine_name,
            "selection": selection,
            "bases": bases,
            "outsider_num": outsider,
            "probabilities": {},
            "metadata": {"type": "etpe_heuristic"}
        }
