"""Lecture scellée de la règle d'arrêt de la recette A (docs/REGLE_ARRET.md).

Décision écrite de Steph du 13/11/2026 : retour au moteur actuel si, sur
30 jours de production, tout l'intervalle de confiance du gain passe sous
zéro. Tests : constantes identiques au document, contrat avec le compteur et
le moteur, aucune lecture intermédiaire, lecture unique, gain sur les seules
éditions en recette A, exclusions, sortie en agrégats, workflow à exécution
unique.
"""
import hashlib
import io
import json
import os
import re
import sqlite3
import sys
from contextlib import redirect_stdout
from datetime import date, timedelta

import pytest

np = pytest.importorskip("numpy")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import fondamental_nuit, ombre, recette_compteur as rc, regle_arret as ra

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(RACINE, "docs", "REGLE_ARRET.md")
WORKFLOW = os.path.join(RACINE, ".github", "workflows", "regle_arret_lecture.yml")
VEILLE = "2026-12-29"                       # 30e jour : encore aucune lecture
LECTURE = "2026-12-30"


def _softmax(x):
    e = np.exp(x - x.max())
    return e / e.sum()


def _arrondi(p):
    return {str(i + 1): round(float(v), 4) for i, v in enumerate(p)}


def _meta_a(temoin, version_nve=None, sans_temoin_cle=None, recette=None):
    tm = {"nve_version": ra.NVE_TEMOIN, "probabilities": dict(temoin),
          "selection": sorted(temoin, key=lambda k: -temoin[k])[:10]}
    if sans_temoin_cle:
        tm["probabilities"].pop(sans_temoin_cle)
    return {"market_calibration": {"applied": True, "market_weight": 0.9, "coverage_pct": 100.0},
            "recette": recette or ra.RECETTE_A, "nve_version": version_nve or ra.NVE_RECETTE_A,
            "fondamental_model_version": ra.MODELE_ATTENDU, ra.META_TEMOIN: tm}


