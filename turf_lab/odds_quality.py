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


def all_default_odds(runner: Dict[str, Any]) -> bool:
    """True si AUCUN champ de cote du partant ne porte une valeur réelle
    (tous absents ou égaux à DEFAULT_ODDS). Sémantique historique du banc
    de mesure conservée à l'identique."""
    for field in ODDS_FIELDS:
        v = runner.get(field)
        if v is not None and abs(float(v) - DEFAULT_ODDS) > 1e-9:
            return False
    return True


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
