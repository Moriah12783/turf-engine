"""Référence figée du moteur du gel pour la mise en production de la recette A.

À lancer avec le moteur du gel (``turf_lab/engine.py`` de ``nve-2026-10-07``,
sha256 ``bf7193459ab3c82d…``), AVANT le patch moteur :

    python tests/fixtures/generer_reference_recette_a.py

Le script refuse de tourner avec un autre moteur. Il écrit
``tests/fixtures/recette_a_reference.json`` : pour chaque cas, les entrées
(course, partants, clés du fondamental) et la sortie complète du moteur du
gel, ombre comprise. Les tests de ``tests/test_recette_a_production.py``
comparent le nouveau moteur à cette référence :

- avant le 30/11 : sortie identique, champ pour champ ;
- à partir du 30/11 : publié (nouveau) = ombre (gel), témoin (nouveau) =
  publié (gel).

Entrées synthétiques, graine fixe par cas : aucune donnée réelle, aucune
probabilité de l'ombre mesurée. Le moteur du gel ne lit pas la date de la
course : le script le vérifie (même sortie le 29/11 et le 30/11) et n'archive
qu'une sortie par cas.

Indépendance de la plateforme (relecture du labo du 16/11/2026). La somme des
flottants a changé en Python 3.12 : un arrondi à 4 décimales qui tombe pile
sur la limite peut basculer d'une version de Python à l'autre. La production
tourne en Python 3.11. Un cas n'est donc retenu que si le moteur du gel donne
EXACTEMENT la même sortie sous toutes les variantes de ``variantes()`` : somme
du Python courant, somme naïve (Python 3.11 et avant), somme exacte, et
exponentielle décalée d'une unité du dernier chiffre dans chaque sens
(bibliothèques mathématiques différentes, Windows compris). Sinon le cas est
retiré avec la graine suivante. Le fichier écrit est ainsi le même, octet
pour octet, en Python 3.11, 3.12 et 3.13.
"""
import contextlib
import copy
import hashlib
import json
import math
import os
import random
import sys

RACINE = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, RACINE)

MOTEUR_DU_GEL = "bf7193459ab3c82d"          # sha256 (16 premiers caractères) de turf_lab/engine.py au gel
GRAINE = 20261130
ESSAIS_MAX = 50                              # graines essayées par cas avant de renoncer
SORTIE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recette_a_reference.json")
DATES = ("2026-11-29", "2026-11-30")
VERSION = "fond-matin-reference"
TRAIN_UNTIL = "2026-11-28"

CLE_P, CLE_MODEL_VERSION, CLE_TRAIN_UNTIL = "p_fondamental", "fondamental_model_version", "fondamental_train_until"


# ── Variantes de plateforme ──────────────────────────────────────────────
def somme_naive(valeurs, depart=0):
    """Somme de gauche à droite, sans compensation : ``sum`` jusqu'à Python 3.11."""
    total = depart
    for v in valeurs:
        total += v
    return total


def somme_exacte(valeurs, depart=0):
    """Somme arrondie une seule fois (``math.fsum``) ; les entiers restent des entiers."""
    valeurs = list(valeurs)
    if all(isinstance(v, int) for v in valeurs):
        return somme_naive(valeurs, depart)
    return math.fsum(valeurs) + depart


class MathDecale:
    """Module ``math`` dont ``exp`` est décalée, appel après appel, du nombre
    d'unités du dernier chiffre donné par ``decalages`` (pris en boucle)."""

    def __init__(self, decalages):
        self._decalages = tuple(decalages)
        self._appel = 0

    def __getattr__(self, nom):
        return getattr(math, nom)

    def exp(self, x):
        y = math.exp(x)
        d = self._decalages[self._appel % len(self._decalages)]
        self._appel += 1
        for _ in range(abs(d)):
            y = math.nextafter(y, math.inf if d > 0 else 0.0)
        return y


def variantes():
    """[(nom, somme ou None, décalages de exp ou None)] : 3 sommes × 5 exponentielles."""
    sommes = (("somme du Python courant", None), ("somme naïve (Python 3.11)", somme_naive),
              ("somme exacte", somme_exacte))
    exps = (("exp de la plateforme", None), ("exp +1", (1,)), ("exp -1", (-1,)), ("exp +1/-1", (1, -1)),
            ("exp -1/+1", (-1, 1)))
    return [(f"{a} ; {b}", s, e) for a, s in sommes for b, e in exps]


@contextlib.contextmanager
def plateforme(modules, somme, decalages):
    """Le temps du bloc, les modules du moteur voient ``sum`` et ``math.exp``
    d'une autre plateforme. Tout est remis en place à la sortie."""
    avec_math = [m for m in modules if m.__dict__.get("math") is math]
    try:
        for m in modules:
            if somme is not None:
                m.sum = somme
        if decalages is not None:
            for m in avec_math:
                m.math = MathDecale(decalages)
        yield
    finally:
        for m in modules:
            m.__dict__.pop("sum", None)
        for m in avec_math:
            m.math = math


