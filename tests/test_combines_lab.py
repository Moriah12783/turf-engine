"""Tests du labo des paris combinés : Harville exact sur petits cas, lecture
des rapports, et contrôles de bout en bout sur un miroir synthétique."""
import io
import itertools
import json
import math
import os
import sqlite3
import sys
import tempfile
from contextlib import redirect_stdout

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import combines_lab as cl
from tests.test_benter_lab import make_mirror


def test_harville_exact_sur_petit_champ():
    p = np.array([0.4, 0.3, 0.2, 0.1])
    for k in (2, 3, 4):
        assert abs(cl.ordered_tensor(p, k).sum() - 1.0) < 1e-12
    single, pair = cl.in_top_k(p, 3)
    assert abs(single.sum() - 3.0) < 1e-12 and abs(np.triu(pair, 1).sum() - 3.0) < 1e-12
    # Force brute : somme des arrivées ordonnées qui contiennent la paire (0, 3) dans les 3 premiers.
    brute = sum(cl.seq_prob(p, s) for s in itertools.permutations(range(4), 3) if 0 in s and 3 in s)
    assert abs(pair[0, 3] - brute) < 1e-12
    assert abs(cl.seq_prob(p, (0, 1)) - 0.4 * 0.3 / 0.6) < 1e-12
    assert abs(cl.set_prob(p, (0, 1)) - (0.4 * 0.3 / 0.6 + 0.3 * 0.4 / 0.7)) < 1e-12
    sets = cl.event_matrix("SET_FIRST3", p, 3)
    assert abs(sum(sets.values()) - 1.0) < 1e-12 and abs(sets[(0, 1, 2)] - cl.set_prob(p, (0, 1, 2))) < 1e-12


def test_classement_des_paris():
    assert cl.classify("SIMPLE_GAGNANT", "Simple gagnant") == "WIN"
    assert cl.classify("E_COUPLE_GAGNANT", "Couplé gagnant") == "PAIR_FIRST2"
    assert cl.classify("TIERCE", "Tiercé Ordre") == "SEQ_FIRST3"
    assert cl.classify("TIERCE", "Tiercé Désordre") == "SET_FIRST3"
    assert cl.classify("QUINTE_PLUS", "Quinté+ Ordre") == "SEQ_FIRST5"
    assert cl.classify("QUINTE_PLUS", "Bonus 4") is None
    assert cl.classify("MULTI", "Multi en 4") == "SET_FIRST4" and cl.classify("MULTI", "Multi en 6") is None
    assert cl.classify("DEUX_SUR_QUATRE", "2sur4") == "PAIR_TOP4"
    assert cl.classify("INCONNU", "x") is None
    assert cl.places_paid(8) == 3 and cl.places_paid(7) == 2 and cl.places_paid(3) == 0


def _add_exotics(path):
    """Rapports « justes au marché avec 25 % de prélèvement » : D = 0,75 / q_marché.
    Plus une ligne discordante et un bonus (inventoriés, jamais analysés)."""
    conn = sqlite3.connect(path)
    races = {}
    for d, r, c, num, cote, pos in conn.execute(
            "SELECT date_course, num_reunion, num_course, num_pmu, cote_direct, ordre_arrivee FROM participants "
            "WHERE date_course >= '2026-01-15' ORDER BY date_course, num_reunion, num_course, num_pmu"):
        races.setdefault((d, r, c), []).append((num, cote, pos))
    rows = []
    for i, ((d, r, c), parts) in enumerate(sorted(races.items())):
        nums = [n for n, _, _ in parts]
        inv = np.array([1.0 / o for _, o, _ in parts])
        p = inv / inv.sum()
        order = [idx for _, idx in sorted((pos, idx) for idx, (_, _, pos) in enumerate(parts))]
        a, b, t = order[:3]
        q_couple = cl.set_prob(p, (a, b))
        rows.append((d, r, c, "COUPLE_GAGNANT", "Couplé gagnant", f"{nums[a]}-{nums[b]}", 0.75 / q_couple))
        _, pair = cl.in_top_k(p, 3)
        for x, y in itertools.combinations((a, b, t), 2):
            rows.append((d, r, c, "COUPLE_PLACE", "Couplé placé", f"{nums[x]}-{nums[y]}", 0.75 / pair[x, y]))
        if i == 500:                                                   # course de février (trois modèles)
            rows[-1] = rows[-1][:5] + ("99-98", 5.0)                   # ligne discordante
        rows.append((d, r, c, "QUINTE_PLUS", "Bonus 4", "1-2-3-4", 3.0))
    conn.executemany("INSERT INTO rapports_definitifs (date_course, num_reunion, num_course, type_pari, libelle, "
                     "combinaison, dividende_pour_1e, nombre_gagnants, rembourse, captured_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, 1, 0, 't')", rows)
    conn.commit()
    conn.close()


@pytest.fixture(scope="module")
def mirror():
    with tempfile.TemporaryDirectory() as d:
        path = make_mirror(os.path.join(d, "h.db"))
        _add_exotics(path)
        yield path


def test_bout_en_bout_controles_exacts(mirror):
    out = io.StringIO()
    with redirect_stdout(out):
        report = cl.run(mirror)
    res = report["resultats"]
    win = res["SIMPLE_GAGNANT|Simple gagnant"]
    # Contrôle : au simple gagnant, le marché de clôture paie exactement 1 / surround.
    assert win["courses"] > 30 and win["G_marche"] < 0
    couple = res["COUPLE_GAGNANT|Couplé gagnant"]
    assert abs(couple["G_marche"] - math.log(0.75)) < 1e-3                # rapports justes à 25 % de prélèvement
    assert couple["ic95_G_marche"][0] <= couple["G_marche"] <= couple["ic95_G_marche"][1]
    place = res["COUPLE_PLACE|Couplé placé"]
    assert abs(place["G_marche"] - math.log(0.75)) < 1e-3 and place["non_concordant"] == 1
    assert "QUINTE_PLUS|Bonus 4" not in res                                # bonus : inventorié seulement
    inventaire = {(e["pari"], e["libelle"]): e for e in report["inventaire"]}
    assert inventaire[("QUINTE_PLUS", "Bonus 4")]["evenement"] is None
    assert set(inventaire[("COUPLE_GAGNANT", "Couplé gagnant")]["formats"]) == {"N-N"}
    for key in ("roi_top_marche", "roi_top_combine"):
        assert set(couple[key]) == {"1", "3", "6", "10"}
    assert "delta_G_combine_vs_marche" in couple and isinstance(couple["bat_la_masse_combine"], bool)
    logs = out.getvalue()
    assert "CHEVAL" not in logs and "JOC" not in logs                     # dépôt public
    assert {line.split(" ")[0] for line in logs.strip().splitlines()} <= {
        "COMBINES_INVENTAIRE", "COMBINES_DONNEES", "COMBINES_RESULTAT"}
