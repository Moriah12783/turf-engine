"""Recette A en production sur l'édition du matin à compter du 30/11/2026
(décision écrite de Steph du 13/11/2026 ; contrat : docs/RECETTE_A_PRODUCTION.md).

Référence : tests/fixtures/recette_a_reference.json, figée avec le moteur du
gel (nve-2026-10-07) avant le patch. Ce fichier vérifie :

- avant le 30/11, sorties identiques au gel, champ pour champ ;
- à partir du 30/11, continuité croisée : publié = ombre du gel, témoin =
  publié du gel ; les champs dérivés (sélection, bases, tocard, confiance,
  tickets) viennent du code habituel ;
- hors du matin (T-90, T-30, T-15), rien ne change ;
- chaque cas de repli donne l'édition NVE, avec son motif ;
- toute édition en recette A porte son témoin, de la forme lue par le lecteur
  scellé ; le témoin n'est cité nulle part ailleurs et n'atteint jamais le site ;
- le moteur n'écrit aucune ligne de journal ;
- la référence ne dépend pas de la plateforme (Python 3.11 de la production,
  3.12 et suivants, bibliothèque mathématique) : relecture du labo du 16/11.
"""
import contextlib
import copy
import importlib.util
import io
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import engine as engine_mod
from turf_lab import features as features_mod
from turf_lab import fondamental_verrou as fv
from turf_lab import ombre, ombre_lecture
from turf_lab.daily_sync import DailySyncManager
from turf_lab.database import TurfDatabase
from turf_lab.engine import (MOTIF_ERREUR_MOTEUR, MOTIF_MARCHE_ABSENT, MOTIF_PARTANT_SANS_FONDAMENTAL,
                             MOTIF_STATUT_INCOHERENT,
                             MOTIF_TRAIN_UNTIL_MULTIPLES, MOTIF_VERSIONS_MULTIPLES, NVE_VERSION,
                             NVE_VERSION_RECETTE_A, OMBRE_META, OMBRE_RECETTE, RECETTE_REPLI, TEMOIN_META,
                             NewValueEngine, recette_a_en_service)
from turf_lab.odds_quality import DEFAULT_ODDS

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(RACINE, "tests", "fixtures", "recette_a_reference.json"), encoding="utf-8") as _f:
    REFERENCE = json.load(_f)
CAS = {c["nom"]: c for c in REFERENCE["cas"]}
# Variantes de plateforme : celles qui ont servi à retenir les cas de la référence.
_spec = importlib.util.spec_from_file_location(
    "generer_reference_recette_a", os.path.join(RACINE, "tests", "fixtures", "generer_reference_recette_a.py"))
GENERATEUR = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(GENERATEUR)
AVEC_OMBRE = [n for n, c in CAS.items() if OMBRE_META in c["sortie_du_gel"]["metadata"]]
with open(os.path.join(RACINE, "tests", "fixtures", "recette_a_cas_fragile.json"), encoding="utf-8") as _f:
    FRAGILE = json.load(_f)                                  # entrées seules : cas volontairement hors de la référence
VEILLE, DEBUT = "2026-11-29", "2026-11-30"
MARQUEURS = ("recette", "motif_repli", "nve_version", engine_mod.META_MODEL_VERSION, TEMOIN_META)
REPLIS_MOTEUR = {
    "partant_sans_fondamental": (MOTIF_PARTANT_SANS_FONDAMENTAL, "fond-matin-reference"),
    "p_nul": (MOTIF_PARTANT_SANS_FONDAMENTAL, "fond-matin-reference"),
    "marche_absent": (MOTIF_MARCHE_ABSENT, "fond-matin-reference"),
    "versions_melees": (MOTIF_VERSIONS_MULTIPLES, None),
    "train_until_meles": (MOTIF_TRAIN_UNTIL_MULTIPLES, "fond-matin-reference"),
}
STATUTS_SANS_PORTAGE = ("TABLE_ABSENTE", "AUCUNE_LIGNE", "VERSIONS_MULTIPLES", "TRAIN_UNTIL_MULTIPLES", "ERREUR")


