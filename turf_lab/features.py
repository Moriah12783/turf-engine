"""Advanced feature engineering: Smart Money velocity, track bias, terrain penetrometer, and equipment."""

import re
from typing import Any, Dict, List, Tuple


def parse_music(music: str) -> Dict[str, float]:
    """Parse French turf music string (e.g. '1a2a3a0aDa(25)4a' or '1p2p3p0p')"""
    if not music or not isinstance(music, str):
        return {"form_score": 30.0, "regularity": 0.2, "dai_rate": 0.1}

    clean_music = re.sub(r"\(\d+\)", "", music.strip())
    tokens = re.findall(r"(\d+|[Dd]|[Tt]|[Aa]|[Ff])([a-zA-Z]?)", clean_music)

    if not tokens:
        return {"form_score": 30.0, "regularity": 0.2, "dai_rate": 0.1}

    scores = []
    top_3_count = 0
    dai_count = 0
    total_valid = 0
    weights = [1.0, 0.85, 0.70, 0.55, 0.40, 0.25]

    for idx, (pos_str, disc) in enumerate(tokens[:6]):
        w = weights[idx] if idx < len(weights) else 0.2
        total_valid += 1
        pos_upper = pos_str.upper()

        if pos_upper in ("D", "DA", "DAI", "DISQ", "T", "A"):
            dai_count += 1
            scores.append(0.0 * w)
        else:
            try:
                pos = int(pos_str)
                if pos == 1:
                    score = 100.0
                    top_3_count += 1
                elif pos == 2:
                    score = 80.0
                    top_3_count += 1
                elif pos == 3:
                    score = 65.0
                    top_3_count += 1
                elif pos == 4:
                    score = 45.0
                elif pos == 5:
                    score = 30.0
                elif pos <= 9:
                    score = 15.0
                else:
                    score = 5.0
                scores.append(score * w)
            except ValueError:
                scores.append(20.0 * w)

    form_score = (sum(scores) / (sum(weights[:len(scores)]) or 1.0)) if scores else 30.0
    regularity = (top_3_count / total_valid) if total_valid > 0 else 0.0
    dai_rate = (dai_count / total_valid) if total_valid > 0 else 0.0

    return {
        "form_score": round(form_score, 2),
        "regularity": round(regularity, 3),
        "dai_rate": round(dai_rate, 3)
    }


def compute_shoeing_score(shoeing: str, discipline: str) -> float:
    """Evaluate shoeing impact for Trot."""
    if "TROT" not in discipline.upper():
        return 50.0
    
    s = (shoeing or "").upper()
    if s in ("D4", "D4P", "DEFERRE_4"):
        return 100.0
    elif s in ("DP", "DEFERRE_POSTERIEURS"):
        return 75.0
    elif s in ("DA", "DEFERRE_ANTERIEURS"):
        return 70.0
    elif s in ("FERRE", "SANS"):
        return 35.0
    return 50.0


def compute_equipment_score(blinkers: str, discipline: str) -> float:
    """Evaluate equipment for Galop (Plat / Obstacle)."""
    if "PLAT" not in discipline.upper() and "OBSTACLE" not in discipline.upper():
        return 50.0
    
    b = (blinkers or "").upper()
    if "OEILLERES_AUSTRALIENNES" in b:
        return 85.0
    elif "OEILLERES" in b:
        return 75.0
    return 50.0


def compute_track_bias_score(draw: int, distance: int, rope: str, hippodrome: str, track_condition: str, discipline: str, autostart: bool) -> float:
    """Proposition 2: Track Bias (Biais de corde & Pénétromètre / Terrain)."""
    is_trot = "TROT" in discipline.upper()

    if is_trot:
        if autostart:
            # Autostart: 1 to 5 are premium, 6-8 are outer tier 1, 9-16 are tier 2
            if 1 <= draw <= 5:
                return 95.0
            elif 6 <= draw <= 8:
                return 70.0
            elif 9 <= draw <= 12:
                return 55.0
            else:
                return 35.0
        else:
            # Volt start: inside runners at first tier have slight edge
            if 1 <= draw <= 8:
                return 80.0
            else:
                return 60.0
    else:
        # Plat / Galop: Rope & Distance Bias
        # Sprints (< 1600m) or soft/heavy ground heavily favor inside draws (1 to 6)
        is_heavy = track_condition.upper() in ("SOUPLE", "TRES_SOUPLE", "LOURD", "COLLANT")
        is_sprint = distance <= 1600

        if is_sprint or is_heavy:
            if 1 <= draw <= 4:
                return 95.0
            elif 5 <= draw <= 8:
                return 75.0
            elif 9 <= draw <= 12:
                return 50.0
            else:
                return 30.0  # Big handicap from wide draw
        else:
            if 1 <= draw <= 6:
                return 85.0
            elif 7 <= draw <= 12:
                return 70.0
            else:
                return 55.0


