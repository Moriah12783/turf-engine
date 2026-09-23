"""Tableau de score « Benter » (turf_lab/combinaison.py) : logit conditionnel
et gain hors échantillon sur des courses synthétiques."""
import math
import random

from turf_lab.combinaison import fit, mean_nll, race_nll


def _synthetic(n_races=400, n_runners=10, seed=7):
    """Courses où le marché est bien calibré et où une source « bruit » n'apporte rien."""
    rng = random.Random(seed)
    races = []
    for i in range(n_races):
        strength = [rng.gauss(0, 1) for _ in range(n_runners)]
        z = sum(math.exp(s) for s in strength)
        p_true = [math.exp(s) / z for s in strength]
        winner = rng.choices(range(n_runners), weights=p_true)[0]
        noise = [rng.gauss(0, 1) for _ in range(n_runners)]
        zn = sum(math.exp(v) for v in noise)
        p_noise = [math.exp(v) / zn for v in noise]
        x = [[math.log(p_true[k]), math.log(p_noise[k])] for k in range(n_runners)]
        races.append({"date": f"2026-09-{1 + i % 28:02d}", "x": x, "y": winner})
    return races


def test_marche_calibre_garde_un_coefficient_proche_de_un():
    races = _synthetic()
    beta = fit(races[:240], [0])
    assert abs(beta[0] - 1.0) < 0.2


def test_source_bruit_recoit_un_poids_nul_et_aucun_gain():
    races = _synthetic()
    train, test = races[:240], races[240:]
    beta = fit(train, [0, 1])
    assert abs(beta[1]) < 0.15
    gain = mean_nll([1.0], test, [0]) - mean_nll(beta, test, [0, 1])
    assert abs(gain) < 0.02


def test_nll_positive_et_finie():
    races = _synthetic(n_races=5)
    for r in races:
        v = race_nll([1.0, 0.0], r, [0, 1])
        assert v > 0 and math.isfinite(v)