def _predire(nom, jour, statut=None, moteur=None):
    """Sortie du moteur pour un cas de la référence, relue comme elle est archivée (JSON)."""
    c = CAS[nom]
    partants = copy.deepcopy(c["partants"])
    if statut is not None:                                  # ce que pose la ligne du verrou T_MATIN
        for r in partants:
            r[fv.CLE_STATUT] = statut
    return json.loads(json.dumps((moteur or NewValueEngine()).predict(dict(c["course"], date=jour), partants)))


def _sans_marqueurs(pred):
    p = copy.deepcopy(pred)
    for cle in MARQUEURS + (OMBRE_META,):
        p["metadata"].pop(cle, None)
    return p


# ── Constantes et dates ──────────────────────────────────────────────────
def test_constantes_de_la_decision():
    assert engine_mod.RECETTE_A_DEBUT == "2026-11-30" and engine_mod.RECETTE_A_FIN is None
    assert date.fromisoformat(engine_mod.RECETTE_A_DEBUT).weekday() == 0        # lundi 30/11/2026
    assert NVE_VERSION == REFERENCE["moteur_du_gel"]["nve_version"] == "nve-2026-10-07"
    assert NVE_VERSION_RECETTE_A == "nve-2026-11-30"
    assert OMBRE_RECETTE == ombre.RECETTE == "A_lineaire_0.90_0.10" and RECETTE_REPLI == "REPLI_NVE"
    assert NewValueEngine.MARKET_WEIGHT == ombre.POIDS_MARCHE == 0.90
    assert REFERENCE["moteur_du_gel"]["engine_sha256"].startswith("bf7193459ab3c82d")
    assert REFERENCE["moteur_du_gel"]["plateformes_verifiees"] == [v[0] for v in GENERATEUR.variantes()]
    assert len(GENERATEUR.variantes()) == 15 and len(CAS) == 16


def test_cles_de_la_ligne_du_verrou_importees_jamais_recopiees():
    assert (engine_mod.CLE_P, engine_mod.CLE_MODEL_VERSION, engine_mod.CLE_TRAIN_UNTIL) == fv.CLES
    assert engine_mod.CLE_STATUT is fv.CLE_STATUT and engine_mod.STATUT_PORTE is fv.STATUT_PORTE
    with open(os.path.join(RACINE, "turf_lab", "engine.py"), encoding="utf-8") as f:
        source = f.read()
    for litteral in (*fv.CLES, fv.CLE_STATUT, f'"{fv.STATUT_PORTE}"'):
        assert litteral not in source, litteral
    assert "except ImportError" not in source                # plus de repli par recopie des noms


def test_dates_en_service(monkeypatch):
    assert not recette_a_en_service(VEILLE) and recette_a_en_service(DEBUT) and recette_a_en_service("2027-06-01")
    assert recette_a_en_service(date(2026, 11, 30)) and not recette_a_en_service(date(2026, 11, 29))
    for illisible in (None, "", "30/11/2026", "demain", 20261130.5, {}):
        assert recette_a_en_service(illisible) is False      # refus par défaut : moteur actuel
    # Retour arrière de la règle d'arrêt : une ligne. Fin exclue = premier jour rendu au moteur actuel.
    monkeypatch.setattr(engine_mod, "RECETTE_A_FIN", "2026-12-31")
    assert recette_a_en_service("2026-12-30") and not recette_a_en_service("2026-12-31")
    assert not recette_a_en_service("2027-01-15") and not recette_a_en_service(VEILLE)
    monkeypatch.setattr(engine_mod, "RECETTE_A_FIN", "illisible")
    assert recette_a_en_service(DEBUT) is False


# ── Avant le 30/11 : rien ne change ──────────────────────────────────────
@pytest.mark.parametrize("nom", sorted(CAS))
def test_avant_le_debut_sorties_identiques_au_gel(nom):
    gel = CAS[nom]["sortie_du_gel"]
    assert _predire(nom, VEILLE) == gel
    # La quatrième clé de la ligne du verrou est ignorée avant le début.
    for statut in (fv.STATUT_PORTE, "TABLE_ABSENTE"):
        assert _predire(nom, VEILLE, statut=statut) == gel


@pytest.mark.parametrize("nom", sorted(CAS))
def test_date_absente_ou_illisible_moteur_actuel(nom):
    c = CAS[nom]
    course = {k: v for k, v in c["course"].items() if k != "date"}
    sortie = json.loads(json.dumps(NewValueEngine().predict(course, copy.deepcopy(c["partants"]))))
    assert sortie == c["sortie_du_gel"]


