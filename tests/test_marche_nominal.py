"""Ligne de base MARCHÉ : jamais de sélection « par numéro » sans cotes réelles.

Contexte (21/09/2026) : entre 50 et 90 % de partants cotés, la porte de
verrouillage neutralise les cotes vues par les moteurs ; le moteur marché
produisait alors l'ordre des numéros (1-2-3-4-5-6-7-8) qui était affiché et
compté dans les bancs comme un pronostic du marché. Ces tests figent la règle :
sans marché réel, pas de sélection ; une édition archivée sans cotes réelles
est ignorée par le banc.
"""
from turf_lab.baselines import MarketOddsEngine, market_edition_informative
from turf_lab.odds_quality import neutralize_odds


def _runners(odds):
    return [{"num": i + 1, "odds_t15": o, "final_odds": o, "morning_odds": o, "is_non_partant": False}
            for i, o in enumerate(odds)]


def test_marche_reel_classe_par_cote():
    p = MarketOddsEngine().predict({}, _runners([8.0, 3.5, 2.1, 30.0, 6.0, 12.0, 4.4, 9.0]))
    assert p["selection"] == [3, 2, 7, 5, 1, 8, 6, 4]
    assert p["bases"] == [3, 2]
    assert p["metadata"]["market_available"] is True
    assert market_edition_informative(p)


def test_cotes_neutralisees_donnent_selection_vide():
    runners = neutralize_odds(_runners([8.0, 3.5, 2.1, 30.0, 6.0, 12.0, 4.4, 9.0]))
    p = MarketOddsEngine().predict({}, runners)
    assert p["selection"] == [] and p["bases"] == []
    assert len(set(p["probabilities"].values())) == 1          # le marché nominal ne « sait » rien
    assert p["metadata"]["market_available"] is False
    assert not market_edition_informative(p)


def test_cotes_toutes_par_defaut_donnent_selection_vide():
    p = MarketOddsEngine().predict({}, _runners([15.0] * 10))
    assert p["selection"] == []


def test_marche_partiel_favoris_cotes_en_premier():
    # 6 cotés sur 10 (60 %) : marché présent, les non-cotés passent après, par numéro.
    p = MarketOddsEngine().predict({}, _runners([15.0, 3.0, 15.0, 2.0, 6.0, 15.0, 4.0, 15.0, 9.0, 12.0]))
    assert p["selection"] == [4, 2, 7, 5, 9, 10, 1, 3]
    assert p["probabilities"]["1"] == 0.0


def test_edition_archivee_nominale_ignoree():
    # Verrou posé sur cotes neutralisées (porte 50 %) : odds_real = 0.
    assert not market_edition_informative({"selection": [1, 2, 3, 4, 5, 6, 7, 8], "odds_real": 0,
                                           "probabilities": {"1": 0.2, "2": 0.1}})
    # Verrou antérieur à la porte, cotes toutes par défaut : probabilités uniformes.
    assert not market_edition_informative({"selection": [1, 2, 3, 4, 5, 6, 7, 8], "odds_real": None,
                                           "probabilities": {str(i): 0.125 for i in range(1, 9)}})
    # Petit peloton réellement coté dont l'ordre des favoris est 1-2-3-4-5 : édition valide.
    assert market_edition_informative({"selection": [1, 2, 3, 4, 5], "odds_real": 1,
                                       "probabilities": {"1": 0.35, "2": 0.34, "3": 0.18, "4": 0.09, "5": 0.04}})
