"""Baseline engines used for benchmarking and comparison against the new engine."""

from typing import Any, Dict, List, Optional
from turf_lab.features import extract_runner_features
from turf_lab.odds_quality import all_default_odds, is_valid_odds

# Champs de cotes lus par la ligne de base marché, du plus récent au plus ancien.
_MARKET_ODDS_FIELDS = ("odds_t15", "final_odds", "morning_odds")


def market_edition_informative(pred: Dict[str, Any]) -> bool:
    """Une édition MARKET_BASELINE archivée porte-t-elle une information de marché ?

    La ligne de base marché n'a de sens que si des cotes réelles existaient au
    verrou. Trois signatures d'édition NOMINALE (à ignorer partout : banc,
    courses communes, affichage) :
    - sélection vide (le moteur marché a lui-même déclaré l'absence de marché) ;
    - ``odds_real`` faux : verrou posé sur cotes neutralisées (porte de
      verrouillage à 50 %, cotes réelles entre 50 et 90 %) — les cotes vues
      par le moteur marché étaient toutes la sentinelle 15.0 ;
    - probabilités toutes égales : cotes toutes par défaut au verrou (éditions
      antérieures à la porte de fraîcheur, réunions sans cotes dans le flux).
    Dans ces trois cas la « sélection marché » n'était que l'ordre des numéros
    (1-2-3-4-5-6-7-8) et ne doit jamais être comptée comme un pronostic."""
    if not pred or not pred.get("selection"):
        return False
    odds_real = pred.get("odds_real")
    if odds_real is not None and not bool(odds_real):
        return False
    probs = pred.get("probabilities") or {}
    if isinstance(probs, str):
        try:
            import json
            probs = json.loads(probs)
        except (TypeError, ValueError):
            probs = {}
    values = [float(v) for v in probs.values()] if isinstance(probs, dict) else []
    if not values or len(set(round(v, 6) for v in values)) <= 1:
        return False
    return True


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

    # Part minimale de partants réellement cotés pour qu'un « marché » existe.
    # Même seuil que NewValueEngine.MIN_MARKET_COVERAGE : en dessous, la ligne
    # de base marché ne produit AUCUNE sélection (édition nominale honnête)
    # plutôt qu'un classement par numéro de dossard.
    MIN_MARKET_COVERAGE = 0.5

    @staticmethod
    def _real_odds(runner: Dict[str, Any]) -> Optional[float]:
        """Première cote RÉELLE du partant (provenance puis valeur), None sinon."""
        if all_default_odds(runner):
            return None
        for field in _MARKET_ODDS_FIELDS:
            v = runner.get(field)
            if v is not None and is_valid_odds(v):
                return float(v)
        return None

    def _empty(self, valid_runners: List[Dict[str, Any]], coverage: float) -> Dict[str, Any]:
        """Édition NOMINALE : aucun favori, aucune base — le marché ne « sait »
        rien, ses probabilités sont uniformes (jamais une sélection par numéro)."""
        n = len(valid_runners)
        uniform = {str(r["num"]): round(1.0 / n, 4) for r in valid_runners} if n else {}
        return {
            "engine_name": self.engine_name,
            "selection": [],
            "bases": [],
            "outsider_num": None,
            "probabilities": uniform,
            "metadata": {"type": "market_odds", "market_available": False,
                         "coverage_pct": round(100.0 * coverage, 1)},
        }

    def predict(self, race: Dict[str, Any], runners: List[Dict[str, Any]]) -> Dict[str, Any]:
        valid_runners = [r for r in runners if not r.get("is_non_partant", False)]
        if not valid_runners:
            return self._empty(valid_runners, 0.0)

        # Marché réel ? Sans cotes réelles (réunion sans cotes dans le flux,
        # ou cotes neutralisées par la porte de verrouillage), il n'y a pas de
        # favoris : la sélection reste VIDE au lieu de l'ordre des numéros.
        real = {r["num"]: self._real_odds(r) for r in valid_runners}
        priced = [r for r in valid_runners if real[r["num"]] is not None]
        coverage = len(priced) / len(valid_runners)
        if coverage < self.MIN_MARKET_COVERAGE or len(priced) < 2:
            return self._empty(valid_runners, coverage)

        # Favoris d'abord (cote croissante) ; les partants sans cote réelle
        # (marché partiel) passent après, par numéro.
        sorted_runners = sorted(
            valid_runners,
            key=lambda r: (0, real[r["num"]], r["num"]) if real[r["num"]] is not None else (1, 0.0, r["num"])
        )

        selection = [r["num"] for r in sorted_runners[:8]]
        bases = [r["num"] for r in sorted_runners[:2]]
        outsider = sorted_runners[7]["num"] if len(sorted_runners) >= 8 else None

        # Probabilités implicites : seuls les partants cotés portent une masse ;
        # un partant sans cote réelle vaut 0 (jamais une constante fictive).
        inv_odds = [1.0 / max(1.1, real[r["num"]]) if real[r["num"]] is not None else 0.0 for r in valid_runners]
        sum_inv = sum(inv_odds) or 1.0
        prob_dict = {str(r["num"]): round(inv / sum_inv, 4) for r, inv in zip(valid_runners, inv_odds)}

        return {
            "engine_name": self.engine_name,
            "selection": selection,
            "bases": bases,
            "outsider_num": outsider,
            "probabilities": prob_dict,
            "metadata": {"type": "market_odds", "market_available": True,
                         "coverage_pct": round(100.0 * coverage, 1)},
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
