"""verrous_tentatives et verrous_refus : les 16 tests prévus par la spécification
(docs/VERROUS_TENTATIVES.md, version 3 et retouches du 20/10/2026).

Tous les instants sont injectés : aucune dépendance à l'heure réelle.
"""
import io
import os
import re
import sqlite3
import sys
import tempfile
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import verrous
from turf_lab.daily_sync import DailySyncManager
from turf_lab.database import TurfDatabase
from turf_lab.odds_quality import DEFAULT_ODDS, MIN_LOCK_RATIO, MIN_PRICED_RATIO
from turf_lab.publication_gate import GATE_VERSION

# Dérivés de MISE_EN_SERVICE (correction du dev Radar du 20/11) : le décalage
# de la mise en service tient en une ligne. Jamais avant la bascule du plancher
# du matin (23/11 : 07h30 UTC en hiver), dont dépend le cas Moulins/Pontchâteau.
_JOUR = max(date.fromisoformat(verrous.MISE_EN_SERVICE), date(2026, 11, 23))
JOUR = _JOUR.isoformat()
LENDEMAIN = (_JOUR + timedelta(days=1)).isoformat()
AVANT = (date.fromisoformat(verrous.MISE_EN_SERVICE) - timedelta(days=1)).isoformat()   # course non suivie
J_API, AVANT_API = _JOUR.strftime("%d%m%Y"), date.fromisoformat(AVANT).strftime("%d%m%Y")


def _t(hm, jour=JOUR):
    h, m = (int(x) for x in hm.split(":"))
    d = datetime.fromisoformat(jour)
    return d.replace(hour=h, minute=m)


def _race(rid=f"R1C1_{J_API}_TESTVILLE", jour=JOUR, depart="14:00", status="SCHEDULED", start_utc=True):
    return {
        "race_id": rid, "date": jour, "meeting_number": 1, "race_number": 1, "name": "Prix Test",
        "hippodrome": "TESTVILLE", "discipline": "TROT_ATTELE", "distance": 2700, "track_type": "SABLE",
        "track_condition": "BON", "rope": "GAUCHE", "autostart": False,
        "scheduled_start_time": f"{depart} GMT" if depart else None, "status": status,
        "start_time_utc": f"{jour}T{depart}:00Z" if (depart and start_utc) else None,
    }


def _runners(n=10, priced=10):
    out = []
    for i in range(1, n + 1):
        odds = 4.0 + i if i <= priced else DEFAULT_ODDS
        out.append({"num": i, "horse_name": f"Cheval{i}", "sex": "M", "age": 5, "driver_jockey": "J.TEST",
                    "trainer": "T.TEST", "weight": 60.0, "draw": i, "shoeing": "FERRE", "blinkers": "SANS",
                    "morning_odds": odds, "odds_t15": odds, "final_odds": odds, "is_non_partant": False,
                    "press_citation_count": 0, "music": "1a2a3a", "earnings": 50000.0,
                    "record_chrono": 0.0, "official_rating": 0.0})
    return out


@pytest.fixture
def banc():
    db = TurfDatabase(os.path.join(tempfile.mkdtemp(), "verrous.db"))
    with redirect_stdout(io.StringIO()):
        mgr = DailySyncManager(db)
    return db, mgr


def _ajoute(db, race, runners):
    db.save_race(race)
    db.save_runners(race["race_id"], runners)


def _verrou(mgr, race, runners, horizon, quand):
    buf = io.StringIO()
    with redirect_stdout(buf):
        n = mgr._lock_horizon(race, runners, horizon, now_utc=quand)
    return n, buf.getvalue()


def _lignes(db, table, race_id=None):
    with db.transaction() as conn:
        sql = f"SELECT * FROM {table}" + (" WHERE race_id = ?" if race_id else "") + " ORDER BY id"
        return [dict(r) for r in conn.execute(sql, (race_id,) if race_id else ())]


def _statut(db, race_id, statut):
    with db.transaction() as conn:
        conn.execute("UPDATE races SET status = ? WHERE race_id = ?", (statut, race_id))