# ── Cas ──────────────────────────────────────────────────────────────────
def _course(discipline, distance, partants):
    return {"race_id": f"R1C1_30112026_REFERENCE_{discipline}_{partants}", "date": DATES[0], "meeting_number": 1,
            "race_number": 1, "name": "Prix Reference", "hippodrome": "REFERENCE", "discipline": discipline,
            "distance": distance, "track_type": "SABLE" if "TROT" in discipline else "HERBE",
            "track_condition": "BON", "rope": "GAUCHE", "autostart": False,
            "scheduled_start_time": "14:00 GMT (15:00 Paris)", "status": "SCHEDULED"}


def _partants(rng, n, discipline, cotes=None):
    trot = "TROT" in discipline
    out = []
    for i in range(1, n + 1):
        cote = cotes[i - 1] if cotes else round(rng.choice([2.4, 3.1, 4.5, 6.0, 7.5, 9.0, 12.0, 18.0, 26.0, 41.0, 67.0])
                                               + rng.random(), 1)
        musique = "".join(f"{rng.choice([1, 1, 2, 3, 4, 5, 6, 7, 8, 9, 0])}{'a' if trot else 'p'}"
                          for _ in range(rng.randint(3, 6)))
        if trot and rng.random() < 0.25:
            musique = "Da" + musique
        out.append({
            "num": i, "horse_name": f"Reference{i}", "sex": rng.choice(["M", "F", "H"]), "age": rng.randint(3, 9),
            "driver_jockey": f"J.REF{i}", "trainer": f"T.REF{1 + i % 4}", "weight": round(54.0 + rng.random() * 8, 1),
            "draw": rng.randint(1, n), "shoeing": rng.choice(["FERRE", "FERRE", "D4", "DA", "DP"]) if trot else "FERRE",
            "blinkers": "SANS" if trot else rng.choice(["SANS", "SANS", "OEILLERES_AUSTRALIENNES", "OEILLERES_CLASSIQUE"]),
            "morning_odds": cote, "odds_t15": cote, "final_odds": cote, "is_non_partant": False,
            "press_citation_count": 0, "music": musique, "earnings": float(rng.randint(8000, 260000)),
            "record_chrono": round(72.0 + rng.random() * 6, 1) if trot else 0.0,
            "official_rating": 0.0 if trot else round(28.0 + rng.random() * 22, 1),
        })
    return out


def _porter(rng, partants, proche_du_marche=False, sans=(), version=VERSION, train_until=TRAIN_UNTIL):
    """Pose les trois clés de la ligne du verrou, comme fondamental_verrou.porter_fondamental."""
    if proche_du_marche:
        poids = {r["num"]: 1.0 / r["odds_t15"] for r in partants}
    else:
        poids = {r["num"]: 0.2 + rng.random() for r in partants}
    total = somme_exacte(poids.values())
    for r in partants:
        if r["num"] in sans:
            continue
        r[CLE_P] = round(poids[r["num"]] / total, 6)
        r[CLE_MODEL_VERSION] = version
        r[CLE_TRAIN_UNTIL] = train_until
    return partants


def _retire(partants):
    partants[-1]["is_non_partant"] = True


def _deux_sans_cote(partants):
    for r in partants[-2:]:
        r["morning_odds"] = r["odds_t15"] = r["final_odds"] = 15.0


def _sans_marche(partants):
    for r in partants:
        r["morning_odds"] = r["odds_t15"] = r["final_odds"] = 15.0


def _versions(partants):
    partants[2][CLE_MODEL_VERSION] = "fond-matin-autre"


def _train(partants):
    partants[5][CLE_TRAIN_UNTIL] = "2026-11-27"


def _p_nul(partants):
    partants[0][CLE_P] = 0.0