# ── La référence ne dépend pas de la plateforme ──────────────────────────
@pytest.mark.parametrize("variante", GENERATEUR.variantes(), ids=[v[0] for v in GENERATEUR.variantes()])
def test_reference_independante_de_la_plateforme(variante):
    """La somme des flottants a changé en Python 3.12 (la production tourne en
    3.11) et l'exponentielle varie d'une bibliothèque à l'autre : un arrondi
    à 4 décimales sur la limite peut basculer. Sous chaque variante, le moteur
    doit rendre la référence EXACTE : identité avant le début, continuité
    croisée et replis à partir du début."""
    _, somme, decalages = variante
    with GENERATEUR.plateforme((engine_mod, features_mod), somme, decalages):
        avant = {nom: _predire(nom, VEILLE) for nom in sorted(CAS)}
        apres = {nom: _predire(nom, DEBUT) for nom in sorted(CAS)}
    assert "sum" not in vars(engine_mod) and "sum" not in vars(features_mod)      # tout est remis en place
    assert engine_mod.math.exp(0.0) == 1.0
    for nom, c in CAS.items():
        gel = c["sortie_du_gel"]
        assert avant[nom] == gel, nom
        archive = gel["metadata"].get(OMBRE_META)
        if archive is None:
            assert _sans_marqueurs(apres[nom]) == gel, nom
        else:
            assert apres[nom]["probabilities"] == archive["probabilities"], nom
            assert apres[nom]["selection"] == archive["selection"], nom
            assert apres[nom]["metadata"][TEMOIN_META] == {
                "nve_version": NVE_VERSION, "probabilities": gel["probabilities"], "selection": gel["selection"]}, nom


def test_la_garde_de_plateforme_detecte_un_arrondi_sur_la_limite():
    """Le cas écarté à la relecture du labo : la probabilité du modèle pur du
    n° 10 valait 0,0429 en Python 3.12 et 0,0430 en Python 3.11. Les variantes
    de plateforme le font apparaître sur n'importe quelle machine."""
    c = FRAGILE
    vus = set()
    for _, somme, decalages in GENERATEUR.variantes():
        with GENERATEUR.plateforme((engine_mod, features_mod), somme, decalages):
            sortie = NewValueEngine().predict(dict(c["course"], date=VEILLE), copy.deepcopy(c["partants"]))
        vus.add(sortie["metadata"]["model_probs"]["10"])
    assert vus == {0.0429, 0.043}


# ── À partir du 30/11 : continuité avec ce qui a été mesuré ──────────────
@pytest.mark.parametrize("nom", AVEC_OMBRE)
@pytest.mark.parametrize("statut", [None, "PORTE"])
def test_continuite_croisee_avec_le_gel(nom, statut):
    """Publié (nouveau) = ombre (gel) ; témoin (nouveau) = publié (gel)."""
    gel = CAS[nom]["sortie_du_gel"]
    ombre_gel = gel["metadata"][OMBRE_META]
    sortie = _predire(nom, DEBUT, statut=statut)
    meta = sortie["metadata"]
    assert sortie["probabilities"] == ombre_gel["probabilities"]
    assert sortie["selection"] == ombre_gel["selection"]
    temoin = meta[TEMOIN_META]
    assert temoin == {"nve_version": NVE_VERSION, "probabilities": gel["probabilities"],
                      "selection": gel["selection"]}
    assert set(temoin["probabilities"]) == set(sortie["probabilities"])     # mêmes clés que le publié
    assert meta["recette"] == OMBRE_RECETTE == ombre_gel["recette"] and "motif_repli" not in meta
    assert meta["nve_version"] == NVE_VERSION_RECETTE_A
    assert meta[engine_mod.META_MODEL_VERSION] == ombre_gel["model_version"]
    assert OMBRE_META not in meta                                           # plus d'archive de l'ombre en production
    # Ce qui ne dépend pas de la recette est celui du gel, éligibilité du lecteur comprise.
    for cle in ("market_calibration", "model_probs", "smart_signals", "top_score", "discipline", "runners_count",
                "human_coverage_pct"):
        assert meta[cle] == gel["metadata"][cle], cle
    assert meta["market_calibration"]["applied"] is True and meta["market_calibration"]["market_weight"] == 0.90
    assert ombre_lecture.is_eligible({"meta": meta})