def _refuses(db, quand):
    with redirect_stdout(io.StringIO()):
        return verrous.ecrire_refuses(db, quand)


def _sans_defaut(db, fin=JOUR):
    assert verrous.controles(db, verrous.MISE_EN_SERVICE, fin) == {f"C{i}": [] for i in range(1, 6)}


# 1 ─────────────────────────────────────────────────────────────────────
def test_partiel_identique_a_l_edition(banc):
    db, mgr = banc
    race, runners = _race(), _runners(10, 7)
    _ajoute(db, race, runners)
    n, log = _verrou(mgr, race, runners, "T90", _t("12:21"))
    assert n == 1 and "VERROU_TENTATIVE " in log
    (ligne,) = _lignes(db, "verrous_tentatives")
    pred = [p for p in db.get_predictions(race["race_id"]) if p["engine_name"] == "NEW_VALUE_ENGINE"][0]
    assert ligne["issue"] == "PARTIEL" and pred["odds_real"] == 0
    assert ligne["part_cotee"] == pred["priced_ratio"] == 0.7
    assert ligne["decided_at_utc"] == ligne["heure_max_utc"] == pred["lock_time_utc"]
    assert (ligne["n_partants"], ligne["n_cotes"]) == (10, 7)
    assert (ligne["min_lock_ratio"], ligne["min_priced_ratio"], ligne["gate_version"]) == (
        MIN_LOCK_RATIO, MIN_PRICED_RATIO, GATE_VERSION)
    _sans_defaut(db)


# 2 ─────────────────────────────────────────────────────────────────────
def test_declencheurs_ajout_seul_sur_les_deux_tables(banc):
    db, mgr = banc
    race = _race()
    _ajoute(db, race, _runners(10, 7))
    _verrou(mgr, race, _runners(10, 7), "T90", _t("12:21"))
    _verrou(mgr, race, _runners(10, 3), "T30", _t("13:26"))
    for table in ("verrous_tentatives", "verrous_refus"):
        assert _lignes(db, table)
        conn = db.get_connection()
        with pytest.raises(sqlite3.DatabaseError, match="ajout seul"):
            conn.execute(f"UPDATE {table} SET part_cotee = 0.99")
        with pytest.raises(sqlite3.DatabaseError, match="ajout seul"):
            conn.execute(f"DELETE FROM {table}")
        conn.close()


# 3 ─────────────────────────────────────────────────────────────────────
def test_une_erreur_d_ecriture_n_annule_rien(banc, monkeypatch):
    db, mgr = banc
    race = _race()
    _ajoute(db, race, _runners(10, 7))

    def panne(*_a, **_k):
        raise sqlite3.OperationalError("disque plein")
    monkeypatch.setattr(db, "save_verrou_tentative", panne)
    monkeypatch.setattr(db, "save_verrou_refus", panne)
    n, log = _verrou(mgr, race, _runners(10, 7), "T90", _t("12:21"))
    assert n == 1 and "VERROU_TENTATIVE_ERROR" in log                    # le verrou est posé quand même
    assert db.has_prediction(race["race_id"], "NEW_VALUE_ENGINE", "T90")
    avant = mgr.gate_refused
    n, log = _verrou(mgr, race, _runners(10, 2), "T30", _t("13:26"))
    assert n == 0 and mgr.gate_refused == avant + 1 and "VERROU_REFUS_ERROR" in log and "GATE_REFUSED " in log
    assert not db.has_prediction(race["race_id"], "NEW_VALUE_ENGINE", "T30")


def test_ligne_gate_refused_porte_les_deux_nombres(banc):
    db, mgr = banc
    race = _race()
    _ajoute(db, race, _runners(10, 4))
    _, log = _verrou(mgr, race, _runners(10, 4), "T_MATIN", _t("07:30"))
    ligne = [l for l in log.splitlines() if l.startswith("GATE_REFUSED ")][0]
    assert '"n_partants": 10' in ligne and '"n_cotes": 4' in ligne
    (j,) = _lignes(db, "verrous_refus")
    assert (j["part_cotee"], j["n_partants"], j["n_cotes"]) == (0.4, 10, 4)
    assert j["observed_at_utc"] == _t("07:30").isoformat()


