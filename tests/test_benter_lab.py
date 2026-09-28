"""Tests du labo Benter (étape 1 fondamental + étape 2 combinaison). Aucun
réseau, aucune donnée réelle : un miroir SYNTHÉTIQUE dont on connaît la vérité
(capacité cachée des chevaux) remplace l'historique Radar."""
import io
import json
import os
import random
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date, timedelta

import pytest

np = pytest.importorskip("numpy")      # le labo tourne dans GitHub Actions (numpy installé)

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import benter_lab as lab
from turf_lab.history_export import TABLES, HistoryDB
from tests.test_r2_store import BUCKET, FakeS3


def _tuple(table, values):
    return tuple(values.get(c) for c in TABLES[table])


def make_mirror(path, days=200, races_per_day=18, runners=8, seed=7, leak=False, start=date(2025, 8, 1)):
    """Chevaux à capacité cachée ; le gagnant est tiré du vrai logit. Le
    programme (courses, victoires, musique) est figé AVANT la course, sauf si
    ``leak`` (le programme inclut alors la victoire du jour)."""
    rng = random.Random(seed)
    horses = [{"nom": f"CHEVAL {i}", "ability": rng.gauss(0, 1), "courses": 0, "victoires": 0, "hist": []}
              for i in range(500)]
    db = HistoryDB(path)
    for d in range(days):
        day = (start + timedelta(days=d)).isoformat()
        courses, parts, arrs, raps, snaps = [], [], [], [], []
        pool = rng.sample(horses, races_per_day * runners)
        for rc in range(races_per_day):
            field = pool[rc * runners:(rc + 1) * runners]
            w = [np.exp(h["ability"]) for h in field]
            winner = rng.choices(range(runners), weights=w)[0]
            order = [winner] + rng.sample([i for i in range(runners) if i != winner], runners - 1)
            pos = {idx: k + 1 for k, idx in enumerate(order)}
            spec = "TROT_ATTELE" if rc % 2 else "PLAT"
            courses.append(_tuple("courses", {
                "id": d * 100 + rc, "date_course": day, "num_reunion": 1, "num_course": rc + 1,
                "specialite": spec, "discipline": "ATTELE" if rc % 2 else "PLAT",
                "heure_depart": f"{day}T{10 + rc // 4:02d}:{(rc % 4) * 15:02d}:00+00:00",
                "statut": "ARRIVEE_DEFINITIVE_COMPLETE", "annulee": 0}))
            arrs.append(_tuple("arrivees", {"id": d * 100 + rc, "date_course": day, "num_reunion": 1,
                                            "num_course": rc + 1,
                                            "ordre_arrivee": json.dumps([[order[0] + 1], [order[1] + 1]])}))
            base = [h["ability"] + rng.gauss(0, 0.8) for h in field]              # ce que sait la clôture
            extra = [rng.gauss(0, 1.2) for _ in field]                             # bruit résorbé avant le départ
            noisy = [np.exp(b) for b in base]                                      # clôture
            early = [np.exp(b + e) for b, e in zip(base, extra)]                   # référence, moins informée
            total, total_early = sum(noisy), sum(early)
            close = [max(1.1, round(total / x * 0.85, 1)) for x in noisy]         # prélèvement 15 %
            for minute in (45, 32, 17, 6, 2):              # photos : de la référence vers la clôture
                mixed = [np.exp(b + e * minute / 60.0) for b, e in zip(base, extra)]  # T-x : entre les deux
                tot = sum(mixed)
                for i, x in enumerate(mixed):
                    snaps.append(_tuple("cotes_snapshots", {
                        "id": len(snaps) + d * 100000 + rc * 1000, "date_course": day, "num_reunion": 1,
                        "num_course": rc + 1, "num_pmu": i + 1, "cote": max(1.1, round(tot / x * 0.85, 1)),
                        "minutes_avant_depart": minute}))
            raps.append(_tuple("rapports_definitifs", {
                "date_course": day, "num_reunion": 1, "num_course": rc + 1, "type_pari": "SIMPLE_GAGNANT",
                "libelle": "Simple gagnant", "combinaison": str(winner + 1),
                "dividende_pour_1e": close[winner], "rembourse": 0}))
            for i, h in enumerate(field):
                won_today = int(i == winner)
                parts.append(_tuple("participants", {
                    "id": len(parts) + d * 10000, "date_course": day, "num_reunion": 1, "num_course": rc + 1,
                    "num_pmu": i + 1, "nom": h["nom"], "nom_pere": "PERE", "nom_mere": "MERE", "age": 5,
                    "sexe": "HONGRES", "statut": "PARTANT", "oeilleres": "SANS_OEILLERES",
                    "entraineur": f"ENT {hash(h['nom']) % 40}", "driver": f"JOC {rng.randrange(60)}",
                    "driver_change": 0, "musique": "".join(f"{min(p, 9)}a" for p in h["hist"][:6]),
                    "nombre_courses": h["courses"] + (1 if leak else 0),
                    "nombre_victoires": h["victoires"] + (won_today if leak else 0),
                    "nombre_places": 0, "gains_carriere": 1000 * h["victoires"], "gains_annee_en_cours": 0,
                    "handicap_poids": 560 + rng.randrange(40) if spec == "PLAT" else None,
                    "place_corde": i + 1 if spec == "PLAT" else None,
                    "cote_direct": close[i], "cote_reference": max(1.1, round(total_early / early[i] * 0.85, 1)),
                    "ordre_arrivee": pos[i]}))
            for i, h in enumerate(field):
                h["courses"] += 1
                h["victoires"] += int(i == winner)
                h["hist"].insert(0, pos[i])
        db.replace_date("courses", day, courses, "t")
        db.replace_date("arrivees", day, arrs, "t")
        db.replace_date("participants", day, parts, "t")
        if d >= days - 60:                               # photos de cotes : les 60 derniers jours
            db.replace_date("cotes_snapshots", day, snaps, "t")
        if d % 2 == 0:                                   # rapports officiels : un jour sur deux
            db.replace_date("rapports_definitifs", day, raps, "t")
    db.close()
    return path