def test_la_recette_change_bien_des_editions():
    """La référence n'est pas triviale : l'ombre du gel s'écarte du publié."""
    ecarts = [n for n in AVEC_OMBRE
              if CAS[n]["sortie_du_gel"]["metadata"][OMBRE_META]["selection"] != CAS[n]["sortie_du_gel"]["selection"]]
    probas = [n for n in AVEC_OMBRE if CAS[n]["sortie_du_gel"]["metadata"][OMBRE_META]["probabilities"]
              != CAS[n]["sortie_du_gel"]["probabilities"]]
    assert len(AVEC_OMBRE) == 10 and len(probas) == 10 and len(ecarts) >= 3


class _RecetteIdentite(NewValueEngine):
    """Moteur d'essai dont la « recette » rend les probabilités NVE elles-mêmes."""

    def _recette_a(self, scored_runners, valid_runners, market_available):
        return {"recette": OMBRE_RECETTE, "model_version": "identite",
                "probabilities": {str(r["num"]): r["estimated_prob"] for r in scored_runners}}, None


@pytest.mark.parametrize("nom", AVEC_OMBRE)
def test_champs_derives_par_le_code_habituel(nom):
    """Si la recette rend les probabilités NVE, les champs publiés sont ceux
    du gel : sélection, bases, tocard, confiance et tickets sortent du même
    code, appliqué aux probabilités de la recette. Seule convention propre à
    la recette, héritée de l'ombre mesurée : l'indice de value est la
    probabilité PUBLIÉE (4 décimales) × la cote, là où le calcul NVE multiplie
    avant d'arrondir. L'écart ne dépasse pas un centième."""
    gel = _sans_marqueurs(CAS[nom]["sortie_du_gel"])
    sortie = _predire(nom, DEBUT, moteur=_RecetteIdentite())
    assert sortie["metadata"]["recette"] == OMBRE_RECETTE
    sortie = _sans_marqueurs(sortie)
    values, values_gel = sortie["metadata"].pop("value_indices"), gel["metadata"].pop("value_indices")
    assert sortie == gel
    assert set(values) == set(values_gel)
    assert all(abs(values[n] - values_gel[n]) <= 0.01 + 1e-9 for n in values)


@pytest.mark.parametrize("nom", AVEC_OMBRE)
def test_champs_derives_coherents_avec_la_recette(nom):
    c = CAS[nom]
    sortie = _predire(nom, DEBUT)
    probs = {int(k): v for k, v in sortie["probabilities"].items()}
    cotes = {r["num"]: r["odds_t15"] for r in c["partants"] if not r["is_non_partant"]}
    assert set(probs) == set(cotes)
    values = {int(k): v for k, v in sortie["metadata"]["value_indices"].items()}
    assert values == {n: round(probs[n] * cotes[n], 2) for n in probs}
    ordre = sorted(probs, key=lambda n: (probs[n], values[n]), reverse=True)
    assert set(sortie["selection"]) == set(ordre[:10]) and len(sortie["selection"]) == min(10, len(probs))
    assert [probs[n] for n in sortie["selection"]] == sorted((probs[n] for n in sortie["selection"]), reverse=True)
    assert set(sortie["bases"]) <= set(sortie["selection"][:5]) and len(sortie["bases"]) == 2
    tocards = [n for n in probs if cotes[n] >= 10.0]
    if tocards:
        assert values[sortie["outsider_num"]] == max(values[n] for n in tocards)
    assert abs(sum(probs.values()) - 1.0) < 2e-3


# ── Hors du matin : T-90, T-30 et T-15 ───────────────────────────────────
def test_hors_du_matin_rien_ne_change():
    """Sans aucune clé de la ligne du verrou, la sortie reste celle du gel, sans aucun marqueur."""
    sortie = _predire("sans_aucune_cle", DEBUT)
    assert sortie == CAS["sans_aucune_cle"]["sortie_du_gel"]
    assert not any(m in sortie["metadata"] for m in MARQUEURS)


