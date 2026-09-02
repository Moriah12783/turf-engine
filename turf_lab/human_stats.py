"""F2 — Carnet de statistiques humaines auto-apprises (drivers / entraîneurs).

Le flux PMU ne fournit AUCUNE statistique de réussite des drivers, jockeys
et entraîneurs. Ce module les calcule en continu depuis notre propre
historique permanent (courses terminées + arrivées officielles) : un
capteur propriétaire qui s'améliore chaque jour à mesure que la base
grandit, et que personne d'autre ne possède à l'identique.

Principes d'honnêteté (les mêmes que pour tous les capteurs du moteur) :
- UNIQUEMENT des arrivées officielles enregistrées — jamais d'estimation ;
- lissage bayésien (shrinkage) vers la moyenne globale : un driver vu 6
  fois ne peut pas écraser le score d'un driver vu 200 fois ;
- en dessous de MIN_SAMPLE courses, AUCUN signal n'est émis : le poids du
  capteur est alors redistribué sur les capteurs réels (jamais de
  constante fictive injectée) ;
- le carnet est construit au début de chaque passe de synchronisation :
  au verrouillage d'une course, il ne contient QUE des courses déjà
  terminées — aucune information du futur ne fuit dans le pronostic.
"""

import json
from typing import Any, Dict, List, Optional

# Noms « placeholder » de l'ancien système fictif : jamais comptés.
_PLACEHOLDER_NAMES = {"", "-", "J.DRIVER", "T.TRAINER", "NON PARTANT"}


def _norm(name: Optional[str]) -> str:
    """Clé de regroupement d'un nom (insensible à la casse et aux espaces)."""
    return " ".join(str(name or "").upper().split())


class HumanStatsBook:
    """Carnet de réussite par driver/jockey, entraîneur et couple driver-cheval."""

    MIN_SAMPLE = 5     # courses minimum avant d'émettre un signal
    MIN_COUPLE = 3     # courses minimum pour le bonus couple driver-cheval
    SHRINK_K = 20.0    # force du lissage bayésien vers la moyenne globale

    def __init__(self, db):
        self.db = db
        self.drivers: Dict[str, Dict[str, float]] = {}
        self.trainers: Dict[str, Dict[str, float]] = {}
        self.couples: Dict[str, Dict[str, float]] = {}
        self.global_top3_rate = 0.25   # a priori neutre, remplacé au build
        self.races_learned = 0
        self._build()

    # ── Construction (une passe SQL sur l'historique permanent) ──────────
    def _build(self) -> None:
        try:
            with self.db.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT race_id, arrival_order_json FROM race_results")
                results = {row["race_id"]: row["arrival_order_json"] for row in cursor.fetchall()}
                cursor.execute(
                    "SELECT race_id, num, horse_name, driver_jockey, trainer, is_non_partant FROM runners"
                )
                runner_rows = cursor.fetchall()
        except Exception:
            return  # base indisponible : carnet vide, capteur muet (honnête)

        total_starts = 0
        total_top3 = 0

        for row in runner_rows:
            arrival_json = results.get(row["race_id"])
            if not arrival_json:
                continue
            if row["is_non_partant"]:
                continue
            try:
                arrival = json.loads(arrival_json)
            except (TypeError, ValueError):
                continue
            if not arrival:
                continue

            num = row["num"]
            won = 1 if num == arrival[0] else 0
            top3 = 1 if num in arrival[:3] else 0
            total_starts += 1
            total_top3 += top3

            d_key = _norm(row["driver_jockey"])
            t_key = _norm(row["trainer"])
            h_key = _norm(row["horse_name"])

            if d_key not in _PLACEHOLDER_NAMES:
                d = self.drivers.setdefault(d_key, {"n": 0, "wins": 0, "top3": 0})
                d["n"] += 1
                d["wins"] += won
                d["top3"] += top3
                if h_key not in _PLACEHOLDER_NAMES:
                    c = self.couples.setdefault(d_key + "|" + h_key, {"n": 0, "wins": 0, "top3": 0})
                    c["n"] += 1
                    c["wins"] += won
                    c["top3"] += top3

            if t_key not in _PLACEHOLDER_NAMES:
                t = self.trainers.setdefault(t_key, {"n": 0, "wins": 0, "top3": 0})
                t["n"] += 1
                t["wins"] += won
                t["top3"] += top3

        if total_starts > 0:
            self.global_top3_rate = total_top3 / total_starts
        self.races_learned = len(results)

    # ── Lissage bayésien ─────────────────────────────────────────────────
    def _shrunk_top3(self, entry: Optional[Dict[str, float]]) -> Optional[float]:
        """Taux top-3 lissé : (top3 + K × moyenne globale) / (n + K)."""
        if not entry or entry["n"] <= 0:
            return None
        return (entry["top3"] + self.SHRINK_K * self.global_top3_rate) / (entry["n"] + self.SHRINK_K)

    # ── Enrichissement des partants avant pronostic ──────────────────────
    def enrich_runners(self, runners: List[Dict[str, Any]]) -> None:
        """Attache à chaque partant ses statistiques humaines apprises.

        N'écrit que des faits mesurés sur nos archives ; un partant dont le
        driver/entraîneur n'atteint pas MIN_SAMPLE ne reçoit pas de taux."""
        for r in runners:
            d = self.drivers.get(_norm(r.get("driver_jockey")))
            t = self.trainers.get(_norm(r.get("trainer")))
            c = self.couples.get(
                _norm(r.get("driver_jockey")) + "|" + _norm(r.get("horse_name"))
            )
            hs: Dict[str, Any] = {
                "global_top3_rate": round(self.global_top3_rate, 4),
                "driver_rides": int(d["n"]) if d else 0,
                "trainer_starts": int(t["n"]) if t else 0,
                "couple_rides": int(c["n"]) if c else 0,
            }
            if d and d["n"] >= self.MIN_SAMPLE:
                hs["driver_top3_shrunk"] = round(self._shrunk_top3(d), 4)
            if t and t["n"] >= self.MIN_SAMPLE:
                hs["trainer_top3_shrunk"] = round(self._shrunk_top3(t), 4)
            if c and c["n"] >= self.MIN_COUPLE:
                hs["couple_top3_shrunk"] = round(self._shrunk_top3(c), 4)
            r["human_stats"] = hs

    # ── Palmarès (contenu éditorial / futur tableau site) ────────────────
    def top_drivers(self, min_rides: int = 10, limit: int = 15) -> List[Dict[str, Any]]:
        rows = [
            {"name": k, "rides": int(v["n"]), "wins": int(v["wins"]),
             "top3_pct": round(100.0 * v["top3"] / v["n"], 1),
             "top3_shrunk_pct": round(100.0 * self._shrunk_top3(v), 1)}
            for k, v in self.drivers.items() if v["n"] >= min_rides
        ]
        rows.sort(key=lambda x: -x["top3_shrunk_pct"])
        return rows[:limit]

    def top_trainers(self, min_starts: int = 10, limit: int = 15) -> List[Dict[str, Any]]:
        rows = [
            {"name": k, "starts": int(v["n"]), "wins": int(v["wins"]),
             "top3_pct": round(100.0 * v["top3"] / v["n"], 1),
             "top3_shrunk_pct": round(100.0 * self._shrunk_top3(v), 1)}
            for k, v in self.trainers.items() if v["n"] >= min_starts
        ]
        rows.sort(key=lambda x: -x["top3_shrunk_pct"])
        return rows[:limit]
