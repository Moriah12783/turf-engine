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
        courses, parts, arrs = [], [], []
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
            noisy = [np.exp(h["ability"] + rng.gauss(0, 0.8)) for h in field]
            total = sum(noisy)
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
                    "cote_reference": round(total / noisy[i] / 0.85, 1), "ordre_arrivee": pos[i]}))
            for i, h in enumerate(field):
                h["courses"] += 1
                h["victoires"] += int(i == winner)
                h["hist"].insert(0, pos[i])
        db.replace_date("courses", day, courses, "t")
        db.replace_date("arrivees", day, arrs, "t")
        db.replace_date("participants", day, parts, "t")
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
    # Dépôt public : le journal ne contient ni cheval, ni jockey, ni coefficient.
    for secret in ("CHEVAL", "JOC ", "ENT ", "taux_victoires", "coefficients"):
        assert secret not in logs
    assert {line.split(" ")[0] for line in logs.strip().splitlines()} <= {
        "BENTER_DONNEES", "BENTER_AUDIT_FUITE", "BENTER_PLI", "BENTER_RESULTAT"}


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
