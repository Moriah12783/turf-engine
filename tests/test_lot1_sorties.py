"""Lot 1 — sécuriser les sorties abonnés (audit du 10/09/2026 : A03, A04, A05,
A06, A07-ticket, A08, A12, presse-papiers).

Exécutable en script (python tests/test_lot1_sorties.py) ou via pytest.
Base temporaire, aucune donnée réelle, aucune métrique du banc touchée.
"""
import itertools
import json
import math
import os
import sqlite3
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab.benchmark import TurfBenchmarkLab
from turf_lab.database import TurfDatabase
from turf_lab.engine import NewValueEngine
from turf_lab.html_report import generate_html_dashboard
from turf_lab.odds_quality import all_default_odds, is_valid_odds, priced_ratio
from turf_lab.publication_gate import can_publish

RACE_DATE = "2026-09-10"
RID = "R1C1_10092026_TESTVILLE"
BETS_FULL = [{"code": c, "mise_base_eur": m} for c, m in (("SIMPLE_GAGNANT", 2.0), ("COUPLE_PLACE", 1.5), ("COUPLE_GAGNANT", 1.5),
                                                          ("TRIO", 1.5), ("DEUX_SUR_QUATRE", 3.0), ("QUINTE_PLUS", 2.0))]
BETS_SMALL = [{"code": c, "mise_base_eur": m} for c, m in (("SIMPLE_GAGNANT", 2.0), ("COUPLE_ORDRE", 1.5), ("TRIO_ORDRE", 1.5))]


def _db():
    return TurfDatabase(os.path.join(tempfile.mkdtemp(), "lot1.db"))


def _race(rid=RID, bets=None, status="SCHEDULED", start_utc="2026-09-10T14:00:00Z"):
    return {"race_id": rid, "date": RACE_DATE, "meeting_number": 1, "race_number": int(rid.split("C")[1].split("_")[0]),
            "name": "Prix Test", "hippodrome": "TESTVILLE", "discipline": "TROT_ATTELE", "distance": 2700,
            "track_type": "SABLE", "track_condition": "BON", "rope": "GAUCHE", "autostart": False,
            "scheduled_start_time": "14:00 GMT (16:00 Paris)", "status": status, "start_time_utc": start_utc,
            "declared_runners": 12, "bets": bets}


def _runners(n=12, np_nums=(), captured=None):
    out = []
    for i in range(1, n + 1):
        out.append({"num": i, "horse_name": f"Cheval{i}", "sex": "M", "age": 5, "driver_jockey": "J", "trainer": "T",
                    "weight": 60.0, "draw": i, "shoeing": "FERRE", "blinkers": "SANS",
                    "morning_odds": 3.0 + i, "odds_t15": 3.0 + i, "final_odds": 3.0 + i,
                    "is_non_partant": i in np_nums, "press_citation_count": 0, "music": "1a2a3a", "earnings": 50000.0,
                    "record_chrono": 0.0, "official_rating": 0.0, "odds_is_real": True,
                    "odds_captured_at": (captured or datetime.utcnow()).isoformat()})
    return out


def _prediction(rid=RID, engine="NEW_VALUE_ENGINE", horizon="T_MATIN", **over):
    eng = NewValueEngine()
    race = _race(rid, bets=BETS_FULL)
    p = eng.predict(race, _runners())
    p.update({"race_id": rid, "horizon": horizon, "odds_real": True, "priced_ratio": 1.0,
              "lock_time_utc": datetime.utcnow().isoformat()})
    p.update(over)
    return p


# ── A03 / A04 : contrat persisté, écriture refusée ─────────────────────