# 4 ─────────────────────────────────────────────────────────────────────
def test_refuse_passage_a_la_part_la_plus_haute(banc):
    db, mgr = banc
    race = _race()
    _ajoute(db, race, _runners())
    for hm, cotes in (("07:30", 2), ("08:00", 4), ("09:00", 4), ("10:00", 3), ("14:05", 4)):
        _verrou(mgr, race, _runners(10, cotes), "T_MATIN", _t(hm))
    with db.transaction() as conn:            # un passage après le départ (14h00), plus haut, ignoré
        conn.execute("INSERT INTO verrous_refus (race_id, horizon, observed_at_utc, part_cotee, n_partants, n_cotes,"
                     " min_lock_ratio, min_priced_ratio, gate_version) VALUES (?, 'T_MATIN', ?, 0.45, 20, 9, 0.5, 0.9, ?)",
                     (race["race_id"], _t("14:10").isoformat(), GATE_VERSION))
    _statut(db, race["race_id"], "FINISHED")
    assert _refuses(db, _t("16:00")) == 1
    (r,) = _lignes(db, "verrous_tentatives")
    assert r["issue"] == "REFUSE" and r["part_cotee"] == 0.4 and (r["n_partants"], r["n_cotes"]) == (10, 4)
    assert r["heure_max_utc"] == _t("08:00").isoformat()                  # le plus ancien des deux 0,4
    assert r["decided_at_utc"] == _t("16:00").isoformat()
    _sans_defaut(db)


# 5 ─────────────────────────────────────────────────────────────────────
def test_seuils_et_gate_version_du_passage_retenu(banc):
    db, _ = banc
    race = _race()
    _ajoute(db, race, _runners())
    with db.transaction() as conn:
        for hm, part, lock, prix, version in (("09:00", 0.30, 0.50, 0.90, "PG2"),
                                              ("21:45", 0.40, 0.45, 0.85, "PG3")):    # porte changée le soir
            conn.execute("INSERT INTO verrous_refus (race_id, horizon, observed_at_utc, part_cotee, n_partants,"
                         " n_cotes, min_lock_ratio, min_priced_ratio, gate_version) VALUES (?, 'T15', ?, ?, 10, ?, ?, ?, ?)",
                         (race["race_id"], _t(hm).isoformat(), part, int(part * 10), lock, prix, version))
    db.save_race(dict(race, scheduled_start_time="22:00 GMT", start_time_utc=f"{JOUR}T22:00:00Z"))
    assert _refuses(db, _t("00:10", LENDEMAIN)) == 1
    (r,) = _lignes(db, "verrous_tentatives")
    assert (r["part_cotee"], r["min_lock_ratio"], r["min_priced_ratio"], r["gate_version"]) == (0.4, 0.45, 0.85, "PG3")


# 6 ─────────────────────────────────────────────────────────────────────
def test_refuse_puis_verrouille_jamais_de_refuse(banc):
    db, mgr = banc
    race = _race()
    _ajoute(db, race, _runners())
    _verrou(mgr, race, _runners(10, 2), "T_MATIN", _t("07:30"))
    _verrou(mgr, race, _runners(10, 10), "T_MATIN", _t("09:00"))          # verrouillé plus tard, à 100 %
    _verrou(mgr, race, _runners(10, 3), "T90", _t("12:21"))               # T90 refusé dans sa fenêtre…
    for h in ("T90", "T30"):                                              # …puis posé dans la passe de T30
        _verrou(mgr, race, _runners(10, 10), h, _t("13:26"))
    _verrou(mgr, race, _runners(10, 10), "T15", _t("13:43"))
    _statut(db, race["race_id"], "FINISHED")
    assert _refuses(db, _t("16:00")) == 0
    assert _lignes(db, "verrous_tentatives") == []
    _sans_defaut(db)