# ── Replis ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("nom", sorted(REPLIS_MOTEUR))
@pytest.mark.parametrize("statut", [None, "PORTE"])
def test_repli_decide_par_le_moteur(nom, statut):
    motif, version = REPLIS_MOTEUR[nom]
    gel = CAS[nom]["sortie_du_gel"]
    sortie = _predire(nom, DEBUT, statut=statut)
    assert _sans_marqueurs(sortie) == gel                                   # édition NVE, champ pour champ
    meta = sortie["metadata"]
    assert meta["recette"] == RECETTE_REPLI and meta["motif_repli"] == motif
    assert meta["nve_version"] == NVE_VERSION and meta[engine_mod.META_MODEL_VERSION] == version
    assert TEMOIN_META not in meta and OMBRE_META not in meta


@pytest.mark.parametrize("statut", STATUTS_SANS_PORTAGE)
def test_repli_decide_par_la_ligne_du_verrou(statut):
    """Rien n'a pu être porté : le statut de la ligne devient le motif du repli."""
    sortie = _predire("sans_aucune_cle", DEBUT, statut=statut)
    assert _sans_marqueurs(sortie) == CAS["sans_aucune_cle"]["sortie_du_gel"]
    meta = sortie["metadata"]
    assert (meta["recette"], meta["motif_repli"], meta["nve_version"]) == (RECETTE_REPLI, statut, NVE_VERSION)
    assert meta[engine_mod.META_MODEL_VERSION] is None and TEMOIN_META not in meta


def test_statut_non_porte_meme_si_des_probabilites_trainent():
    """Un statut autre que PORTE l'emporte : jamais de recette sur des clés incohérentes."""
    sortie = _predire("trot_attele_10", DEBUT, statut="TRAIN_UNTIL_MULTIPLES")
    assert sortie["metadata"]["recette"] == RECETTE_REPLI
    assert sortie["metadata"]["motif_repli"] == "TRAIN_UNTIL_MULTIPLES"
    assert sortie["probabilities"] == CAS["trot_attele_10"]["sortie_du_gel"]["probabilities"]


def test_statuts_incoherents_entre_partants():
    c = CAS["trot_attele_10"]
    partants = copy.deepcopy(c["partants"])
    for i, r in enumerate(partants):
        r[fv.CLE_STATUT] = "PORTE" if i % 2 else "AUCUNE_LIGNE"
    sortie = NewValueEngine().predict(dict(c["course"], date=DEBUT), partants)
    assert sortie["metadata"]["recette"] == RECETTE_REPLI
    assert sortie["metadata"]["motif_repli"] == MOTIF_STATUT_INCOHERENT
    assert sortie["probabilities"] == c["sortie_du_gel"]["probabilities"]


class _RecetteEnPanne(NewValueEngine):
    def _recette_a(self, scored_runners, valid_runners, market_available):
        raise RuntimeError("panne simulée")


@pytest.mark.parametrize("nom", AVEC_OMBRE)
def test_erreur_du_moteur_jamais_remontee_au_verrou(nom):
    """Une erreur dans le calcul de la recette ne bloque pas l'édition du
    matin : elle est posée avec le calcul NVE, intact, et le repli est compté."""
    sortie = _predire(nom, DEBUT, statut="PORTE", moteur=_RecetteEnPanne())
    gel = CAS[nom]["sortie_du_gel"]
    assert _sans_marqueurs(sortie) == _sans_marqueurs(gel)
    meta = sortie["metadata"]
    assert (meta["recette"], meta["motif_repli"], meta["nve_version"]) == (RECETTE_REPLI, MOTIF_ERREUR_MOTEUR,
                                                                         NVE_VERSION)
    assert meta[engine_mod.META_MODEL_VERSION] is None and TEMOIN_META not in meta


def test_aucun_partant_valide():
    c = CAS["trot_attele_10"]
    partants = copy.deepcopy(c["partants"])
    for r in partants:
        r["is_non_partant"] = True
        r[fv.CLE_STATUT] = "PORTE"
    sortie = NewValueEngine().predict(dict(c["course"], date=DEBUT), partants)
    assert sortie["selection"] == [] and sortie["metadata"] == {}