@pytest.fixture(scope="module")
def mirror():
    with tempfile.TemporaryDirectory() as d:
        yield make_mirror(os.path.join(d, "h.db"))


# ── Briques ─────────────────────────────────────────────────────────────
def test_musique():
    m = lab.parse_musique("1a2a(25)Da0a3a")
    assert m == {"mus_moyenne": 5.6, "mus_victoires": 1.0, "mus_top3": 3.0, "mus_fautes": 1.0,
                 "mus_derniere": 1.0, "mus_absente": 0.0}
    assert lab.parse_musique(None)["mus_absente"] == 1.0


def test_logit_conditionnel_retrouve_les_vrais_coefficients():
    rng = np.random.default_rng(1)
    truth = np.array([1.0, -0.5, 0.0])
    blocks, winners = [], []
    for _ in range(3000):
        x = rng.normal(size=(10, 3))
        p = np.exp(x @ truth)
        blocks.append(x)
        winners.append(int(rng.choice(10, p=p / p.sum())))
    packed = lab.Packed(blocks, winners)
    beta = lab.fit_clogit(packed, ridge=1e-3)
    assert np.allclose(beta, truth, atol=0.12)
    probs = packed.probs(beta)
    assert np.allclose(np.add.reduceat(probs, packed.starts), 1.0)


def test_bootstrap_ic_encadre_la_moyenne():
    values = np.random.default_rng(3).normal(0.02, 0.5, size=4000)
    lo, hi = lab.bootstrap_ci(values)
    assert lo < values.mean() < hi and hi - lo < 0.05


# ── Chaîne complète sur le miroir synthétique ───────────────────────────
def test_chargement_courses_et_gagnants(mirror):
    races, stats = lab.load_races(mirror)
    assert stats["courses_retenues"] == 200 * 18 and stats["partants"] == 200 * 18 * 8
    assert stats["courses_trot"] == stats["courses_galop"]
    assert all(r.runners[r.winner]["ordre_arrivee"] == 1 for r in races)
    assert all(r.market is not None and abs(r.market.sum() - 1) < 1e-9 for r in races)
    assert all(r.market_ref is not None for r in races)
    assert stats["courses_avec_rapport_officiel"] == 100 * 18
    assert all(r.dividend == r.odds[r.winner] for r in races if r.dividend is not None)
    assert 1.1 < stats["surround_moyen_cloture"] < 1.25                  # prélèvement ~15 %
    days = [r.day for r in races]
    assert days == sorted(days)                                         # ordre chronologique


