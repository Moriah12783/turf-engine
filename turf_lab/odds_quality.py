"""Qualité des cotes — SOURCE DE VÉRITÉ UNIQUE.

Partagé par la synchronisation (verrou), le banc de mesure (affichage),
le rapport HTML et la porte de publication. La fraîcheur d'une édition se
prouve par les DONNÉES (part de partants réellement cotés), jamais par
l'heure seule.

Incident du 01/09/2026 : une sélection a été diffusée sur une édition dont
toutes les cotes étaient à la valeur par défaut DEFAULT_ODDS (marché pas
encore ouvert). Ce module fournit la mesure qui l'interdit désormais.
"""

from typing import Any, Dict, Iterable, List

from turf_lab.engine import NewValueEngine

# Une seule définition de la cote « par défaut » (engine.py fait foi).
DEFAULT_ODDS: float = NewValueEngine.DEFAULT_ODDS

# Champs de cotes archivés sur chaque partant.
ODDS_FIELDS = ("morning_odds", "odds_t15", "final_odds")

# Seuil BLOQUANT : en dessous, aucun horizon n'est verrouillé et aucune
# sélection n'est diffusée (verrou de fraîcheur, Axe 3 du Plan Value Radar).
MIN_PRICED_RATIO: float = 0.90

# Seuil d'AFFICHAGE (inchangé) : en dessous, le banc de mesure affiche
# « cotes indisponibles » (réunions étrangères hors mutualisation).
MIN_DISPLAY_RATIO: float = 0.50


# Bornes de validité d'une cote PMU (A12) : une valeur hors de cet intervalle
# (0, -1, NaN, infini) n'est JAMAIS une cote réelle, quelle que soit sa
# distance à la sentinelle.
MIN_VALID_ODDS: float = 1.01
MAX_VALID_ODDS: float = 999.0
# Ancienneté maximale d'une capture de cotes pour la DIFFUSION (minutes).
MAX_ODDS_AGE_MINUTES: int = 90


def is_valid_odds(value: Any) -> bool:
    """Cote finie et dans [MIN_VALID_ODDS, MAX_VALID_ODDS]."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False
    if v != v or v in (float("inf"), float("-inf")):
        return False
    return MIN_VALID_ODDS <= v <= MAX_VALID_ODDS


def all_default_odds(runner: Dict[str, Any]) -> bool:
    """True si AUCUN champ de cote du partant ne porte une valeur réelle.

    Provenance d'abord (A12) : si le partant porte ``odds_is_real`` (écrit par
    la synchronisation depuis le flux), c'est elle qui fait foi — une vraie
    cote à 15 reste réelle, une sentinelle n'est jamais réelle. Sinon, repli
    historique : une valeur VALIDE (finie, dans les bornes) et distincte de
    DEFAULT_ODDS. Une cote 0 / -1 / NaN n'est pas « réelle »."""
    flag = runner.get("odds_is_real")
    if flag is not None:
        return not bool(flag)
    for field in ODDS_FIELDS:
        v = runner.get(field)
        if v is not None and is_valid_odds(v) and abs(float(v) - DEFAULT_ODDS) > 1e-9:
            return False
    return True


def odds_age_minutes(runners: Iterable[Dict[str, Any]], now_utc=None) -> "float | None":
    """Âge (minutes) de la capture de cotes la plus récente ; None si inconnu."""
    from datetime import datetime
    now_utc = now_utc or datetime.utcnow()
    latest = None
    for r in runners:
        ts = r.get("odds_captured_at")
        if not ts:
            continue
        try:
            dt = datetime.fromisoformat(str(ts).replace("Z", ""))
        except Exception:
            continue
        if latest is None or dt > latest:
            latest = dt
    if latest is None:
        return None
    return (now_utc - latest).total_seconds() / 60.0


def active_runners(runners: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Partants actifs (non-partants exclus) ; repli sur tous si tous NP."""
    runners = list(runners)
    active = [r for r in runners if not r.get("is_non_partant", False)]
    return active or runners


def priced_ratio(runners: Iterable[Dict[str, Any]]) -> float:
    """Part des partants actifs dont au moins une cote est réelle (≠ DEFAULT_ODDS).
    0.0 si aucun partant ou aucune cote réelle."""
    active = active_runners(runners)
    if not active:
        return 0.0
    priced = sum(1 for r in active if not all_default_odds(r))
    return priced / len(active)


def odds_are_real(runners: Iterable[Dict[str, Any]], threshold: float = MIN_PRICED_RATIO) -> bool:
    """True si la course est suffisamment cotée pour être verrouillée / diffusée."""
    return priced_ratio(runners) >= threshold