def compute_smart_money_velocity(morning_odds: float, live_odds: float) -> Tuple[float, str]:
    """Proposition 1: Smart Money Velocity & Insider Bet Detection."""
    if not morning_odds or not live_odds or morning_odds <= 0 or live_odds <= 0:
        return 50.0, "STABLE"

    ratio = morning_odds / live_odds

    if ratio >= 1.45:
        return 100.0, "FORTE_BAISSE_INSIDER"  # Massive backing (e.g. 15.0 -> 8.0)
    elif ratio >= 1.20:
        return 85.0, "BAISSE_CONFIRMEE"
    elif ratio >= 0.85:
        return 55.0, "STABLE"
    elif ratio >= 0.65:
        return 35.0, "LEGERE_HAUSSE"
    else:
        return 15.0, "FORTE_HAUSSE_DELAISSE"  # Drifting


def compute_speed_rating_score(record_chrono: float, official_rating: float, weight: float, discipline: str) -> float:
    """Evaluate raw capability (reduction kilométrique or handicap rating)."""
    if "TROT" in discipline.upper():
        if not record_chrono or record_chrono <= 0:
            return 50.0
        norm = max(0.0, min(100.0, 100.0 - (record_chrono - 70.0) * 10.0))
        return round(norm, 2)
    else:
        if not official_rating or official_rating <= 0:
            val = 32.0
        else:
            val = official_rating
        weight_penalty = ((weight or 58.0) - 56.0) * 1.5
        score = (val * 2.2) - weight_penalty
        return round(max(10.0, min(100.0, score)), 2)


def compute_human_factor_score(runner: Dict[str, Any]) -> float:
    """F2 — Score du facteur humain (driver/jockey + entraîneur), appris
    depuis notre propre historique permanent via HumanStatsBook.

    Le score mesure l'écart du taux top-3 LISSÉ de chaque acteur par
    rapport à la moyenne globale de la base : 50 = dans la moyenne,
    au-dessus = surperformeur avéré, en dessous = sous-performeur.
    Bonus si le couple driver-cheval a déjà brillé ensemble.

    Retourne 0.0 (capteur MUET, poids redistribué par le moteur) quand
    aucune statistique n'atteint l'échantillon minimum — jamais de
    constante fictive."""
    hs = runner.get("human_stats") or {}
    baseline = float(hs.get("global_top3_rate", 0.25)) or 0.25

    parts = []  # (poids, score)
    d_rate = hs.get("driver_top3_shrunk")
    if d_rate is not None:
        parts.append((0.6, 50.0 + (float(d_rate) - baseline) * 250.0))
    t_rate = hs.get("trainer_top3_shrunk")
    if t_rate is not None:
        parts.append((0.4, 50.0 + (float(t_rate) - baseline) * 250.0))

    if not parts:
        return 0.0

    total_w = sum(w for w, _ in parts)
    score = sum(w * s for w, s in parts) / total_w

    # Couple driver-cheval déjà performant ensemble : léger bonus.
    c_rate = hs.get("couple_top3_shrunk")
    if c_rate is not None and float(c_rate) > baseline:
        score += 5.0

    return round(max(5.0, min(95.0, score)), 2)


def extract_runner_features(runner: Dict[str, Any], race: Dict[str, Any]) -> Dict[str, Any]:
    """Extract a comprehensive feature vector including Track Bias and Smart Money Velocity."""
    discipline = race.get("discipline", "TROT_ATTELE")
    distance = int(race.get("distance", 2700))
    rope = race.get("rope", "GAUCHE")
    hippodrome = race.get("hippodrome", "")
    track_condition = race.get("track_condition", "BON")
    autostart = bool(race.get("autostart", False))

    music_stats = parse_music(runner.get("music", ""))
    shoeing_score = compute_shoeing_score(runner.get("shoeing", ""), discipline)
    equip_score = compute_equipment_score(runner.get("blinkers", ""), discipline)

    draw = int(runner.get("draw", runner.get("num", 1)))
    track_bias_score = compute_track_bias_score(
        draw, distance, rope, hippodrome, track_condition, discipline, autostart
    )

    speed_score = compute_speed_rating_score(
        runner.get("record_chrono", 74.0),
        runner.get("official_rating", 34.0),
        runner.get("weight", 58.0),
        discipline
    )

    morning_odds = float(runner.get("morning_odds", 10.0) or 10.0)
    live_odds = float(runner.get("odds_t15", runner.get("final_odds", morning_odds)) or morning_odds)

    smart_money_score, smart_signal = compute_smart_money_velocity(morning_odds, live_odds)
    press_citations = runner.get("press_citation_count", 0)
    press_score = min(100.0, (press_citations / 25.0) * 100.0)

    implied_prob = (1.0 / max(1.1, live_odds)) if live_odds > 0 else 0.1

    return {
        "num": runner.get("num"),
        "horse_name": runner.get("horse_name", f"Cheval_{runner.get('num')}"),
        "is_non_partant": bool(runner.get("is_non_partant", False)),
        "form_score": music_stats["form_score"],
        "regularity": music_stats["regularity"],
        "dai_rate": music_stats["dai_rate"],
        "shoeing_score": shoeing_score,
        "equip_score": equip_score,
        "track_bias_score": track_bias_score,
        "speed_score": speed_score,
        "smart_money_score": smart_money_score,
        "smart_signal": smart_signal,
        "press_score": press_score,
        "human_score": compute_human_factor_score(runner),
        "morning_odds": morning_odds,
        "odds": live_odds,
        "implied_prob": round(implied_prob, 4)
    }