def test_audit_de_fuite(mirror):
    races, _ = lab.load_races(mirror)
    audit = lab.build_features(races)
    assert audit["fuite_suspectee"] is False
    assert audit["delta_victoires_apres_victoire"] > 0.9                # programme figé avant la course
    with tempfile.TemporaryDirectory() as d:
        leaky, _ = lab.load_races(make_mirror(os.path.join(d, "fuite.db"), days=40, leak=True))
        assert lab.build_features(leaky)["fuite_suspectee"] is True


def test_variables_figees_a_la_veille(mirror):
    races, _ = lab.load_races(mirror)
    lab.build_features(races)
    first_day = races[0].day
    jockey = lab.FEATURES.index("jockey_log_montes")
    premiere = lab.FEATURES.index("premiere_vue")
    same_day = [r for r in races if r.day == first_day]
    # Premier jour : aucune statistique, même pour la dernière course du jour.
    assert all(r.features[:, jockey].max() == 0 and r.features[:, premiere].min() == 1 for r in same_day)


def test_rapport_complet_sans_fuite_dans_le_journal(mirror):
    out = io.StringIO()
    with redirect_stdout(out):
        report = lab.run(mirror)
    logs = out.getvalue()
    plis = report["plis"]
    assert [p["mois"] for p in plis] == ["2026-01", "2026-02"]
    # Le vrai signal (capacité → victoires passées) est appris : mieux que le hasard.
    assert all(p["ll_fondamental"] > p["ll_uniforme"] + 0.05 for p in plis)
    # Combinaison apprise sur les mois PRÉCÉDENTS seulement : pas de 1er mois.
    assert "ll_combine" not in plis[0] and "ll_combine" in plis[1]
    comb = report["combinaison"]
    assert comb["courses_jugees"] == plis[1]["courses_avec_marche"]
    assert comb["ic95_vs_marche_recalibre"][0] <= comb["delta_ll_par_course_vs_marche_recalibre"] \
        <= comb["ic95_vs_marche_recalibre"][1]
    assert set(report["coefficients_dernier_pli"]) >= {"TROT", "GALOP"}
    assert report["combinaison_marche_reference"]["courses_jugees"] > 0
    assert report["kelly"]["paris"] > 0 and report["kelly"]["roi_mise_fixe"] is not None
    # Témoin (marché seul recalibré) : présent ; sur un vrai marché calibré (γ ≈ 1) il ne mise presque
    # rien, mais le marché synthétique, bruité donc mal calibré, laisse du jeu au simple recalibrage.
    assert {"paris", "roi_mise_fixe"} <= set(report["kelly_temoin_marche_seul"])
    # Dépôt public : le journal ne contient ni cheval, ni jockey, ni coefficient.
    for secret in ("CHEVAL", "JOC ", "ENT ", "taux_victoires", "coefficients"):
        assert secret not in logs
    assert {line.split(" ")[0] for line in logs.strip().splitlines()} <= {
        "BENTER_DONNEES", "BENTER_AUDIT_FUITE", "BENTER_PLI", "BENTER_RESULTAT", "BENTER_RESULTAT_REFERENCE",
        "BENTER_KELLY", "BENTER_KELLY_TEMOIN", "BENTER_TX"}
    for h in ("T30", "T15", "T5"):
        tx = report["horizons_de_pari"][h]
        assert tx["courses_jugees"] > 0 and tx["ic95"][0] <= tx["delta_ll_vs_marche_T_recalibre"] <= tx["ic95"][1]
        # Les cotes glissent vers la clôture : l'information tardive est positive.
        assert tx["gain_ll_cloture_vs_T"] > 0
        assert "paris" in tx["kelly"] and "paris" in tx["kelly_temoin_marche_seul"]
    assert "_comb" not in report and "_recal" not in report                # probabilités par course : jamais


def test_envoi_sur_r2_prive(monkeypatch, mirror):
    report = {"genere_le_utc": "2026-09-28T08:00:00Z", "plis": []}
    for key in ("R2_ACCOUNT_ID", "CLOUDFLARE_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "R2_BUCKET"):
        monkeypatch.delenv(key, raising=False)
    assert lab.upload(report) is None                                   # sans secrets : rien n'est envoyé
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acc")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "k")
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "s")
    monkeypatch.setenv("R2_BUCKET", BUCKET)
    monkeypatch.setenv("GITHUB_RUN_ID", "42")
    s3 = FakeS3()
    key = lab.upload(report, client_factory=lambda cfg: s3)
    assert key == "lab/benter/fondamental_2026-09-28_42.json"
    assert set(s3.objects) == {key, "lab/benter/dernier.json"}
    assert json.loads(s3.objects[key][0])["genere_le_utc"] == report["genere_le_utc"]