# ── Témoin : présence, forme, confidentialité ────────────────────────────
@pytest.mark.parametrize("nom", sorted(CAS))
@pytest.mark.parametrize("statut", [None, "PORTE", "AUCUNE_LIGNE"])
def test_marqueurs_toujours_coherents(nom, statut):
    """Recette A <=> témoin ; repli <=> motif, sans témoin ; hors du matin : aucun marqueur."""
    meta = _predire(nom, DEBUT, statut=statut)["metadata"]
    recette = meta.get("recette")
    assert (recette == OMBRE_RECETTE) == (TEMOIN_META in meta)
    assert (recette == RECETTE_REPLI) == ("motif_repli" in meta)
    if recette is None:
        assert not any(m in meta for m in MARQUEURS)
    else:
        assert meta["nve_version"] == (NVE_VERSION_RECETTE_A if recette == OMBRE_RECETTE else NVE_VERSION)
        assert engine_mod.META_MODEL_VERSION in meta
    assert OMBRE_META not in meta


@pytest.mark.parametrize("nom", AVEC_OMBRE)
def test_temoin_de_la_forme_lue_par_le_lecteur_scelle(nom):
    """Le témoin a la forme de l'archive que le lecteur du 12/11 acceptait
    (mêmes clés que le publié, sélection de 10 ou de tout le champ)."""
    sortie = _predire(nom, DEBUT)
    temoin = sortie["metadata"][TEMOIN_META]
    edition = {"probs": {int(k): float(v) for k, v in sortie["probabilities"].items()},
               "meta": {ombre_lecture.META_OMBRE: temoin}, "selection": sortie["selection"]}
    lu = ombre_lecture.shadow_of(edition)
    assert lu is not None and len(lu["selection"]) == min(10, len(lu["probs"]))


def _fichiers(base, extensions, ignores=(".git", "tests", "__pycache__", "node_modules", "venv", ".venv")):
    for dossier, sous, noms in os.walk(base):
        sous[:] = [d for d in sous if d not in ignores]
        for nom in noms:
            if nom.endswith(extensions):
                yield os.path.join(dossier, nom)


def _cite_le_temoin(chemin):
    with open(chemin, encoding="utf-8", errors="ignore") as f:
        texte = f.read()
    return TEMOIN_META in texte or "TEMOIN_META" in texte


def test_seul_le_moteur_cite_le_temoin():
    """Jusqu'à la lecture de la règle d'arrêt, le témoin n'est cité que par le
    moteur, qui l'écrit, et par le lecteur scellé du labo, qui ne rend aucun
    gain avant le 30/12 (docs/REGLE_ARRET.md)."""
    autorises = {os.path.join("turf_lab", "engine.py"), os.path.join("turf_lab", "regle_arret.py")}
    fautifs = sorted(os.path.relpath(p, RACINE) for p in _fichiers(RACINE, (".py",)) if _cite_le_temoin(p))
    assert [p for p in fautifs if p not in autorises] == []


def test_le_site_public_ne_contient_jamais_le_temoin():
    site = os.path.join(RACINE, "site")
    publics = (".json", ".html", ".js", ".css", ".txt", ".csv", ".xml")
    assert [os.path.relpath(p, RACINE) for p in _fichiers(site, publics) if _cite_le_temoin(p)] == []


def test_le_moteur_n_ecrit_aucune_ligne_de_journal():
    tampon = io.StringIO()
    with contextlib.redirect_stdout(tampon), contextlib.redirect_stderr(tampon):
        for nom in sorted(CAS):
            for statut in (None, "PORTE", "TABLE_ABSENTE"):
                _predire(nom, DEBUT, statut=statut)
                _predire(nom, VEILLE, statut=statut)
    assert tampon.getvalue() == ""


# ── Chaîne complète : table du labo -> ligne du verrou -> moteur -> base ──
VERSION, TRAIN_UNTIL = "fond-matin-chaine", "2026-11-29"


def _course(jour):
    j = date.fromisoformat(jour)
    return {"race_id": f"R1C1_{j:%d%m%Y}_TESTVILLE", "date": jour, "meeting_number": 1, "race_number": 1,
            "name": "Prix Test", "hippodrome": "TESTVILLE", "discipline": "TROT_ATTELE", "distance": 2700,
            "track_type": "SABLE", "track_condition": "BON", "rope": "GAUCHE", "autostart": False,
            "scheduled_start_time": "14:00 GMT (15:00 Paris)", "status": "SCHEDULED"}