# 7 ─────────────────────────────────────────────────────────────────────
def test_liste_non_observee_et_c1(banc):
    db, mgr = banc
    annulee = _race(f"R2C1_{J_API}_TESTVILLE")
    inconnue = _race(f"R2C2_{J_API}_TESTVILLE", depart=None)
    sans_passage = _race(f"R2C3_{J_API}_TESTVILLE")
    for r in (annulee, inconnue, sans_passage):
        _ajoute(db, r, _runners())
    _verrou(mgr, annulee, _runners(10, 2), "T_MATIN", _t("07:30"))        # refusée le matin…
    _statut(db, annulee["race_id"], "ANNULEE")                           # …puis annulée
    assert _refuses(db, _t("00:10", LENDEMAIN)) == 0                       # jamais de REFUSE pour une annulée
    motifs = {(n["race_id"], n["horizon"]): n["motif"] for n in verrous.non_observes(db, JOUR)}
    assert {motifs[(annulee["race_id"], h)] for h in verrous.HORIZONS} == {"ANNULEE"}
    assert {motifs[(inconnue["race_id"], h)] for h in verrous.HORIZONS} == {"HEURE_INCONNUE"}
    assert {motifs[(sans_passage["race_id"], h)] for h in verrous.HORIZONS} == {"AUCUN_PASSAGE"}
    assert not any(l["issue"] == "REFUSE" for l in _lignes(db, "verrous_tentatives"))
    _sans_defaut(db)


# 8 ─────────────────────────────────────────────────────────────────────
def test_aucune_reprise_du_passe(banc):
    db, mgr = banc
    jour = AVANT                                                          # avant la mise en service
    race = _race(f"R1C1_{AVANT_API}_TESTVILLE", jour=jour)
    _ajoute(db, race, _runners())
    _verrou(mgr, race, _runners(10, 2), "T_MATIN", _t("06:30", jour))
    _verrou(mgr, race, _runners(10, 7), "T90", _t("12:21", jour))
    assert _lignes(db, "verrous_refus") == [] and _lignes(db, "verrous_tentatives") == []
    assert _refuses(db, _t("00:10", verrous.MISE_EN_SERVICE)) == 0
    assert verrous.non_observes(db, jour) == []
    assert verrous.controles(db, verrous.MISE_EN_SERVICE, jour) == {f"C{i}": [] for i in range(1, 6)}


# 9 ─────────────────────────────────────────────────────────────────────
def test_verrou_a_90_pour_cent_ou_plus_aucune_ligne(banc):
    db, mgr = banc
    race = _race()
    _ajoute(db, race, _runners())
    for h, cotes in (("T_MATIN", 9), ("T90", 10)):
        assert _verrou(mgr, race, _runners(10, cotes), h, _t("12:21"))[0] == 1
    assert _lignes(db, "verrous_tentatives") == [] and _lignes(db, "verrous_refus") == []


# 10 ────────────────────────────────────────────────────────────────────
def test_calcul_repete_et_courses_fermees(banc):
    db, mgr = banc
    # Course datée du 24/11, partie le 25/11 à 00h20 UTC : refusée en T-30 avant
    # minuit, puis plus aucun horizon dû après minuit (elle n'est plus du jour).
    tardive = dict(_race(f"R3C1_{J_API}_TESTVILLE", depart="00:20"), start_time_utc=f"{LENDEMAIN}T00:20:00Z")
    ouverte = _race(f"R3C2_{J_API}_TESTVILLE")
    for r in (tardive, ouverte):
        _ajoute(db, r, _runners())
    _verrou(mgr, tardive, _runners(10, 2), "T30", _t("23:50"))
    assert mgr.due_horizons(tardive["scheduled_start_time"], JOUR, _t("00:05", LENDEMAIN)) == []
    _verrou(mgr, ouverte, _runners(10, 2), "T_MATIN", _t("07:30"))
    assert _refuses(db, _t("23:40")) == 0                                  # ni FINISHED ni d'un jour passé
    premier = [_refuses(db, _t("00:05", LENDEMAIN)), _refuses(db, _t("00:20", LENDEMAIN)),
               _refuses(db, _t("09:00", LENDEMAIN))]
    assert premier == [2, 0, 0]                                            # une seule ligne par couple
    lignes = _lignes(db, "verrous_tentatives")
    assert len(lignes) == 2 and all(l["decided_at_utc"] == _t("00:05", LENDEMAIN).isoformat() for l in lignes)
    assert {(l["race_id"], l["horizon"]) for l in lignes} == {(tardive["race_id"], "T30"), (ouverte["race_id"], "T_MATIN")}
    _sans_defaut(db)