def make_bench(path, gain="positif", jours=30, per_day=20, debut=ra.DEBUT_PRODUCTION, seed=7, repli_every=None,
               variantes=None, hors_fenetre=False, une_reunion=False):
    """``jours`` jours de ``per_day`` courses de 10 partants (réunions de 10
    courses, une seule si ``une_reunion``). Recette A publiée plus proche de
    la vérité que le témoin (``positif``), l'inverse (``negatif``) ou
    identique (``nul``). ``variantes`` : {index de course: cas d'exclusion}."""
    rng = np.random.default_rng(seed)
    variantes = variantes or {}
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE predictions (prediction_id TEXT, race_id TEXT, engine_name TEXT, horizon TEXT, "
                 "lock_time TEXT, selection_json TEXT, probabilities_json TEXT, metadata_json TEXT)")
    conn.execute("CREATE TABLE race_results (race_id TEXT, statut TEXT, ranking_json TEXT, arrival_order_json TEXT)")

    def course(i, day, k, sens):
        r, c = (1, k + 1) if une_reunion else (1 + k // 10, 1 + k % 10)
        race_id = f"R{r}C{c}_{day.strftime('%d%m%Y')}_SYN"
        s = rng.normal(0, 1.2, 10)
        order = rng.choice(10, size=10, replace=False, p=_softmax(s))
        proche = _softmax(s + rng.normal(0, 0.3, 10))
        loin = _softmax(0.3 * s + rng.normal(0, 1.0, 10))
        pub, tem = {"positif": (proche, loin), "negatif": (loin, proche), "nul": (proche, proche)}[sens]
        pub, tem = _arrondi(pub), _arrondi(tem)
        cas = variantes.get(i)
        meta = _meta_a(tem)
        if repli_every and i % repli_every == 0:
            meta = {"market_calibration": {"applied": True, "market_weight": 0.9}, "recette": ra.RECETTE_REPLI,
                    "motif_repli": "TABLE_ABSENTE", "nve_version": ra.NVE_TEMOIN}
        elif cas == "temoin_incomplet":
            meta = _meta_a(tem, sans_temoin_cle="1")
        elif cas == "cle_differente":
            meta = _meta_a(tem, version_nve="nve-2026-12-15")
        elif cas == "marqueur_absent":
            meta.pop("recette")
        elif cas == "marche_partiel":
            meta["market_calibration"] = {"applied": False, "market_weight": 0.0}
        elif cas == "gagnant_absent":
            pub.pop(str(int(order[0]) + 1))
            meta = _meta_a({k: v for k, v in tem.items() if k in pub})
        lock = f"{day.isoformat()}T07:30:{k:02d}"
        conn.execute("INSERT INTO predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (f"p{i}", race_id, "NEW_VALUE_ENGINE", "T_MATIN", lock, json.dumps([1, 2, 3, 4, 5, 6, 7, 8]),
                      json.dumps(pub), json.dumps(meta)))
        conn.execute("INSERT INTO predictions VALUES (?, ?, ?, ?, ?, ?, ?, ?)",       # autre horizon : ignoré
                     (f"q{i}", race_id, "NEW_VALUE_ENGINE", "T90", lock, "[1]", json.dumps(tem), json.dumps(meta)))
        if cas == "sans_arrivee":
            return
        ranking = [{"rang": rang + 1, "num": int(n) + 1} for rang, n in enumerate(order)]
        if cas == "dead_heat":
            ranking[1]["rang"] = 1
        conn.execute("INSERT INTO race_results VALUES (?, ?, ?, ?)", (race_id, "DEFINITIVE", json.dumps(ranking), None))

    i = 0
    for d in range(jours):
        day = date.fromisoformat(debut) + timedelta(days=d)
        for k in range(per_day):
            course(i, day, k, gain)
            i += 1
    if hors_fenetre:                 # veille du premier jour et lendemain du 30e : gain très négatif, ignoré
        for day in (date(2026, 11, 29), date(2026, 12, 30)):
            for k in range(per_day):
                course(10_000 + k, day, k, "negatif")
    conn.commit()
    conn.close()
    return path


def _lire(path, today=LECTURE):
    out = io.StringIO()
    with redirect_stdout(out):
        r = ra.read(path, today=today)
    return r, out.getvalue()


# ── Règle gelée ───────────────────────────────────────────────────────────
def test_constantes_identiques_au_document():
    rows = dict(re.findall(r"^\| ([A-Z_0-9]+) \| (.+?) \|$", open(DOC, encoding="utf-8").read(), re.M))
    for nom in ("DEBUT_PRODUCTION", "FIN_FENETRE", "LECTURE_LE", "HORIZON", "RECETTE_A", "RECETTE_REPLI",
                "NVE_RECETTE_A", "NVE_TEMOIN", "MODELE_ATTENDU", "META_TEMOIN"):
        assert rows[nom] == getattr(ra, nom), nom
    assert int(rows["DUREE_JOURS"]) == ra.DUREE_JOURS == 30
    assert float(rows["NIVEAU"]) == ra.NIVEAU == ombre.NIVEAU_LECTURE
    assert int(rows["REUNIONS_MIN"]) == ra.REUNIONS_MIN
    assert int(rows["BOOTSTRAP_TIRAGES"]) == ombre.BOOTSTRAP_TIRAGES and int(rows["GRAINE"]) == ombre.GRAINE
    debut, fin = date.fromisoformat(ra.DEBUT_PRODUCTION), date.fromisoformat(ra.FIN_FENETRE)
    assert (fin - debut).days + 1 == ra.DUREE_JOURS
    assert date.fromisoformat(ra.LECTURE_LE) == fin + timedelta(days=1)