def _partants(n=10, cotes_reelles=10):
    out = []
    for i in range(1, n + 1):
        cote = 3.0 + 1.7 * i if i <= cotes_reelles else DEFAULT_ODDS
        out.append({"num": i, "horse_name": f"Cheval{i}", "sex": "M", "age": 5, "driver_jockey": f"J.D{i}",
                    "trainer": f"T.E{i}", "weight": 60.0, "draw": i, "shoeing": "FERRE", "blinkers": "SANS",
                    "morning_odds": cote, "odds_t15": cote, "final_odds": cote, "is_non_partant": False,
                    "press_citation_count": 0, "music": f"{1 + i % 5}a{2 + i % 4}a3a", "earnings": 50000.0 + i,
                    "record_chrono": 0.0, "official_rating": 0.0})
    return out


def _lignes(race_id, n=10, sauf=(), versions=(VERSION,), trains=None):
    """Fondamental qui préfère les numéros élevés, à l'inverse du marché."""
    total = sum(range(1, n + 1))
    out = []
    for version in versions:
        for k in range(1, n + 1):
            if k not in sauf:
                train = (trains or {}).get(k, TRAIN_UNTIL)
                out.append((race_id, k, k / total, version, "2026-11-30T05:51:00Z", train))
    return out


def _passe(jour=DEBUT, table=True, lignes="defaut", cotes_reelles=10, table_illisible=False):
    """Une passe qui pose T_MATIN puis T90 sur la même liste ; renvoie les verrous NVE lus en base."""
    course = _course(jour)
    race_id = course["race_id"]
    db = TurfDatabase(os.path.join(tempfile.mkdtemp(), "recette_a.db"))
    conn = db.get_connection()
    if table_illisible:
        conn.execute("CREATE TABLE fundamental_probs (autre_chose TEXT)")
    elif table:
        from turf_lab.fondamental_nuit import TABLE_SQL                 # contrat de la table du labo
        conn.execute(TABLE_SQL)
        conn.executemany("INSERT INTO fundamental_probs VALUES (?, ?, ?, ?, ?, ?)",
                         _lignes(race_id) if lignes == "defaut" else lignes(race_id))
    conn.commit()
    conn.close()
    mgr = DailySyncManager(db)
    partants = _partants(cotes_reelles=cotes_reelles)
    db.save_race(course)
    db.save_runners(race_id, partants)
    maintenant = datetime.combine(date.fromisoformat(jour), datetime.min.time()).replace(hour=12)
    journal = io.StringIO()
    with contextlib.redirect_stdout(journal):
        assert mgr._lock_horizon(course, partants, "T_MATIN", now_utc=maintenant) == 1
        assert mgr._lock_horizon(course, partants, "T90", now_utc=maintenant) == 1
    assert not any(k in r for r in partants for k in (*fv.CLES, fv.CLE_STATUT))      # partants d'origine intacts
    verrous = {(p["engine_name"], p["horizon"]): p for p in db.get_predictions(race_id)}
    return verrous[("NEW_VALUE_ENGINE", "T_MATIN")], verrous[("NEW_VALUE_ENGINE", "T90")], journal.getvalue(), verrous


def test_chaine_matin_en_recette_a_et_t90_inchange():
    matin, t90, journal, verrous = _passe()
    meta = matin["metadata"]
    assert meta["recette"] == OMBRE_RECETTE and meta["nve_version"] == NVE_VERSION_RECETTE_A
    assert meta[engine_mod.META_MODEL_VERSION] == VERSION and OMBRE_META not in meta
    # T90 de la même passe, mêmes cotes : l'édition NVE, sans aucun marqueur.
    assert not any(m in t90["metadata"] for m in MARQUEURS) and OMBRE_META not in t90["metadata"]
    # Le témoin du matin est exactement ce que NVE publie sur ces partants.
    assert meta[TEMOIN_META] == {"nve_version": NVE_VERSION, "probabilities": t90["probabilities"],
                                 "selection": t90["selection"]}
    # Le fondamental préfère les numéros élevés : la recette s'écarte de NVE.
    assert matin["probabilities"] != t90["probabilities"]
    total = sum(range(1, 11))
    cotes = {i: 3.0 + 1.7 * i for i in range(1, 11)}
    implicite = {i: 1.0 / max(1.05, cotes[i]) for i in cotes}
    for i in cotes:
        attendu = round(0.90 * implicite[i] / sum(implicite.values()) + 0.10 * i / total, 4)
        assert abs(matin["probabilities"][str(i)] - attendu) <= 1e-4
    # Les deux autres moteurs du banc sont verrouillés comme avant, aux deux horizons.
    assert set(verrous) == {(e, h) for e in ("NEW_VALUE_ENGINE", "ETPE_ENGINE", "MARKET_BASELINE")
                            for h in ("T_MATIN", "T90")}
    assert not any(m in verrous[(e, "T_MATIN")]["metadata"] for m in MARQUEURS
                   for e in ("ETPE_ENGINE", "MARKET_BASELINE"))
    # Journal : la ligne du verrou, sans aucune probabilité ; rien du moteur.
    lignes = [l for l in journal.splitlines() if l.startswith("FONDAMENTAL_VERROU ")]
    assert len(lignes) == 1 and '"statut": "PORTE"' in lignes[0]
    assert TEMOIN_META not in journal and fv.CLE_P not in journal
    assert not re.search(r"0\.\d{4,}", "\n".join(lignes))