def _race(day, odds, winner, dividend=None):
    race = lab.Race((day, 1, 1), day, "TROT")
    race.runners = [{"num_pmu": i + 1} for i in range(len(odds))]
    race.winner, race.odds, race.dividend = winner, np.array(odds, dtype=float), dividend
    return race


def test_simulation_kelly_cas_calcules_a_la_main():
    gagne = _race("2026-02-01", [2.0, 4.0, 8.0], winner=1, dividend=4.2)
    perd = _race("2026-02-02", [2.0, 4.0, 8.0], winner=0)
    probs = {gagne.key: np.array([0.40, 0.35, 0.25]), perd.key: np.array([0.40, 0.35, 0.25])}
    # Espérances : 0.40·2 − 1 = −0.20 ; 0.35·4 − 1 = +0.40 ; 0.25·8 − 1 = +1.00 → 2 paris par course.
    out = lab.simulate_kelly([gagne, perd], probs)
    assert out["paris"] == 4 and out["courses_jouees"] == 2
    assert out["roi_mise_fixe"] == round(4.2 / 4 - 1, 4)                   # réglé au rapport officiel 4,2
    assert out["reglement"] == {"rapport_officiel": 1, "cote_finale": 0}
    # Kelly : f = 0.25·[0.40/3, 1.00/7] = [0.0333, 0.0357] → total 6,9 % > 5 % : ramené à 5 %.
    f = 0.25 * np.array([0.40 / 3, 1.00 / 7])
    f *= 0.05 / f.sum()
    b1 = 1.0 + f[0] * 4.2 - f.sum()
    assert out["kelly_bankroll_finale"] == round(b1 * (1 - 0.05), 4)
    assert out["kelly_drawdown_max"] == round(0.05, 4)
    assert out["roi_par_cote"]["cote_<5"]["paris"] == 2 and out["roi_par_cote"]["cote_5-15"]["paris"] == 2


def test_simulation_sans_esperance_ne_mise_rien():
    race = _race("2026-02-01", [1.6, 3.2, 6.4], winner=0)                # Σ 1/cote = 1,09 : prélèvement
    fair = 1 / race.odds / (1 / race.odds).sum()                         # le marché lui-même, sans avantage
    out = lab.simulate_kelly([race], {race.key: fair})
    assert out["paris"] == 0 and out["roi_mise_fixe"] is None


def test_choix_de_la_photo_de_cotes():
    assert lab.pick_snapshot([45, 32, 17, 2], 30) == 32
    assert lab.pick_snapshot([45, 32, 17, 2], 15) == 17
    assert lab.pick_snapshot([45, 3], 30) is None                         # 45 min : trop ancienne pour T-30
    assert lab.pick_snapshot([29, 3], 30) is None                         # jamais une photo prise après T-30


def test_kelly_decide_a_T_regle_au_rapport_final():
    race = _race("2026-08-01", [1.6, 3.2, 6.4], winner=2, dividend=9.0)
    race.odds_tx = {30: np.array([2.0, 3.0, 12.0])}                       # cotes à T-30
    probs = {race.key: np.array([0.40, 0.35, 0.25])}
    # À T-30 : 0.25·12 − 1 = +2.0 ; 0.35·3 − 1 = +0.05 (pas > 5 %) ; 0.40·2 − 1 = −0.2 → un seul pari.
    out = lab.simulate_kelly([race], probs, lambda r: r.odds_tx.get(30))
    assert out["paris"] == 1 and out["roi_mise_fixe"] == round(9.0 - 1, 4)   # payé 9,0 (final), pas 12
    assert out["roi_par_cote"]["cote_5-15"]["paris"] == 1                  # tranche selon la cote de décision


def _bench(path, races, editions):
    """Base de banc minimale au format de turf_bench.db. ``editions`` :
    (moteur, horizon) -> fonction(course) -> (probabilités, métadonnées, sélection, odds_real) ou None."""
    import sqlite3
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE predictions (prediction_id TEXT, race_id TEXT, engine_name TEXT, horizon TEXT, "
                 "probabilities_json TEXT, metadata_json TEXT, selection_json TEXT, odds_real INTEGER)")
    for r in races:
        d = date.fromisoformat(r.day)
        race_id = f"R{r.key[1]}C{r.key[2]}_{d.strftime('%d%m%Y')}_SYNTH"
        for (engine, horizon), fn in editions.items():
            ed = fn(r)
            if ed is not None:
                probs, meta, selection, odds_real = ed
                conn.execute("INSERT INTO predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                             (f"{race_id}_{engine}_{horizon}", race_id, engine, horizon, json.dumps(probs),
                              json.dumps(meta), json.dumps(selection), odds_real))
    conn.commit()
    conn.close()