def test_contrat_avec_le_compteur_et_le_moteur():
    from turf_lab import engine
    assert (ra.DEBUT_PRODUCTION, ra.RECETTE_A, ra.RECETTE_REPLI, ra.NVE_RECETTE_A, ra.MODELE_ATTENDU) == (
        rc.DEBUT_PRODUCTION, rc.RECETTE_A, rc.RECETTE_REPLI, rc.NVE_RECETTE_A, rc.MODELE_ATTENDU)
    assert ra.NVE_TEMOIN == rc.NVE_REPLI == engine.NVE_VERSION        # le témoin est le moteur actuel
    assert ra.RECETTE_A == ombre.RECETTE
    assert ra.MODELE_ATTENDU == fondamental_nuit.model_version()      # fondamental figé pendant la fenêtre
    # Une fois le paquet moteur fusionné, ses constantes doivent concorder.
    for nom, attendu in (("RECETTE_A_DEBUT", ra.DEBUT_PRODUCTION), ("RECETTE_REPLI", ra.RECETTE_REPLI),
                         ("NVE_VERSION_RECETTE_A", ra.NVE_RECETTE_A), ("TEMOIN_META", ra.META_TEMOIN)):
        if hasattr(engine, nom):
            assert getattr(engine, nom) == attendu, nom


# ── Aucune lecture intermédiaire ──────────────────────────────────────────
def test_avant_le_30_12_compteur_seul(tmp_path):
    r, texte = _lire(make_bench(str(tmp_path / "b.db")), today=VEILLE)
    assert "lecture" not in r and r["prochaine_lecture"].startswith("le 2026-12-30")
    assert texte.startswith("REGLE_ARRET_COMPTEUR ") and "REGLE_ARRET_LECTURE" not in texte
    assert (r["editions_eligibles"], r["editions_recette_a_lisibles"], r["jours_avec_edition"]) == (600, 600, 30)
    for interdit in ("gain_ll", "ic95", "decision", "empreinte"):
        assert interdit not in texte, interdit


def test_la_date_de_lecture_n_est_pas_reglable(tmp_path):
    path = make_bench(str(tmp_path / "b.db"), jours=2)
    for option in ("--today", "--jour", "--date"):
        with pytest.raises(SystemExit):
            ra.main(["--banc", path, option, "2027-01-01"])
    out = io.StringIO()
    with redirect_stdout(out):
        assert ra.main(["--banc", path]) == 0


# ── Lecture et décision ───────────────────────────────────────────────────
def test_gain_positif_maintien(tmp_path):
    r, texte = _lire(make_bench(str(tmp_path / "b.db")))
    lu = r["lecture"]
    assert texte.startswith("REGLE_ARRET_LECTURE ")
    assert (lu["editions"], lu["reunions"], lu["part_recette_a"]) == (600, 60, 1.0)
    assert (lu["premier_jour"], lu["dernier_jour"]) == (ra.DEBUT_PRODUCTION, ra.FIN_FENETRE)
    assert lu["gain_ll"] > 0 and lu["ic95"][0] > 0 and lu["decision"] == ra.DECISION_MAINTIEN


def test_tout_l_intervalle_sous_zero_retour(tmp_path):
    lu = _lire(make_bench(str(tmp_path / "b.db"), gain="negatif"))[0]["lecture"]
    assert lu["ic95"][1] < 0 and lu["decision"] == ra.DECISION_RETOUR == "RETOUR_AU_MOTEUR_ACTUEL"


def test_intervalle_a_zero_maintien(tmp_path):
    """Borne haute nulle : l'intervalle ne passe pas tout entier sous zéro."""
    lu = _lire(make_bench(str(tmp_path / "b.db"), gain="nul"))[0]["lecture"]
    assert lu["ic95"] == [0.0, 0.0] and lu["decision"] == ra.DECISION_MAINTIEN