def test_contrat_persiste_et_ecrasement_refuse():
    db = _db()
    db.save_race(_race(bets=BETS_FULL)); db.save_runners(RID, _runners())
    p = _prediction()
    res = db.save_prediction(p)
    assert res["action"] == "INSERTED" and res["hash"], res
    got = [q for q in db.get_predictions(RID) if q["horizon"] == "T_MATIN"][0]
    # Les cinq champs font l'aller-retour, sans valeur par défaut
    assert got["confidence_stars"] == p["confidence_stars"] and got["confidence_label"] == p["confidence_label"]
    assert got["is_no_bet"] == p["is_no_bet"] and got["is_master_couple"] == p["is_master_couple"]
    assert got["smart_tickets"] == p["smart_tickets"] and got["contract_recorded"] is True
    assert got["prediction_hash"] == res["hash"] and got["contract_version"] == 2
    # Second enregistrement DIVERGENT => refusé, original intact
    p2 = dict(p); p2["selection"] = list(reversed(p["selection"])); p2["bases"] = p["selection"][-2:]
    res2 = db.save_prediction(p2)
    assert res2["action"] == "REFUSED_EXISTING" and res2["identical"] is False, res2
    again = [q for q in db.get_predictions(RID) if q["horizon"] == "T_MATIN"][0]
    assert again["selection"] == p["selection"] and again["prediction_hash"] == res["hash"]
    # Correction volontaire : version distincte, tracée, jamais dans `predictions`
    corr = db.save_prediction_correction(p2, reason="TEST_CORRECTION")
    assert corr["action"] == "CORRECTION_RECORDED" and corr["previous_hash"] == res["hash"]
    conn = sqlite3.connect(db.db_path)
    rows = conn.execute("SELECT reason, previous_hash, new_hash FROM predictions_corrections WHERE prediction_id = ?", (f"{RID}_NEW_VALUE_ENGINE_T_MATIN",)).fetchall()
    assert len(rows) == 1 and rows[0][0] == "TEST_CORRECTION" and rows[0][2] == corr["hash"]
    assert conn.execute("SELECT COUNT(*) FROM predictions WHERE race_id = ?", (RID,)).fetchone()[0] == 1
    conn.close()
    print("  [OK] test_contrat_persiste_et_ecrasement_refuse")


def test_archive_sans_contrat_restituee_sans_valeur_favorable():
    db = _db()
    db.save_race(_race()); db.save_runners(RID, _runners())
    conn = sqlite3.connect(db.db_path)
    conn.execute("""INSERT INTO predictions (prediction_id, race_id, engine_name, horizon, created_at, lock_time, selection_json, bases_json, outsider_num, probabilities_json, metadata_json)
                    VALUES (?, ?, 'NEW_VALUE_ENGINE', 'T_MATIN', '2026-09-01T06:31:00', '2026-09-01T06:31:00', '[1,2,3,4,5,6,7,8,9,10]', '[1,2]', 8, '{"1":0.3}', '{}')""",
                 (f"{RID}_NEW_VALUE_ENGINE_T_MATIN", RID))
    conn.commit(); conn.close()
    got = db.get_predictions(RID)[0]
    assert got["is_no_bet"] is None and got["is_master_couple"] is None and got["smart_tickets"] is None
    assert got["confidence_stars"] is None and got["confidence_label"] is None and got["contract_recorded"] is False
    # Le rapport n'invente ni étoiles ni tickets
    logs = TurfBenchmarkLab(db).get_historical_race_logs()
    item = [l for l in logs if l["race_id"] == RID][0]
    assert item["contract_recorded"] is False and item["smart_tickets"] is None and item["confidence_stars"] is None
    assert "non enregistrée" in item["decision"]
    print("  [OK] test_archive_sans_contrat_restituee_sans_valeur_favorable")


# ── A05 / A06 / A07 : tickets uniques, valides, éligibles ─────────────

def test_no_bet_supprime_tous_les_tickets():
    eng = NewValueEngine()
    st = eng.generate_smart_tickets([8, 5], [8, 5, 3, 1, 9, 2, 7, 4, 6, 10], 5, False, True, race=_race(bets=BETS_FULL), active_nums=list(range(1, 13)))
    assert st["no_bet"] is True and st["tickets"] == [] and st["ticket_trio"] is None and st["quinte_champ_reduit"] is None
    print("  [OK] test_no_bet_supprime_tous_les_tickets")