def test_apport_du_fondamental_aux_moteurs_du_banc(mirror):
    races, _ = lab.load_races(mirror)
    lab.build_features(races)
    wf = lab.fundamental_walk_forward(races, list(lab.FEATURES))
    recent = [r for r in races if r.day >= "2026-01-01"]

    def uniform(r):
        return {str(x["num_pmu"]): 1.0 for x in r.runners}

    def nve(r):                                   # une édition sur deux « de production » (poids 0,9)
        prod = r.key[2] % 2 == 0
        meta = {"market_calibration": {"applied": prod, "market_weight": 0.9 if prod else 0.7},
                "model_probs": uniform(r)}
        return uniform(r), meta, [1, 2, 3], 1

    def market(r):                                # une édition sur trois nominale (sélection vide)
        nominal = r.key[2] % 3 == 0
        return uniform(r), {"market_available": not nominal}, [] if nominal else [1, 2], 0 if nominal else 1

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "turf_bench.db")
        _bench(path, recent, {("NEW_VALUE_ENGINE", "T_MATIN"): nve, ("NEW_VALUE_ENGINE", "T30"): nve,
                              ("MARKET_BASELINE", "T_MATIN"): market})
        report = lab.evaluate_bench(races, wf["oos"], lab.load_bench(path))
    toutes, prod = report["NEW_VALUE_ENGINE_T_MATIN_toutes"], report["NEW_VALUE_ENGINE_T_MATIN_production_poids_0.9"]
    assert toutes["apport_demontre"] is True and toutes["gain_top1"] > 0
    assert toutes["ic95_gain_top1"][0] <= toutes["gain_top1"] <= toutes["ic95_gain_top1"][1]
    assert prod["courses"] < toutes["courses"] and prod["editions_exclues"] > 0
    assert set(prod["composition"]) == {"poids_0.90"}
    # Apprise sur toutes les éditions NVE passées, jugée sur les seules éditions de production.
    assert 0 < prod["courses_jugees"] < toutes["courses_jugees"]
    assert set(toutes["composition"]) == {"poids_0.90", "sans_marche"}
    informatives = report["MARKET_BASELINE_T_MATIN_informatives"]
    assert informatives["editions_exclues"] > 0
    assert informatives["courses"] + informatives["editions_exclues"] == report["MARKET_BASELINE_T_MATIN_toutes"]["courses"]
    assert report["NEW_VALUE_ENGINE_T90_toutes"]["courses"] == 0              # horizon absent du banc
    pur = report["FONDAMENTAL_vs_NVE_PUR_T_MATIN_toutes"]
    assert pur["courses"] > 0 and pur["delta_ll_fondamental_moins_nve_pur"] > 0 and pur["ic95"][0] > 0
    assert report["FONDAMENTAL_vs_NVE_PUR_T_MATIN_production_poids_0.9"]["courses"] < pur["courses"]