def test_gain_calcule_comme_le_critere_du_12_11(tmp_path):
    path = make_bench(str(tmp_path / "b.db"), jours=10)          # 20 réunions : le seuil
    rows = ra.collecter(path)["rows"]
    conn = sqlite3.connect(path)
    attendu = []
    for race_id, probs, meta in conn.execute("SELECT race_id, probabilities_json, metadata_json FROM predictions "
                                             "WHERE horizon = 'T_MATIN' ORDER BY lock_time, race_id"):
        ranking = json.loads(conn.execute("SELECT ranking_json FROM race_results WHERE race_id = ?",
                                          (race_id,)).fetchone()[0])
        w = str([x["num"] for x in ranking if x["rang"] == 1][0])
        tem = json.loads(meta)[ra.META_TEMOIN]["probabilities"]
        attendu.append(np.log(max(json.loads(probs)[w], 1e-6)) - np.log(max(tem[w], 1e-6)))
    assert [r["gain"] for r in rows] == pytest.approx(attendu, abs=1e-12)
    lu = ra.lecture_unique(rows, len(rows))
    lo, hi = ombre.intervalle(attendu, [r["reunion"] for r in rows], 0.95)
    assert lu["ic95"] == [round(lo, 5), round(hi, 5)] and lu["gain_ll"] == round(float(np.mean(attendu)), 5)


def test_moins_de_20_reunions_non_mesurable(tmp_path):
    """Décision de Steph du 25/11 : sous 20 réunions, l'IC95 par réunion est
    trop étroit (un retour à tort dans 25 % des cas à 2 réunions) ; la règle
    ne déclenche pas le retour."""
    assert ra.REUNIONS_MIN == 20
    lu = _lire(make_bench(str(tmp_path / "b.db"), jours=1, per_day=8, une_reunion=True, gain="negatif"))[0]["lecture"]
    assert lu["reunions"] == 1 and lu["decision"] == ra.DECISION_NON_MESURABLE
    assert "ic95" not in lu
    # Une réunion par jour : 19 jours restent non mesurables, 20 jours sont lus.
    lu = _lire(make_bench(str(tmp_path / "c.db"), jours=19, per_day=10, gain="negatif"))[0]["lecture"]
    assert lu["reunions"] == 19 and lu["decision"] == ra.DECISION_NON_MESURABLE
    lu = _lire(make_bench(str(tmp_path / "d.db"), jours=20, per_day=10, gain="negatif"))[0]["lecture"]
    assert lu["reunions"] == 20 and lu["decision"] == ra.DECISION_RETOUR


# ── Éditions lues ─────────────────────────────────────────────────────────
def test_editions_en_repli_comptees_et_exclues_du_gain(tmp_path):
    r = _lire(make_bench(str(tmp_path / "b.db"), repli_every=5))[0]
    assert (r["editions_eligibles"], r["editions_en_repli"], r["editions_recette_a_lisibles"]) == (600, 120, 480)
    assert r["exclusions"] == {}                                   # le repli n'est pas une exclusion
    assert r["lecture"]["editions"] == 480 and r["lecture"]["part_recette_a"] == 0.8
    assert r["lecture"]["decision"] == ra.DECISION_MAINTIEN


def test_exclusions_et_eligibilite_du_12_11(tmp_path):
    cas = {1: "sans_arrivee", 2: "dead_heat", 3: "temoin_incomplet", 4: "cle_differente", 5: "marqueur_absent",
           6: "marche_partiel", 7: "gagnant_absent"}
    r = _lire(make_bench(str(tmp_path / "b.db"), variantes=cas))[0]
    assert r["exclusions"] == {"sans_arrivee_definitive": 1, "dead_heat_premiere_place": 1, "temoin_incomplet": 1,
                               "cle_differente": 1, "marqueur_absent_ou_inconnu": 1,
                               "gagnant_hors_partants_au_verrou": 1}
    # Marché partiel : hors de l'éligibilité du 12/11, ni compté ni exclu.
    assert r["editions_eligibles"] == 600 - 1 - 2 and r["editions_recette_a_lisibles"] == 600 - 7


def test_hors_fenetre_ignore(tmp_path):
    r = _lire(make_bench(str(tmp_path / "b.db"), hors_fenetre=True))[0]
    assert r["editions_eligibles"] == 600 and r["jours_avec_edition"] == 30
    assert r["lecture"]["decision"] == ra.DECISION_MAINTIEN


