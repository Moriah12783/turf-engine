"""Tests de l'export JSON des résultats (livrable partenaire).

Exécutable en script (python tests/test_results_export.py) ou via pytest.
"""
import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab.database import TurfDatabase
from turf_lab.results_export import SCHEMA_VERSION, build_day_export, export_results_json
from turf_lab.results_reader import ArrivalReading, STATUT_DEFINITIVE, STATUT_PROVISOIRE, ranking_from_groups

DAY = "2026-09-09"


def _db_with_day():
    db = TurfDatabase(os.path.join(tempfile.mkdtemp(), "exp.db"))
    for c in (1, 2, 3):
        rid = f"R1C{c}_09092026_ANGERS"
        db.save_race({"race_id": rid, "date": DAY, "meeting_number": 1, "race_number": c, "name": f"Prix {c}",
                      "hippodrome": "ANGERS", "discipline": "TROT_ATTELE", "distance": 2850, "track_type": "SABLE",
                      "track_condition": "BON", "rope": "GAUCHE", "autostart": False,
                      "scheduled_start_time": "11:55 GMT (13:55 Paris)", "status": "SCHEDULED",
                      "start_time_utc": "2026-09-09T11:55:00Z", "pmu_statut": "PROGRAMMEE", "declared_runners": 5})
        db.save_runners(rid, [{"num": n, "horse_name": f"H{n}", "sex": "M", "age": 5, "driver_jockey": "J", "trainer": "T",
                               "weight": 60.0, "draw": n, "shoeing": "FERRE", "blinkers": "SANS", "morning_odds": 6.0,
                               "odds_t15": 6.0, "final_odds": 6.0, "is_non_partant": n == 5, "press_citation_count": 0,
                               "music": "1a", "earnings": 0.0, "record_chrono": 0.0, "official_rating": 0.0} for n in range(1, 6)])
    # C1 : provisoire puis définitive avec ex æquo + DAI + dividendes
    t0 = datetime(2026, 9, 9, 12, 0)
    r1 = ArrivalReading(statut=STATUT_PROVISOIRE, source="PMU_PROGRAMME", pmu_statut="ARRIVEE_PROVISOIRE")
    r1.ranking = ranking_from_groups([[4], [2]])
    db.record_result("R1C1_09092026_ANGERS", r1, source_url="https://online.turfinfo.api.pmu.fr/rest/client/7/programme/09092026", now_utc=t0)
    r2 = ArrivalReading(statut=STATUT_DEFINITIVE, definitive_flag=True, source="PMU_PROGRAMME", pmu_statut="ARRIVEE_DEFINITIVE_COMPLETE")
    r2.ranking = ranking_from_groups([[4], [2, 1]])
    r2.incidents = [{"num": 3, "type": "DISQUALIFIE_POUR_ALLURE_IRREGULIERE"}]
    db.record_result("R1C1_09092026_ANGERS", r2, source_url="https://online.turfinfo.api.pmu.fr/rest/client/7/programme/09092026",
                     rapports=[{"bet_type": "SIMPLE_GAGNANT", "combination": "4", "dividend": 7.2},
                               {"bet_type": "SIMPLE_PLACE", "combination": "4", "dividend": 2.1}], now_utc=t0 + timedelta(minutes=9))
    # C2 : provisoire seulement ; C3 : en attente
    r3 = ArrivalReading(statut=STATUT_PROVISOIRE, source="PMU_PARTICIPANTS", pmu_statut="FIN_COURSE")
    r3.ranking = ranking_from_groups([[1], [2], [3]])
    db.record_result("R1C2_09092026_ANGERS", r3, now_utc=t0 + timedelta(minutes=40))
    return db