def test_trio_sans_doublon():
    eng = NewValueEngine()
    # 8–5 avec outsider 5 : le troisième cheval est le premier associé, jamais 5 deux fois
    st = eng.generate_smart_tickets([8, 5], [8, 5, 3, 1, 9, 2, 7, 4, 6, 10], 5, False, False, race=_race(bets=BETS_FULL), active_nums=list(range(1, 13)))
    trio = [t for t in st["tickets"] if t["produit"] == "TRIO"][0]
    assert trio["chevaux"] == [8, 5, 3] and len(set(trio["chevaux"])) == 3 and trio["texte"] == "8 - 5 - 3", trio
    assert st["ticket_trio"]["formule"] == "8 - 5 - 3"
    # Outsider hors bases : il est le troisième
    st2 = eng.generate_smart_tickets([8, 5], [8, 5, 3, 1, 9], 9, False, False, race=_race(bets=BETS_FULL), active_nums=list(range(1, 13)))
    assert [t for t in st2["tickets"] if t["produit"] == "TRIO"][0]["chevaux"] == [8, 5, 9]
    # Deux chevaux seulement : pas de Trio
    st3 = eng.generate_smart_tickets([8, 5], [8, 5], 5, False, False, race=_race(bets=BETS_FULL), active_nums=[8, 5])
    trio3 = [t for t in st3["tickets"] if t["produit"] == "TRIO"][0]
    assert trio3["chevaux"] == [] and trio3["eligible"] is False and trio3["motif_ineligibilite"] == "MOINS_DE_3_CHEVAUX_DISTINCTS"
    assert st3["ticket_trio"] is None
    print("  [OK] test_trio_sans_doublon")


def test_combinaisons_quinte_par_enumeration():
    eng = NewValueEngine()
    for n_assoc in (3, 4, 5):
        sel = [8, 5] + list(range(1, n_assoc + 1)) + [20, 21]  # 20/21 hors des 4 premiers associés
        sel = [8, 5] + list(range(1, 5)) if n_assoc >= 4 else [8, 5] + list(range(1, n_assoc + 1))
        active = list(set(sel)) + [30]
        st = eng.generate_smart_tickets([8, 5], sel, 1, False, False, race=_race(bets=BETS_FULL, start_utc="2026-09-10T14:00:00Z"),
                                        active_nums=active + list(range(40, 46)))  # >= 8 partants
        q = [t for t in st["tickets"] if t["produit"] == "QUINTE_PLUS"][0]
        k = min(n_assoc, 4)
        expected = len(list(itertools.combinations(range(k), 3)))
        assert q["combinaisons"] == expected == math.comb(k, 3), (n_assoc, q)
        assert q["cout_total_eur"] == round(expected * 2.0, 2), q
    # Deux bases, quatre associés : 4 combinaisons à 2 € = 8 €, pas 6 × 2 = 12 €
    st = eng.generate_smart_tickets([8, 5], [8, 5, 3, 1, 9, 2], 3, False, False, race=_race(bets=BETS_FULL), active_nums=list(range(1, 13)))
    q = [t for t in st["tickets"] if t["produit"] == "QUINTE_PLUS"][0]
    assert q["combinaisons"] == 4 and q["cout_total_eur"] == 8.0 and q["eligible"] is True
    assert st["quinte_champ_reduit"]["combinaisons"] == 4 and st["quinte_champ_reduit"]["budget_conseille_eur"] == 8.0
    print("  [OK] test_combinaisons_quinte_par_enumeration")


def test_eligibilite_des_produits():
    eng = NewValueEngine()
    # Course à 7 partants, sans TRIO ni QUINTE_PLUS ouverts (forme réelle ParisLongchamp R1C2 du 10/09)
    st = eng.generate_smart_tickets([2, 5], [2, 5, 1, 3, 4, 6, 7], 6, False, False, race=_race(bets=BETS_SMALL), active_nums=list(range(1, 8)))
    by = {t["produit"]: t for t in st["tickets"]}
    assert by["COUPLE_PLACE"]["eligible"] is False and by["COUPLE_PLACE"]["motif_ineligibilite"] == "PARI_NON_OUVERT"
    assert by["TRIO"]["eligible"] is False and by["TRIO"]["motif_ineligibilite"] == "PARI_NON_OUVERT"
    assert by["QUINTE_PLUS"]["eligible"] is False and by["QUINTE_PLUS"]["cout_total_eur"] is None
    # Disponibilité inconnue (archive / simulation) : None, jamais « prêt à jouer »
    st2 = eng.generate_smart_tickets([2, 5], [2, 5, 1, 3, 4, 6, 7, 8], 6, False, False, race=_race(bets=None), active_nums=list(range(1, 13)))
    assert all(t["eligible"] is None and t["motif_ineligibilite"] == "DISPONIBILITE_NON_VERIFIEE" for t in st2["tickets"])
    # Mise unitaire lue dans le flux (miseBase en centimes)
    st3 = eng.generate_smart_tickets([2, 5], [2, 5, 1, 3, 4, 6, 7, 8], 6, False, False,
                                     race={"bets": [{"codePari": "TRIO", "miseBase": 150}, {"codePari": "QUINTE_PLUS", "miseBase": 200}, {"codePari": "COUPLE_PLACE", "miseBase": 150}]},
                                     active_nums=list(range(1, 13)))
    assert [t for t in st3["tickets"] if t["produit"] == "TRIO"][0]["mise_unitaire_eur"] == 1.5
    print("  [OK] test_eligibilite_des_produits")


