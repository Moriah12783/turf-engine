"""Tests de la lecture du classement PMU (points 2 et 4 du correctif partenaire).

Exécutable en script (python tests/test_results_reader.py) ou via pytest.
Les objets JSON reproduisent la forme RÉELLE du flux public PMU observée le
09/09/2026 (R1C1 Angers) et le 10/09/2026 (R2 Wolvega).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab.results_reader import (
    CMP_COMPLETION, CMP_CORRECTION, CMP_IDENTIQUE, CMP_REGRESSION,
    SOURCE_PARTICIPANTS, SOURCE_PROGRAMME, STATUT_ANNULEE, STATUT_DEFINITIVE,
    STATUT_EN_ATTENTE, STATUT_PROVISOIRE, compare_rankings, parse_ordre_arrivee,
    ranking_from_groups, read_arrival,
)

# Forme réelle (09/09/2026, R1C1 Angers, Grand National du Trot)
COURSE_DEFINITIVE = {
    "numOrdre": 1, "libelle": "GRAND NATIONAL DU TROT", "heureDepart": 1788954900000,
    "statut": "ARRIVEE_DEFINITIVE_COMPLETE", "categorieStatut": "ARRIVEE",
    "arriveeDefinitive": True, "isArriveeDefinitive": True,
    "ordreArrivee": [[14], [3], [5], [6], [12], [7], [15], [1], [8], [2], [4], [10], [11], [13]],
    "incidents": [{"type": "DISQUALIFIE_POUR_ALLURE_IRREGULIERE", "numeroParticipants": [9]}],
}
PARTICIPANTS = [
    {"numPmu": n, "nom": f"CHEVAL{n}", "statut": "PARTANT"} for n in range(1, 16)
]
for p in PARTICIPANTS:
    order = [14, 3, 5, 6, 12, 7, 15, 1, 8, 2, 4, 10, 11, 13]
    if p["numPmu"] in order:
        p["ordreArrivee"] = order.index(p["numPmu"]) + 1
    if p["numPmu"] == 9:
        p["incident"] = "DISQUALIFIE_POUR_ALLURE_IRREGULIERE"
ACTIVE = list(range(1, 16))


def test_definitive_complete_avec_incident():
    r = read_arrival(COURSE_DEFINITIVE, PARTICIPANTS, ACTIVE, source=SOURCE_PROGRAMME)
    assert r.statut == STATUT_DEFINITIVE and r.definitive_flag and r.complete, r
    assert r.valid, r.errors
    assert r.flat_arrival() == [14, 3, 5, 6, 12, 7, 15, 1, 8, 2, 4, 10, 11, 13]
    assert r.ranking[0] == {"rang": 1, "num": 14, "dead_heat": False}
    assert r.incidents == [{"num": 9, "type": "DISQUALIFIE_POUR_ALLURE_IRREGULIERE"}]
    assert r.disqualified() == [9]
    assert r.source == SOURCE_PROGRAMME
    print("  [OK] test_definitive_complete_avec_incident")


def test_dead_heat_rangs_de_competition():
    course = dict(COURSE_DEFINITIVE, ordreArrivee=[[14], [3, 5], [6]], incidents=[])
    r = read_arrival(course, None, ACTIVE)
    assert [(x["rang"], x["num"], x["dead_heat"]) for x in r.ranking] == [
        (1, 14, False), (2, 3, True), (2, 5, True), (4, 6, False)], r.ranking
    assert r.valid
    # L'ancien lecteur aplatissait : le 5 devenait « 3e ». Ici il reste 2e ex æquo.
    assert r.flat_arrival() == [14, 3, 5, 6]
    print("  [OK] test_dead_heat_rangs_de_competition")


def test_provisoire_sans_drapeau():
    course = dict(COURSE_DEFINITIVE, statut="ARRIVEE_PROVISOIRE", arriveeDefinitive=False,
                  isArriveeDefinitive=False, ordreArrivee=[[14], [3], [5], [6], [12]])
    r = read_arrival(course, PARTICIPANTS, ACTIVE)
    assert r.statut == STATUT_PROVISOIRE and not r.definitive_flag, r
    assert r.flat_arrival() == [14, 3, 5, 6, 12]
    course2 = dict(course, statut="FIN_COURSE")
    assert read_arrival(course2, None, ACTIVE).statut == STATUT_PROVISOIRE
    print("  [OK] test_provisoire_sans_drapeau")


def test_booleen_arriveeDefinitive_jamais_itere():
    # L'ancien code faisait `for item in course.get("arriveeDefinitive", [])`
    # sur un booléen => TypeError. Ici : lecture propre.
    course = {"statut": "ARRIVEE_DEFINITIVE", "arriveeDefinitive": True}
    r = read_arrival(course, None, ACTIVE)
    assert r.statut == STATUT_DEFINITIVE and r.ranking == [] and "ARRIVEE_SANS_CLASSEMENT" in r.errors, r
    assert parse_ordre_arrivee(True) == [] and parse_ordre_arrivee(None) == []
    print("  [OK] test_booleen_arriveeDefinitive_jamais_itere")


def test_repli_participants_est_provisoire():
    course = {"statut": "PROGRAMMEE", "categorieStatut": "A_PARTIR", "arriveeDefinitive": False}
    parts = [dict(p) for p in PARTICIPANTS]
    r = read_arrival(course, parts, ACTIVE)
    assert r.statut == STATUT_PROVISOIRE and r.source == SOURCE_PARTICIPANTS, r
    assert r.flat_arrival()[:3] == [14, 3, 5]
    # Ex æquo reconstruit depuis les entiers identiques
    parts2 = [{"numPmu": 1, "ordreArrivee": 1}, {"numPmu": 2, "ordreArrivee": 2}, {"numPmu": 3, "ordreArrivee": 2}]
    r2 = read_arrival({}, parts2, [1, 2, 3])
    assert [(x["rang"], x["num"]) for x in r2.ranking] == [(1, 1), (2, 2), (2, 3)], r2.ranking
    print("  [OK] test_repli_participants_est_provisoire")


def test_en_attente_et_annulee():
    assert read_arrival({"statut": "PROGRAMMEE", "categorieStatut": "A_PARTIR"}, None, ACTIVE).statut == STATUT_EN_ATTENTE
    assert read_arrival({"statut": "ROUGE_AUX_PARTANTS", "categorieStatut": "A_PARTIR"}, None, ACTIVE).statut == STATUT_EN_ATTENTE
    r = read_arrival({"statut": "COURSE_ANNULEE", "ordreArrivee": [[1]]}, None, ACTIVE)
    assert r.statut == STATUT_ANNULEE and r.ranking == [] and r.valid, r
    print("  [OK] test_en_attente_et_annulee")


def test_validation_refuse_les_incoherences():
    # Numéro inconnu du programme
    r = read_arrival(dict(COURSE_DEFINITIVE, ordreArrivee=[[14], [99]], incidents=[]), None, ACTIVE)
    assert any(e.startswith("NUMERO_INCONNU") for e in r.errors), r.errors
    # Cheval classé ET disqualifié
    r = read_arrival(dict(COURSE_DEFINITIVE, ordreArrivee=[[14], [9]]), None, ACTIVE)
    assert "CLASSE_ET_NON_CLASSE" in r.errors, r.errors
    # Doublon
    assert "NUMERO_EN_DOUBLE" in read_arrival(dict(COURSE_DEFINITIVE, ordreArrivee=[[14], [14]], incidents=[]), None, ACTIVE).errors
    # Non-partant classé
    course = dict(COURSE_DEFINITIVE, ordreArrivee=[[14], [3]], incidents=[{"type": "NON_PARTANT", "numeroParticipants": [3]}])
    r = read_arrival(course, None, ACTIVE)
    assert "CLASSE_ET_NON_PARTANT" in r.errors and r.non_partants == [3], r
    print("  [OK] test_validation_refuse_les_incoherences")


def test_non_partants_et_incidents_wolvega():
    # 10/09/2026 R2C1 Wolvega : NP (5) + DAI (3), forme réelle du flux
    course = {"statut": "FIN_COURSE", "categorieStatut": "ARRIVEE", "arriveeDefinitive": True,
              "ordreArrivee": [[6], [7], [1], [4], [2]],
              "incidents": [{"type": "NON_PARTANT", "numeroParticipants": [5]},
                            {"type": "DISQUALIFIE_POUR_ALLURE_IRREGULIERE", "numeroParticipants": [3]}]}
    r = read_arrival(course, None, [1, 2, 3, 4, 6, 7])
    assert r.statut == STATUT_DEFINITIVE and r.valid, r
    assert r.non_partants == [5] and r.incidents == [{"num": 3, "type": "DISQUALIFIE_POUR_ALLURE_IRREGULIERE"}]
    print("  [OK] test_non_partants_et_incidents_wolvega")


def test_comparaison_des_lectures():
    a = ranking_from_groups([[14], [3], [5]])
    b = ranking_from_groups([[14], [3], [5], [6], [12]])
    c = ranking_from_groups([[3], [14], [5], [6], [12]])
    assert compare_rankings(a, a, [], []) == CMP_IDENTIQUE
    assert compare_rankings(a, b, [], []) == CMP_COMPLETION
    assert compare_rankings(b, a, [], []) == CMP_REGRESSION
    assert compare_rankings(b, c, [], []) == CMP_CORRECTION
    inc9 = [{"num": 9, "type": "DISQUALIFIE_POUR_ALLURE_IRREGULIERE"}]
    assert compare_rankings(b, b, [], inc9) == CMP_COMPLETION       # incident ajouté
    assert compare_rankings(b, b, inc9, []) == CMP_CORRECTION       # incident retiré
    assert compare_rankings(b, b, inc9, [{"num": 9, "type": "ARRETE"}]) == CMP_CORRECTION
    print("  [OK] test_comparaison_des_lectures")


def main():
    test_definitive_complete_avec_incident()
    test_dead_heat_rangs_de_competition()
    test_provisoire_sans_drapeau()
    test_booleen_arriveeDefinitive_jamais_itere()
    test_repli_participants_est_provisoire()
    test_en_attente_et_annulee()
    test_validation_refuse_les_incoherences()
    test_non_partants_et_incidents_wolvega()
    test_comparaison_des_lectures()
    print("\n=== 9 TESTS LECTURE DU CLASSEMENT PMU PASSENT ===")


if __name__ == "__main__":
    main()