def test_structure_du_fichier_journalier():
    db = _db_with_day()
    payload = build_day_export(db, DAY, generated_at=datetime(2026, 9, 9, 13, 0))
    assert payload["schema_version"] == SCHEMA_VERSION and payload["date_course"] == DAY
    assert payload["nb_courses"] == 3 and payload["genere_le_utc"] == "2026-09-09T13:00:00Z"
    assert payload["compte_par_statut"] == {"EN_ATTENTE": 1, "PROVISOIRE": 1, "DEFINITIVE": 1, "ANNULEE": 0}
    c1, c2, c3 = payload["courses"]

    # Identité
    assert c1["course_id"] == "R1C1_09092026_ANGERS"
    assert c1["identite"] == {"date": DAY, "reunion": 1, "course": 1, "code": "R1C1", "hippodrome": "ANGERS", "libelle": "Prix 1",
                              "discipline": "TROT_ATTELE", "distance_m": 2850, "heure_depart_utc": "2026-09-09T11:55:00Z",
                              "heure_depart_source": "PMU_HEUREDEPART",
                              "heure_depart_affichee": "11:55 GMT (13:55 Paris)", "partants_declares": 5, "partants_actifs": 4}
    # Statut + classement structuré (ex æquo) + non classés + non partants
    assert c1["statut"] == {"code": "DEFINITIVE", "definitive": True, "finalite": "VERIFIEE_PMU", "annulee": False,
                            "pmu_statut": "ARRIVEE_DEFINITIVE_COMPLETE"}
    assert c1["classement"] == [{"rang": 1, "num": 4, "nom": "H4", "dead_heat": False},
                                {"rang": 2, "num": 1, "nom": "H1", "dead_heat": True},
                                {"rang": 2, "num": 2, "nom": "H2", "dead_heat": True}]
    assert c1["arrivee"] == [4, 1, 2]
    assert c1["non_classes"] == [{"num": 3, "nom": "H3", "incident": "DISQUALIFIE_POUR_ALLURE_IRREGULIERE"}]
    assert c1["non_partants"] == [{"num": 5, "nom": "H5"}]
    # Rapports, source, horodatages, correction
    assert c1["rapports"]["disponibles"] and c1["rapports"]["simple_gagnant"] == {"4": 7.2} and c1["rapports"]["simple_place"] == {"4": 2.1}
    assert c1["source"]["canal"] == "PMU_PROGRAMME" and c1["source"]["url_origine"].startswith("https://")
    assert c1["horodatages"] == {"premiere_lecture_utc": "2026-09-09T12:00:00Z", "definitive_depuis_utc": "2026-09-09T12:09:00Z",
                                 "verifiee_le_utc": "2026-09-09T12:09:00Z", "enregistree_ancien_systeme_utc": None,
                                 "derniere_modification_utc": "2026-09-09T12:09:00Z", "derniere_verification_utc": "2026-09-09T12:09:00Z"}
    # Le 2e rang publié (2 seul) est devenu « 1 et 2 ex æquo » : un rang déjà
    # publié a changé => comptée comme correction, et passage en définitive.
    assert c1["correction"]["version"] == 2 and c1["correction"]["nb_corrections"] == 1
    assert [(h["version"], h["statut"], h["raison"], h["classement"]) for h in c1["correction"]["historique"]] == [
        (1, "PROVISOIRE", "INITIAL", [4, 2]), (2, "DEFINITIVE", "CORRECTION_CLASSEMENT", [4, 1, 2])]

    # Provisoire : exposée comme telle, jamais « définitive »
    assert c2["statut"]["code"] == "PROVISOIRE" and c2["statut"]["definitive"] is False and c2["statut"]["finalite"] is None and c2["arrivee"] == [1, 2, 3]
    assert payload["version_code"]["depot"] == "Moriah12783/turf-engine" and "commit" in payload["version_code"]
    assert c2["horodatages"]["definitive_depuis_utc"] is None and c2["rapports"]["disponibles"] is False
    # En attente : structure présente, classement vide, version 0
    assert c3["statut"]["code"] == "EN_ATTENTE" and c3["classement"] == [] and c3["correction"]["version"] == 0
    print("  [OK] test_structure_du_fichier_journalier")


def test_empreinte_et_fichiers():
    db = _db_with_day()
    site = tempfile.mkdtemp()
    res = export_results_json(db, site, days=3, today=datetime(2026, 9, 10, 9, 0))
    assert res["jours_ecrits"] == [DAY], res
    path = os.path.join(site, "resultats", f"{DAY}.json")
    with open(path, encoding="utf-8") as f:
        payload = json.load(f)
    # L'empreinte se recalcule à l'identique côté consommateur
    canon = json.dumps(payload["courses"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert hashlib.sha256(canon).hexdigest() == payload["empreinte_sha256"]
    # Manifeste
    with open(os.path.join(site, "resultats", "index.json"), encoding="utf-8") as f:
        index = json.load(f)
    assert list(index["journees"]) == [DAY] and index["journees"][DAY]["fichier"] == f"resultats/{DAY}.json"
    assert index["journees"][DAY]["empreinte_sha256"] == payload["empreinte_sha256"]
    # Un fichier ancien non régénéré reste indexé
    with open(os.path.join(site, "resultats", "2026-08-01.json"), "w", encoding="utf-8") as f:
        json.dump({"nb_courses": 2, "compte_par_statut": {}, "empreinte_sha256": "abc", "genere_le_utc": "x"}, f)
    export_results_json(db, site, days=3, today=datetime(2026, 9, 10, 9, 0))
    with open(os.path.join(site, "resultats", "index.json"), encoding="utf-8") as f:
        index = json.load(f)
    assert list(index["journees"]) == [DAY, "2026-08-01"]
    assert index["nb_journees"] == 2 and index["inventaire_corrections"]["fichier"] == "resultats/corrections.json"
    # Inventaire des corrections : l'événement CORRECTION_CLASSEMENT de C1, avec avant/après
    with open(os.path.join(site, "resultats", "corrections.json"), encoding="utf-8") as f:
        inv = json.load(f)
    assert inv["nb_corrections"] == 1 == index["inventaire_corrections"]["nb_corrections"]
    ev = inv["corrections"][0]
    assert ev["course_id"] == "R1C1_09092026_ANGERS" and ev["raison"] == "CORRECTION_CLASSEMENT"
    assert (ev["version_avant"], ev["version_apres"]) == (1, 2) and (ev["statut_avant"], ev["statut_apres"]) == ("PROVISOIRE", "DEFINITIVE")
    assert ev["classement_avant"] == [4, 2] and ev["classement_apres"] == [4, 1, 2] and ev["incidents_apres"][0]["num"] == 3
    assert inv["compte_par_raison"] == {"CORRECTION_CLASSEMENT": 1, "INITIAL": 2}
    canon = json.dumps(inv["corrections"], ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    assert hashlib.sha256(canon).hexdigest() == index["inventaire_corrections"]["empreinte_sha256"]
    # Sans limite de jours : toutes les journées présentes en base sont écrites
    site2 = tempfile.mkdtemp()
    assert export_results_json(db, site2, today=datetime(2026, 10, 30, 9, 0))["jours_ecrits"] == [DAY]
    print("  [OK] test_empreinte_et_fichiers")


def main():
    test_structure_du_fichier_journalier()
    test_empreinte_et_fichiers()
    print("\n=== 2 TESTS EXPORT JSON RÉSULTATS PASSENT ===")


if __name__ == "__main__":
    main()