# ── Ombre du fondamental : outils de lecture et puissance avant le gel ──
def test_outils_de_l_ombre():
    import math
    from turf_lab import ombre
    # Réunions homogènes : l'intervalle par réunion est plus large que par course.
    vals = [1.0] * 10 + [0.0] * 10 + [1.0] * 10 + [0.0] * 10
    lo_c, hi_c = ombre.intervalle(vals, list(range(40)), 0.95)
    lo_r, hi_r = ombre.intervalle(vals, [i // 10 for i in range(40)], 0.95)
    assert lo_r <= 0.5 <= hi_r and (hi_r - lo_r) > (hi_c - lo_c)
    lo99, hi99 = ombre.intervalle(vals, list(range(40)), 0.99)
    assert lo99 <= lo_c and hi99 >= hi_c
    # Puissance : 50 % quand l'effet vaut exactement z × erreur-type projetée ; croît avec n.
    se = 0.02
    effet = ombre.z_bilateral(0.95) * se * math.sqrt(400 / 2300)
    assert abs(ombre.puissance(effet, se, 400, 2300, 0.95) - 0.5) < 1e-9
    assert ombre.puissance(0.01, se, 400, 2300, 0.95) > ombre.puissance(0.01, se, 400, 1000, 0.95)
    assert ombre.puissance(-0.01, se, 400, 2300, 0.95) < 0.025
    # Part marché d'une édition publiée, recettes A et B, « dans les 8 ».
    m, g, f = np.array([0.5, 0.3, 0.2]), np.array([0.2, 0.2, 0.6]), np.array([0.6, 0.3, 0.1])
    assert np.allclose(ombre.marche_de_edition(0.9 * m + 0.1 * g, g, 0.9), m)
    assert np.allclose(ombre.ombre_lineaire(m, f), 0.9 * m + 0.1 * f)
    assert np.allclose(ombre.ombre_loglineaire(m, f, 1.0, 0.0), m)
    assert ombre.top8(list(range(1, 11)), np.arange(10)[::-1] / 45.0) == list(range(1, 9))
    assert ombre.dans_les_8([3, 1, 2, 4, 5, 6, 7, 8, 9], [1, 2])
    assert not ombre.dans_les_8([3, 1, 2, 4, 5, 6, 7, 8, 9], [9])


def test_puissance_de_l_ombre_avant_gel(mirror):
    races, _ = lab.load_races(mirror)
    lab.build_features(races)
    matin = [n for n in lab.FEATURES if n not in lab.INTRADAY_FEATURES]
    wf = lab.fundamental_walk_forward(races, matin)
    recent = [r for r in races if r.day >= "2025-12-01"]

    def nve(r):
        # Marché du matin = cote de référence (moins informée) ; modèle pur sans information.
        if r.market_ref is None:
            return None
        w = 0.9 if r.day >= "2026-01-15" else 0.7          # production (poids 0,9) à partir du 15/01
        nums = [int(x["num_pmu"]) for x in r.runners]
        g = np.full(len(nums), 1.0 / len(nums))
        pub = w * r.market_ref + (1 - w) * g
        probs = {str(n): round(float(p), 4) for n, p in zip(nums, pub)}
        if r.key[2] == 5:
            probs["99"] = 0.01                               # non-partant tardif : hors calcul
        meta = {"market_calibration": {"applied": True, "market_weight": w},
                "model_probs": {str(n): round(float(x), 4) for n, x in zip(nums, g)}}
        return probs, meta, [nums[i] for i in np.argsort(-pub)[:10]], 1

    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "turf_bench.db")
        _bench(path, recent, {("NEW_VALUE_ENGINE", "T_MATIN"): nve, ("NEW_VALUE_ENGINE", "T15"): nve})
        bench = lab.load_bench(path, lab.BENCH_HORIZONS + ("T15",))
    rep = lab.evaluate_shadow_power(races, wf["oos"], bench)
    m = rep["T_MATIN"]
    assert m["exclusions"]["partants_differents_au_verrou"] > 0
    a, b, b0 = m["A"], m["B"], m["B0"]
    assert a["editions"] == m["editions_production"] > 0
    # Le fondamental remplace un modèle pur sans information : l'ombre A gagne.
    assert a["delta_ll"] > 0 and a["ic95"][0] <= a["delta_ll"] <= a["ic95"][1]
    assert a["ic99"][0] <= a["ic95"][0] and a["ic99"][1] >= a["ic95"][1]
    assert 0.0 <= a["puissance_2300_ic95"] <= 1.0 and isinstance(a["detectable_2300"], bool)
    assert a["puissance_2300_ic95"] >= a["puissance_1000_ic99"]
    # B apprise sur les seules éditions passées (toutes, poids 0,7 compris), jugée en production.
    assert 0 < b["editions"] < a["editions"] and b["poids_moyens"]["fondamental"] > 0
    assert b0["editions"] == b["editions"] and "gagnant_dans_8" not in b0
    for recipe in (a, b):
        for crit in ("gagnant_dans_8", "tierce_dans_8"):
            s = recipe[crit]
            assert s["editions"] > 0 and s["ic95"][0] <= s["ecart"] <= s["ic95"][1]
            assert s["borne_basse_projetee_2300"] >= s["borne_basse_projetee_1000"]
    assert rep["T15"]["A"]["editions"] > 0 and rep["T90"]["editions_avec_marche"] == 0
    expected = "A_lineaire_0.90_0.10" if a["detectable_2300"] else "B_loglineaire"
    assert rep["recette_principale_selon_regle"] == expected
    assert "CHEVAL" not in json.dumps(rep) and "JOC" not in json.dumps(rep)