# 11 ────────────────────────────────────────────────────────────────────
def test_empreinte_des_deux_seuils():
    """Un changement de MIN_LOCK_RATIO ou de MIN_PRICED_RATIO demande une
    nouvelle annonce (ligne datée) et la mise à jour de SEUILS_ANNONCES."""
    assert (MIN_LOCK_RATIO, MIN_PRICED_RATIO) == verrous.SEUILS_ANNONCES == (0.50, 0.90)


# 12 ────────────────────────────────────────────────────────────────────
def _journee_complete(db, mgr):
    """Une journée variée : verrous pleins, partiels, refus jusqu'au départ,
    annulation, heure inconnue, course jamais passée."""
    pleine, partielle, refusee = (_race(f"R4C{i}_{J_API}_TESTVILLE") for i in (1, 2, 3))
    annulee, inconnue = _race(f"R4C4_{J_API}_TESTVILLE"), _race(f"R4C5_{J_API}_TESTVILLE", depart=None)
    for r in (pleine, partielle, refusee, annulee, inconnue):
        _ajoute(db, r, _runners())
    for h, hm in (("T_MATIN", "07:30"), ("T90", "12:21"), ("T30", "13:26"), ("T15", "13:43")):
        _verrou(mgr, pleine, _runners(10, 10), h, _t(hm))
        _verrou(mgr, partielle, _runners(10, 6 if h != "T15" else 10), h, _t(hm))
        _verrou(mgr, refusee, _runners(10, 3), h, _t(hm))
    _statut(db, annulee["race_id"], "ANNULEE")
    for r in (pleine, partielle, refusee):
        _statut(db, r["race_id"], "FINISHED")
    return pleine, partielle, refusee, annulee, inconnue


def test_c1_a_c5_a_zero_sur_un_banc_complet(banc):
    db, mgr = banc
    _journee_complete(db, mgr)
    assert _refuses(db, _t("16:00")) == 4
    _sans_defaut(db)
    issues = [l["issue"] for l in _lignes(db, "verrous_tentatives")]
    assert issues.count("PARTIEL") == 3 and issues.count("REFUSE") == 4


# 13 ────────────────────────────────────────────────────────────────────
def test_cas_moulins_et_pontchateau_huit_refuse(banc):
    """Deux courses refusées à chaque passage jusqu'au départ (constat du dev
    Radar du 14/10 sur Moulins R11C1 et Pontchâteau R12C1 du 10/10) : quatre
    REFUSE chacune, huit en tout."""
    db, mgr = banc
    moulins = dict(_race(f"R11C1_{J_API}_MOULINS", depart="15:05"), hippodrome="MOULINS")
    pont = dict(_race(f"R12C1_{J_API}_PONTCHATEAU", depart="15:40"), hippodrome="PONTCHATEAU")
    for r in (moulins, pont):
        _ajoute(db, r, _runners(12, 3))
    t = _t("07:00")
    while t <= _t("16:00"):                                              # une passe toutes les 15 minutes
        for r in (moulins, pont):
            for h in mgr.due_horizons(r["scheduled_start_time"], JOUR, t):
                _verrou(mgr, r, _runners(12, 3), h, t)
        t += timedelta(minutes=15)
    assert _refuses(db, _t("00:05", LENDEMAIN)) == 8
    lignes = _lignes(db, "verrous_tentatives")
    assert sorted((l["race_id"], l["horizon"]) for l in lignes) == sorted(
        (r["race_id"], h) for r in (moulins, pont) for h in verrous.HORIZONS)
    assert all(l["issue"] == "REFUSE" and l["part_cotee"] == 0.25 and l["part_cotee"] < l["min_lock_ratio"]
               for l in lignes)
    _sans_defaut(db)