def test_non_partant_jamais_dans_un_ticket_genere():
    eng = NewValueEngine()
    # Le n° 8 est NP : ni base, ni outsider, ni associé
    st = eng.generate_smart_tickets([8, 5], [8, 5, 3, 1, 9, 2, 7], 8, False, False, race=_race(bets=BETS_FULL), active_nums=[5, 3, 1, 9, 2, 7, 4, 6])
    for t in st["tickets"]:
        assert 8 not in t["chevaux"], t
    # Une seule base active restante : aucun ticket construit (pas de duo)
    assert st["tickets"] == [] or all(len(t["bases"]) == 2 for t in st["tickets"])
    print("  [OK] test_non_partant_jamais_dans_un_ticket_genere")


# ── A08 / A12 : états de course et porte de diffusion ─────────────────

def test_course_annulee_et_partie_sans_ticket_actif():
    db = _db()
    now = datetime.utcnow()
    # Annulée
    db.save_race(_race("R1C1_10092026_TESTVILLE", bets=BETS_FULL, status="ANNULEE")); db.save_runners("R1C1_10092026_TESTVILLE", _runners())
    db.save_prediction(_prediction("R1C1_10092026_TESTVILLE"))
    # Partie (départ il y a 10 min), sans arrivée
    db.save_race(_race("R1C2_10092026_TESTVILLE", bets=BETS_FULL, start_utc=(now - timedelta(minutes=10)).isoformat() + "Z"))
    db.save_runners("R1C2_10092026_TESTVILLE", _runners()); db.save_prediction(_prediction("R1C2_10092026_TESTVILLE"))
    # À venir
    db.save_race(_race("R1C3_10092026_TESTVILLE", bets=BETS_FULL, start_utc=(now + timedelta(hours=2)).isoformat() + "Z"))
    db.save_runners("R1C3_10092026_TESTVILLE", _runners()); db.save_prediction(_prediction("R1C3_10092026_TESTVILLE"))
    logs = {l["race_id"]: l for l in TurfBenchmarkLab(db).get_historical_race_logs()}
    a, b, c = logs["R1C1_10092026_TESTVILLE"], logs["R1C2_10092026_TESTVILLE"], logs["R1C3_10092026_TESTVILLE"]
    assert a["status"] == "ANNULÉE" and a["is_cancelled"] is True and a["decision"] == "🚫 COURSE ANNULÉE"
    assert a["publishable"] is False and a["publication_reason"] == "RACE_CANCELLED"
    assert b["status"] == "PARTIE" and b["is_started"] is True and b["publishable"] is False and b["publication_reason"] == "RACE_STARTED"
    assert c["status"] == "PROGRAMMÉE" and c["is_started"] is False and c["publishable"] is True
    print("  [OK] test_course_annulee_et_partie_sans_ticket_actif")