def test_jours_manques_comptent_dans_la_fenetre(tmp_path):
    """Calcul de nuit manqué les derniers jours : la fenêtre ne s'allonge pas."""
    r = _lire(make_bench(str(tmp_path / "b.db"), jours=20))[0]
    assert r["jours_avec_edition"] == 20 and r["lecture"]["dernier_jour"] == "2026-12-19"


# ── Sortie publique ───────────────────────────────────────────────────────
def test_sortie_en_agregats_et_empreinte_stable(tmp_path):
    path = make_bench(str(tmp_path / "b.db"))
    (r1, texte), (r2, _) = _lire(path), _lire(path)
    assert r1["lecture"]["empreinte"] == r2["lecture"]["empreinte"]
    rows = ra.collecter(path)["rows"]
    attendu = hashlib.sha256(json.dumps([[x["race_id"], round(x["gain"], 6)] for x in rows]).encode()).hexdigest()
    assert r1["lecture"]["empreinte"] == attendu
    assert "_SYN" not in texte                                     # aucun identifiant de course
    for interdit in ("probabilit", "selection", "\"gain\""):
        assert interdit not in texte.lower(), interdit
    # Agrégats seulement : la ligne publiée n'a que ces champs.
    sortie = json.loads(texte.split("REGLE_ARRET_LECTURE ", 1)[1])
    assert set(sortie) == {"fenetre", "lecture_le", "cle", "temoin", "jours_avec_edition", "editions_eligibles",
                           "editions_en_repli", "editions_recette_a_lisibles", "exclusions", "lecture"}
    assert set(sortie["lecture"]) == {"editions", "reunions", "part_recette_a", "premier_jour", "dernier_jour",
                                      "gain_ll", "ic95", "decision", "empreinte"}


# ── Workflow à exécution unique ───────────────────────────────────────────
def test_workflow_lecture_unique_et_empreintes():
    yaml = pytest.importorskip("yaml")
    texte = open(WORKFLOW, encoding="utf-8").read()
    doc = yaml.safe_load(texte)
    declencheurs = doc.get("on", doc.get(True))
    assert set(declencheurs) == {"workflow_dispatch"}
    assert doc["permissions"] == {"contents": "read", "actions": "read"}
    assert doc["concurrency"]["group"] == "regle-arret-lecture"
    etapes = " ".join(str(e.get("run", "")) for e in doc["jobs"]["lecture"]["steps"])
    assert 'refs/heads/main' in etapes and f'< "{ra.LECTURE_LE}"' in etapes
    assert "docs/REGLE_ARRET_LECTURE.md" in etapes and "regle_arret_lecture.yml/runs?status=success" in etapes
    assert "r2_store fetch" in etapes and "r2_store push" not in etapes and "r2_store pull" not in etapes
    assert "python -m turf_lab.regle_arret --banc copie_banc.db" in etapes and "^REGLE_ARRET_LECTURE " in etapes
    empreintes = dict((f, h) for h, f in re.findall(r"^\s+([0-9a-f]{64})  (\S+)$", texte, re.M))
    assert set(empreintes) == {"turf_lab/regle_arret.py", "turf_lab/ombre_lecture.py", "turf_lab/ombre.py",
                               "turf_lab/r2_store.py"}
    for fichier, h in empreintes.items():
        assert hashlib.sha256(open(os.path.join(RACINE, fichier), "rb").read()).hexdigest() == h, fichier


def test_lecteur_du_12_11_inchange():
    """Le lecteur de la règle d'arrêt reprend l'éligibilité du lecteur scellé
    du 12/11, qui ne doit pas bouger (empreinte du gel d8cc8ec4)."""
    h = hashlib.sha256(open(os.path.join(RACINE, "turf_lab", "ombre_lecture.py"), "rb").read()).hexdigest()
    assert h == "5c3b379b903378ac471c5e66562a51eeac23697d64b8ac94fe9f49dfee58fa61"