# 14 ────────────────────────────────────────────────────────────────────
class _Programme:
    def __init__(self, ids):
        self.ids = ids
        self.appels = 0

    def programme_race_ids(self, _date):
        self.appels += 1
        return self.ids


def test_rapport_quotidien(banc, tmp_path):
    db, mgr = banc
    pleine, partielle, refusee, annulee, inconnue = _journee_complete(db, mgr)
    manquante = f"R9C9_{J_API}_JAMAISVUE"
    ids = [r["race_id"] for r in (pleine, partielle, refusee, annulee, inconnue)] + [manquante]
    with redirect_stdout(io.StringIO()):
        etat = verrous.passage(db, _Programme(ids), str(tmp_path), _t("00:05", LENDEMAIN))
    chemin = tmp_path / "rapports" / "verrous" / f"{JOUR}.md"
    assert etat["refuse_ecrits"] == 4 and etat["rapport"] == os.path.join("rapports", "verrous", f"{JOUR}.md")
    texte = chemin.read_text(encoding="utf-8")
    assert "| T_MATIN | 1 | 1 |" in texte and "| T15 | 0 | 1 |" in texte and "| **Total** | 3 | 4 |" in texte
    assert "| ANNULEE | 4 |" in texte and "| HEURE_INCONNUE | 4 |" in texte and "| AUCUN_PASSAGE | 0 |" in texte
    assert f"- `{manquante}`" in texte and "absentes de `races` : 1" in texte
    assert all(f"| C{i} | 0 |" in texte for i in range(1, 6))
    # Aucune probabilité, rien de l'ombre
    probas = {f"{v}" for p in db.get_predictions(pleine["race_id"]) for v in (p.get("probabilities") or {}).values()}
    assert not any(v in texte for v in probas if len(v) > 3)
    assert "ombre" not in texte.lower() or "rien de l'ombre" in texte.lower()
    assert "fondamental" not in texte.lower() and "probabilit" not in texte.replace("aucune probabilité", "")
    # Régénéré à l'identique, rattrapé après un envoi refusé (fichier perdu)
    with redirect_stdout(io.StringIO()):
        verrous.passage(db, _Programme(ids), str(tmp_path), _t("00:20", LENDEMAIN))
    assert chemin.read_text(encoding="utf-8") == texte
    chemin.unlink()
    with redirect_stdout(io.StringIO()):
        verrous.passage(db, _Programme(ids), str(tmp_path), _t("00:35", LENDEMAIN))
    assert chemin.read_text(encoding="utf-8") == texte
    # Programme indisponible : rapport reporté, rien d'écrit à moitié
    autre = tmp_path / "autre"
    with redirect_stdout(io.StringIO()):
        etat = verrous.passage(db, _Programme(None), str(autre), _t("00:50", LENDEMAIN))
    assert etat["rapport"] == "REPORTE_PROGRAMME_INDISPONIBLE" and not (autre / "rapports").exists()


def test_rapport_liste_les_couples_en_defaut(banc, tmp_path):
    db, mgr = banc
    race = _race()
    _ajoute(db, race, _runners())
    with db.transaction() as conn:                                       # PARTIEL sans édition : défaut C3
        conn.execute("INSERT INTO verrous_tentatives (race_id, horizon, issue, part_cotee, n_partants, n_cotes,"
                     " decided_at_utc, heure_max_utc, min_lock_ratio, min_priced_ratio, gate_version)"
                     " VALUES (?, 'T30', 'PARTIEL', 0.6, 10, 6, ?, ?, 0.5, 0.9, ?)",
                     (race["race_id"], _t("13:26").isoformat(), _t("13:26").isoformat(), GATE_VERSION))
    texte = verrous.rapport(db, JOUR, [race["race_id"]])
    assert "| C3 | 1 |" in texte and f"- `{race['race_id']}` T30" in texte