def test_porte_refuse_sans_preuve():
    db = _db()
    now = datetime(2026, 9, 10, 10, 0)
    # Départ illisible
    r = _race(bets=BETS_FULL, start_utc=None); r["scheduled_start_time"] = "??"
    db.save_race(r); db.save_runners(RID, _runners(captured=now)); db.save_prediction(_prediction())
    assert can_publish(db, RID, "T_MATIN", now_utc=now, log=False) == (False, "START_UNKNOWN")
    # Départ connu mais verrou sans preuve
    r2 = _race("R1C2_10092026_TESTVILLE", bets=BETS_FULL); db.save_race(r2); db.save_runners("R1C2_10092026_TESTVILLE", _runners(captured=now))
    db.save_prediction(_prediction("R1C2_10092026_TESTVILLE", odds_real=None, priced_ratio=None, lock_time_utc=None))
    assert can_publish(db, "R1C2_10092026_TESTVILLE", "T_MATIN", now_utc=now, log=False) == (False, "NO_LOCK_PROOF")
    # Cotes périmées (capture il y a 3 h)
    r3 = _race("R1C3_10092026_TESTVILLE", bets=BETS_FULL); db.save_race(r3); db.save_runners("R1C3_10092026_TESTVILLE", _runners(captured=now - timedelta(hours=3)))
    db.save_prediction(_prediction("R1C3_10092026_TESTVILLE", lock_time_utc=(now - timedelta(minutes=30)).isoformat()))
    assert can_publish(db, "R1C3_10092026_TESTVILLE", "T_MATIN", now_utc=now, log=False) == (False, "STALE_ODDS")
    # Tout est prouvé => OK
    r4 = _race("R1C4_10092026_TESTVILLE", bets=BETS_FULL); db.save_race(r4); db.save_runners("R1C4_10092026_TESTVILLE", _runners(captured=now - timedelta(minutes=5)))
    db.save_prediction(_prediction("R1C4_10092026_TESTVILLE", lock_time_utc=(now - timedelta(minutes=30)).isoformat()))
    assert can_publish(db, "R1C4_10092026_TESTVILLE", "T_MATIN", now_utc=now, log=False) == (True, "OK")
    print("  [OK] test_porte_refuse_sans_preuve")


def test_validite_et_provenance_des_cotes():
    assert not is_valid_odds(0) and not is_valid_odds(-1) and not is_valid_odds(float("nan")) and not is_valid_odds("x")
    assert is_valid_odds(1.5) and is_valid_odds(15.0) and is_valid_odds(999)
    # Sans provenance : 0 / -1 ne sont jamais « réelles » ; 15 = sentinelle
    assert all_default_odds({"morning_odds": 0, "odds_t15": -1, "final_odds": 15.0}) is True
    assert all_default_odds({"morning_odds": 15.0, "odds_t15": 4.2, "final_odds": 15.0}) is False
    # Avec provenance : une vraie cote à 15 est réelle ; une sentinelle marquée non réelle ne l'est pas
    assert all_default_odds({"morning_odds": 15.0, "odds_t15": 15.0, "final_odds": 15.0, "odds_is_real": True}) is False
    assert all_default_odds({"morning_odds": 4.0, "odds_t15": 4.0, "final_odds": 4.0, "odds_is_real": False}) is True
    runners = [{"num": 1, "odds_t15": 0, "morning_odds": -1}, {"num": 2, "odds_t15": 3.5}, {"num": 3, "odds_t15": 15.0, "odds_is_real": True}, {"num": 4, "odds_t15": 15.0}]
    assert abs(priced_ratio(runners) - 0.5) < 1e-9
    print("  [OK] test_validite_et_provenance_des_cotes")


# ── Rendu HTML : rien n'est reconstruit côté client ───────────────────

def test_html_rend_les_tickets_depuis_le_contrat():
    db = _db()
    db.save_race(_race(bets=BETS_FULL)); db.save_runners(RID, _runners())
    db.save_prediction(_prediction())
    lab = TurfBenchmarkLab(db)
    report = lab.generate_comparative_report()
    out = os.path.join(tempfile.mkdtemp(), "dash.html")
    generate_html_dashboard(report, output_path=out)
    html = open(out, encoding="utf-8").read()
    assert "renderTickets(" in html and "tickets-container" in html
    assert "ticket-1-text" not in html and "X - X - X / ${associes}" not in html   # plus de reconstruction JS
    assert "Copie impossible" in html                                                 # presse-papiers honnête
    assert "raceIsStarted(" in html and "COURSE ANNULÉE" in html
    print("  [OK] test_html_rend_les_tickets_depuis_le_contrat")


def main():
    test_contrat_persiste_et_ecrasement_refuse()
    test_archive_sans_contrat_restituee_sans_valeur_favorable()
    test_no_bet_supprime_tous_les_tickets()
    test_trio_sans_doublon()
    test_combinaisons_quinte_par_enumeration()
    test_eligibilite_des_produits()
    test_non_partant_jamais_dans_un_ticket_genere()
    test_course_annulee_et_partie_sans_ticket_actif()
    test_porte_refuse_sans_preuve()
    test_validite_et_provenance_des_cotes()
    test_html_rend_les_tickets_depuis_le_contrat()
    print("\n=== 11 TESTS LOT 1 (SORTIES ABONNÉS) PASSENT ===")


if __name__ == "__main__":
    main()