# (nom, description, discipline, distance, partants, options)
CAS = [
    # ── Fondamental complet : le moteur du gel calcule une ombre ──────────
    ("trot_attele_10", "Trot attelé, 10 partants, fondamental complet", "TROT_ATTELE", 2700, 10, {}),
    ("trot_attele_16", "Trot attelé, 16 partants", "TROT_ATTELE", 2850, 16, {}),
    ("trot_monte_14", "Trot monté, 14 partants", "TROT_MONTE", 2175, 14, {}),
    ("plat_12", "Plat, 12 partants", "PLAT", 1600, 12, {}),
    ("obstacle_8", "Obstacle, 8 partants", "OBSTACLE", 3800, 8, {}),
    ("petit_champ_6", "Petit champ, 6 partants : sélection de la taille du champ", "PLAT", 2400, 6, {}),
    ("proche_du_marche", "Fondamental proportionnel au marché", "TROT_ATTELE", 2100, 12, {"proche_du_marche": True}),
    ("cotes_egales", "Cotes égales deux à deux : départage par l'indice de value puis l'ordre des partants",
     "PLAT", 2000, 10, {"cotes": [3.0, 3.0, 5.0, 5.0, 8.0, 8.0, 13.0, 13.0, 21.0, 21.0]}),
    ("non_partant_tardif", "Dernier numéro retiré au verrou : fondamental renormalisé", "TROT_ATTELE", 2700, 11,
     {"retouche": _retire}),
    ("marche_a_80_pct", "Deux partants à la cote par défaut (marché encore retenu par le moteur)", "PLAT", 1800, 10,
     {"retouche": _deux_sans_cote}),
    # ── Pas d'ombre au gel : repli attendu en production ──────────────────
    ("partant_sans_fondamental", "Le n° 4 ne porte aucune probabilité", "TROT_ATTELE", 2700, 10, {"sans": (4,)}),
    ("marche_absent", "Toutes les cotes à la valeur par défaut (verrou à marché partiel neutralisé)", "TROT_ATTELE",
     2700, 10, {"retouche": _sans_marche}),
    ("versions_melees", "Deux versions du modèle parmi les partants", "PLAT", 1600, 10, {"retouche": _versions}),
    ("train_until_meles", "Deux dates d'apprentissage parmi les partants", "PLAT", 1600, 10, {"retouche": _train}),
    ("p_nul", "Probabilité fondamentale nulle pour le n° 1", "TROT_ATTELE", 2700, 10, {"retouche": _p_nul}),
    # ── Aucune clé : ce que reçoivent T-90, T-30 et T-15 ──────────────────
    ("sans_aucune_cle", "Aucune clé du fondamental (horizons hors du matin)", "TROT_ATTELE", 2700, 10,
     {"sans_cle": True}),
]


def construire(cas, essai):
    """(course, partants) d'un cas, pour un essai de graine : chaque cas a son propre tirage."""
    nom, _, discipline, distance, n, options = cas
    options = dict(options)
    cotes, retouche, sans_cle = options.pop("cotes", None), options.pop("retouche", None), options.pop("sans_cle", False)
    rng = random.Random(f"{GRAINE}:{nom}:{essai}")
    partants = _partants(rng, n, discipline, cotes)
    if not sans_cle:
        _porter(rng, partants, **options)
    if retouche:
        retouche(partants)
    return _course(discipline, distance, n), partants


def sorties_par_plateforme(moteur, modules, course, partants):
    """Sortie du moteur, relue comme elle est archivée (JSON), pour chaque
    variante de plateforme et chaque date."""
    out = []
    for nom, somme, decalages in variantes():
        with plateforme(modules, somme, decalages):
            for jour in DATES:
                out.append(json.loads(json.dumps(moteur.predict(dict(course, date=jour), copy.deepcopy(partants)))))
    return out


def main():
    with open(os.path.join(RACINE, "turf_lab", "engine.py"), "rb") as f:
        empreinte = hashlib.sha256(f.read()).hexdigest()
    if empreinte[:16] != MOTEUR_DU_GEL:
        sys.exit(f"REFUS : turf_lab/engine.py ({empreinte[:16]}) n'est pas le moteur du gel ({MOTEUR_DU_GEL}). "
                 "La référence ne se génère qu'avant le patch moteur.")
    from turf_lab import engine as engine_mod
    from turf_lab import features as features_mod

    moteur = engine_mod.NewValueEngine()
    modules = (engine_mod, features_mod)
    archive, ecartes = [], 0
    for cas in CAS:
        nom, description = cas[0], cas[1]
        for essai in range(ESSAIS_MAX):
            course, partants = construire(cas, essai)
            sorties = sorties_par_plateforme(moteur, modules, course, partants)
            if all(s == sorties[0] for s in sorties[1:]):
                archive.append({"nom": nom, "description": description, "essai": essai, "course": course,
                                "partants": partants, "sortie_du_gel": sorties[0]})
                break
            ecartes += 1
        else:
            sys.exit(f"REFUS : aucun tirage du cas {nom} n'est indépendant de la plateforme en {ESSAIS_MAX} essais.")
    doc = {"moteur_du_gel": {"engine_sha256": empreinte, "nve_version": engine_mod.NVE_VERSION, "graine": GRAINE,
                             "dates_verifiees": list(DATES),
                             "plateformes_verifiees": [nom for nom, _, _ in variantes()]},
           "cas": archive}
    with open(SORTIE, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")
    avec = sum(1 for c in archive if "ombre_fondamental" in c["sortie_du_gel"]["metadata"])
    print(f"REFERENCE_RECETTE_A cas={len(archive)} avec_ombre={avec} sans_ombre={len(archive) - avec} "
          f"tirages_ecartes={ecartes} plateformes={len(variantes())} moteur={empreinte[:16]} "
          f"{engine_mod.NVE_VERSION}")


if __name__ == "__main__":
    main()