# 15 ────────────────────────────────────────────────────────────────────
def test_depart_repousse_puis_verrou_aucun_refuse(banc):
    db, mgr = banc
    race = _race()
    _ajoute(db, race, _runners())
    for hm in ("12:21", "12:36", "12:51"):
        _verrou(mgr, race, _runners(10, 3), "T90", _t(hm))                 # refusé avant le départ de 14h00
    repoussee = dict(race, scheduled_start_time="16:00 GMT", start_time_utc=f"{JOUR}T16:00:00Z")
    db.save_race(repoussee)                                               # départ repoussé de 2 h
    assert "T90" in mgr.due_horizons(repoussee["scheduled_start_time"], JOUR, _t("14:30"))
    assert _verrou(mgr, repoussee, _runners(10, 10), "T90", _t("14:30"))[0] == 1
    _statut(db, race["race_id"], "FINISHED")
    assert _refuses(db, _t("18:00")) == 0
    _sans_defaut(db)


# 16 ────────────────────────────────────────────────────────────────────
def test_arrivee_jamais_recue_refuse_le_lendemain_avant_le_rapport(banc, tmp_path):
    db, mgr = banc
    race = _race(depart="20:00")
    _ajoute(db, race, _runners())
    for h, hm in (("T_MATIN", "07:30"), ("T90", "18:21"), ("T30", "19:26"), ("T15", "19:43")):
        _verrou(mgr, race, _runners(10, 4), h, _t(hm))
    assert _refuses(db, _t("23:30")) == 0                                  # jamais FINISHED, encore du jour
    with redirect_stdout(io.StringIO()):
        etat = verrous.passage(db, _Programme([race["race_id"]]), str(tmp_path), _t("00:05", LENDEMAIN))
    assert etat["refuse_ecrits"] == 4
    texte = (tmp_path / "rapports" / "verrous" / f"{JOUR}.md").read_text(encoding="utf-8")
    assert "| **Total** | 0 | 4 |" in texte and all(f"| C{i} | 0 |" in texte for i in range(1, 6))


def test_garde_de_la_bande_partiel(banc):
    """Défense en profondeur : une ligne PARTIEL n'est jamais écrite hors de la
    bande [MIN_LOCK_RATIO ; MIN_PRICED_RATIO[, même appelée directement (C5)."""
    db, _ = banc
    race = _race()
    _ajoute(db, race, _runners())
    for ratio in (0.95, 0.9, 0.3, 0.4999):
        assert verrous.enregistrer_partiel(db, race, "T90", ratio, (10, 9), _t("12:21").isoformat()) is False
    with redirect_stdout(io.StringIO()):
        assert verrous.enregistrer_partiel(db, race, "T90", 0.5, (10, 5), _t("12:21").isoformat()) is True
    assert [l["part_cotee"] for l in _lignes(db, "verrous_tentatives")] == [0.5]


def test_depart_avance_apres_les_refus_aucun_passage(banc):
    """Correction du dev Radar du 20/11 : tous les passages refusés sont
    postérieurs au départ enregistré dans races (départ avancé après les
    refus). Aucun REFUSE (règle 1), mais l'horizon est « non observée »
    (AUCUN_PASSAGE) et C1 reste à zéro : jamais absent des deux."""
    db, mgr = banc
    race = _race()
    _ajoute(db, race, _runners())
    for h, hm in (("T_MATIN", "12:05"), ("T90", "12:21"), ("T30", "13:26"), ("T15", "13:43")):
        _verrou(mgr, race, _runners(10, 3), h, _t(hm))                     # refus avant le départ de 14h00…
    db.save_race(dict(race, scheduled_start_time="12:00 GMT", start_time_utc=f"{JOUR}T12:00:00Z"))   # …avancé à 12h00
    _statut(db, race["race_id"], "FINISHED")
    assert _refuses(db, _t("16:00")) == 0
    motifs = {(n["race_id"], n["horizon"]): n["motif"] for n in verrous.non_observes(db, JOUR)}
    assert {motifs[(race["race_id"], h)] for h in verrous.HORIZONS} == {"AUCUN_PASSAGE"}
    assert len(motifs) == 4
    _sans_defaut(db)