def test_chaine_avant_le_debut_ombre_archivee_comme_au_gel():
    matin, t90, _, _ = _passe(jour=VEILLE)
    assert not any(m in matin["metadata"] for m in MARQUEURS)
    archive = matin["metadata"][OMBRE_META]
    assert archive["recette"] == OMBRE_RECETTE and archive["nve_version"] == NVE_VERSION
    assert matin["probabilities"] == t90["probabilities"] and matin["selection"] == t90["selection"]
    assert OMBRE_META not in t90["metadata"]


@pytest.mark.parametrize("cas,options,motif,version", [
    ("table absente", {"table": False}, "TABLE_ABSENTE", None),
    ("table vide pour la course", {"lignes": lambda rid: _lignes("R9C9_30112026_AILLEURS")}, "AUCUNE_LIGNE", None),
    ("plusieurs versions", {"lignes": lambda rid: _lignes(rid, versions=(VERSION, "fond-autre"))},
     "VERSIONS_MULTIPLES", None),
    ("plusieurs train_until", {"lignes": lambda rid: _lignes(rid, trains={3: "2026-11-28"})},
     "TRAIN_UNTIL_MULTIPLES", None),
    ("portage partiel", {"lignes": lambda rid: _lignes(rid, sauf=(7,))}, MOTIF_PARTANT_SANS_FONDAMENTAL, VERSION),
    ("marché partiel", {"cotes_reelles": 6}, MOTIF_MARCHE_ABSENT, VERSION),
    ("table illisible", {"table_illisible": True}, "ERREUR", None),
])
def test_chaine_replis(cas, options, motif, version):
    """Un test par cas de repli : l'édition du matin est celle de NVE (la
    même que le T90 de la passe), marquée REPLI_NVE avec son motif."""
    matin, t90, journal, _ = _passe(**options)
    meta = matin["metadata"]
    assert (meta["recette"], meta["motif_repli"], meta["nve_version"]) == (RECETTE_REPLI, motif, NVE_VERSION), cas
    assert meta[engine_mod.META_MODEL_VERSION] == version and TEMOIN_META not in meta and OMBRE_META not in meta
    assert matin["probabilities"] == t90["probabilities"] and matin["selection"] == t90["selection"]
    assert matin["bases"] == t90["bases"] and matin["outsider_num"] == t90["outsider_num"]
    assert not any(m in t90["metadata"] for m in MARQUEURS)
    assert TEMOIN_META not in journal


def test_chaine_retour_arriere_en_une_ligne(monkeypatch):
    """Règle d'arrêt : RECETTE_A_FIN rend les courses à partir de ce jour au moteur du gel."""
    monkeypatch.setattr(engine_mod, "RECETTE_A_FIN", "2026-12-31")
    encore, _, _, _ = _passe(jour="2026-12-30")
    assert encore["metadata"]["recette"] == OMBRE_RECETTE
    rendu, t90, _, _ = _passe(jour="2026-12-31")
    assert not any(m in rendu["metadata"] for m in MARQUEURS)
    assert rendu["probabilities"] == t90["probabilities"] and rendu["selection"] == t90["selection"]
    assert rendu["metadata"][OMBRE_META]["nve_version"] == NVE_VERSION        # comme au gel, ombre comprise
