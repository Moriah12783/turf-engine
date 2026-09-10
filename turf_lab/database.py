"""Database management for the Turf Prediction Engine & Benchmarking Lab."""

import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, List, Optional


class TurfDatabase:
    """SQLite Database wrapper for managing turf races, runners, predictions, and results."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            base_dir = os.path.dirname(os.path.abspath(__file__))
            self.db_path = os.path.join(base_dir, "turf_bench.db")
        else:
            self.db_path = db_path
            
        parent_dir = os.path.dirname(self.db_path)
        if parent_dir and not os.path.exists(parent_dir):
            os.makedirs(parent_dir, exist_ok=True)
            
        self.init_db()

    def get_connection(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    @contextmanager
    def transaction(self):
        conn = self.get_connection()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_db(self):
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.executescript("""
            CREATE TABLE IF NOT EXISTS races (
                race_id TEXT PRIMARY KEY,
                date TEXT NOT NULL,
                meeting_number INTEGER,
                race_number INTEGER,
                name TEXT,
                hippodrome TEXT,
                discipline TEXT,
                distance INTEGER,
                track_type TEXT,
                track_condition TEXT,
                rope TEXT,
                autostart BOOLEAN,
                scheduled_start_time TEXT,
                status TEXT DEFAULT 'SCHEDULED'
            );

            CREATE TABLE IF NOT EXISTS runners (
                runner_id TEXT PRIMARY KEY,
                race_id TEXT NOT NULL,
                num INTEGER NOT NULL,
                horse_name TEXT NOT NULL,
                sex TEXT,
                age INTEGER,
                driver_jockey TEXT,
                trainer TEXT,
                weight REAL,
                draw INTEGER,
                shoeing TEXT,
                blinkers TEXT,
                morning_odds REAL,
                odds_t15 REAL,
                final_odds REAL,
                is_non_partant BOOLEAN DEFAULT 0,
                press_citation_count INTEGER DEFAULT 0,
                music TEXT,
                earnings REAL DEFAULT 0.0,
                record_chrono REAL,
                official_rating REAL,
                FOREIGN KEY (race_id) REFERENCES races (race_id)
            );

            CREATE TABLE IF NOT EXISTS predictions (
                prediction_id TEXT PRIMARY KEY,
                race_id TEXT NOT NULL,
                engine_name TEXT NOT NULL,
                horizon TEXT DEFAULT 'T15',
                created_at TEXT NOT NULL,
                lock_time TEXT NOT NULL,
                selection_json TEXT NOT NULL,
                bases_json TEXT,
                outsider_num INTEGER,
                probabilities_json TEXT,
                metadata_json TEXT,
                FOREIGN KEY (race_id) REFERENCES races (race_id),
                UNIQUE(race_id, engine_name, horizon)
            );

            CREATE TABLE IF NOT EXISTS race_results (
                race_id TEXT PRIMARY KEY,
                arrival_order_json TEXT NOT NULL,
                disqualified_json TEXT,
                recorded_at TEXT NOT NULL,
                FOREIGN KEY (race_id) REFERENCES races (race_id)
            );

            CREATE TABLE IF NOT EXISTS rapports (
                rapport_id TEXT PRIMARY KEY,
                race_id TEXT NOT NULL,
                bet_type TEXT NOT NULL,
                combination TEXT NOT NULL,
                dividend REAL NOT NULL,
                FOREIGN KEY (race_id) REFERENCES races (race_id)
            );

            CREATE TABLE IF NOT EXISTS odds_snapshots (
                race_id TEXT NOT NULL,
                num INTEGER NOT NULL,
                horizon TEXT NOT NULL,
                odds REAL,
                captured_at TEXT NOT NULL,
                PRIMARY KEY (race_id, num, horizon),
                FOREIGN KEY (race_id) REFERENCES races (race_id)
            );

            CREATE INDEX IF NOT EXISTS idx_races_date ON races (date);
            CREATE INDEX IF NOT EXISTS idx_runners_race ON runners (race_id);
            CREATE INDEX IF NOT EXISTS idx_pred_race ON predictions (race_id);
            CREATE INDEX IF NOT EXISTS idx_rapports_race ON rapports (race_id);
            CREATE INDEX IF NOT EXISTS idx_snapshots_race ON odds_snapshots (race_id);
            """)

            # Safe migrations for existing databases
            try:
                cursor.execute("ALTER TABLE predictions ADD COLUMN horizon TEXT DEFAULT 'T15'")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE runners ADD COLUMN odds_t15 REAL")
            except Exception:
                pass
            try:
                cursor.execute("ALTER TABLE runners ADD COLUMN is_non_partant BOOLEAN DEFAULT 0")
            except Exception:
                pass
            # Verrou de fraîcheur (Axe 3) : état des cotes au moment du verrou,
            # persisté sur chaque prédiction (migration idempotente).
            for ddl in (
                "ALTER TABLE predictions ADD COLUMN odds_real BOOLEAN",
                "ALTER TABLE predictions ADD COLUMN priced_ratio REAL",
                "ALTER TABLE predictions ADD COLUMN lock_time_utc TEXT",
                # Correctif partenaire (résultats) : identité et horodatage
                # de course enrichis, statut PMU brut conservé.
                "ALTER TABLE races ADD COLUMN start_time_utc TEXT",
                "ALTER TABLE races ADD COLUMN pmu_statut TEXT",
                "ALTER TABLE races ADD COLUMN declared_runners INTEGER",
                # Résultats versionnés : statut (PROVISOIRE/DEFINITIVE/ANNULEE),
                # classement structuré (dead-heats), incidents, source,
                # horodatages, version et nombre de corrections.
                "ALTER TABLE race_results ADD COLUMN statut TEXT DEFAULT 'DEFINITIVE'",
                "ALTER TABLE race_results ADD COLUMN version INTEGER DEFAULT 1",
                "ALTER TABLE race_results ADD COLUMN nb_corrections INTEGER DEFAULT 0",
                "ALTER TABLE race_results ADD COLUMN source TEXT DEFAULT 'PMU_LEGACY'",
                "ALTER TABLE race_results ADD COLUMN source_url TEXT",
                "ALTER TABLE race_results ADD COLUMN pmu_statut TEXT",
                "ALTER TABLE race_results ADD COLUMN ranking_json TEXT",
                "ALTER TABLE race_results ADD COLUMN incidents_json TEXT",
                "ALTER TABLE race_results ADD COLUMN non_partants_json TEXT",
                "ALTER TABLE race_results ADD COLUMN first_seen_at TEXT",
                "ALTER TABLE race_results ADD COLUMN definitive_at TEXT",
                "ALTER TABLE race_results ADD COLUMN updated_at TEXT",
                "ALTER TABLE race_results ADD COLUMN last_checked_at TEXT",
            ):
                try:
                    cursor.execute(ddl)
                except Exception:
                    pass

            # Journal APPEND-ONLY des versions successives d'une arrivée.
            cursor.execute("""
            CREATE TABLE IF NOT EXISTS race_results_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                race_id TEXT NOT NULL,
                version INTEGER NOT NULL,
                statut TEXT NOT NULL,
                pmu_statut TEXT,
                ranking_json TEXT NOT NULL,
                incidents_json TEXT,
                non_partants_json TEXT,
                source TEXT,
                reason TEXT NOT NULL,
                recorded_at TEXT NOT NULL,
                UNIQUE(race_id, version)
            )
            """)
            cursor.execute("CREATE INDEX IF NOT EXISTS idx_results_history_race ON race_results_history (race_id)")

            # Reprise idempotente des lignes antérieures au versionnage : le
            # classement plat devient un classement structuré (rangs 1..n, sans
            # dead-heat connu), statut DEFINITIVE (c'est ainsi que le banc les a
            # toujours jugées), source PMU_LEGACY, version 1. Rien n'est
            # supprimé ni réordonné : arrival_order_json reste tel quel.
            cursor.execute("SELECT race_id, arrival_order_json, disqualified_json, recorded_at FROM race_results WHERE ranking_json IS NULL")
            legacy_rows = cursor.fetchall()
            for row in legacy_rows:
                try:
                    flat = json.loads(row["arrival_order_json"] or "[]")
                except Exception:
                    flat = []
                ranking = [{"rang": i + 1, "num": int(n), "dead_heat": False} for i, n in enumerate(flat)]
                try:
                    dq = json.loads(row["disqualified_json"] or "[]")
                except Exception:
                    dq = []
                incidents = [{"num": int(n), "type": "DISQUALIFIE"} for n in dq]
                rec = row["recorded_at"]
                cursor.execute("""
                    UPDATE race_results SET statut = 'DEFINITIVE', version = 1, nb_corrections = 0,
                        source = 'PMU_LEGACY', ranking_json = ?, incidents_json = ?, non_partants_json = '[]',
                        first_seen_at = ?, definitive_at = ?, updated_at = ?, last_checked_at = ?
                    WHERE race_id = ?
                """, (json.dumps(ranking), json.dumps(incidents), rec, rec, rec, rec, row["race_id"]))
                cursor.execute("""
                    INSERT OR IGNORE INTO race_results_history (race_id, version, statut, pmu_statut, ranking_json,
                        incidents_json, non_partants_json, source, reason, recorded_at)
                    VALUES (?, 1, 'DEFINITIVE', NULL, ?, ?, '[]', 'PMU_LEGACY', 'MIGRATION_LEGACY', ?)
                """, (row["race_id"], json.dumps(ranking), json.dumps(incidents), rec))

    def save_race(self, race_data: Dict[str, Any]):
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO races (
                race_id, date, meeting_number, race_number, name,
                hippodrome, discipline, distance, track_type,
                track_condition, rope, autostart, scheduled_start_time, status,
                start_time_utc, pmu_statut, declared_runners
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                race_data["race_id"],
                race_data.get("date", datetime.utcnow().strftime("%Y-%m-%d")),
                race_data.get("meeting_number", 1),
                race_data.get("race_number", 1),
                race_data.get("name", ""),
                race_data.get("hippodrome", ""),
                race_data.get("discipline", "TROT_ATTELE"),
                race_data.get("distance", 2700),
                race_data.get("track_type", "SABLE"),
                race_data.get("track_condition", "BON"),
                race_data.get("rope", "GAUCHE"),
                race_data.get("autostart", False),
                race_data.get("scheduled_start_time", datetime.utcnow().isoformat()),
                race_data.get("status", "SCHEDULED"),
                race_data.get("start_time_utc"),
                race_data.get("pmu_statut"),
                race_data.get("declared_runners"),
            ))

    def update_race_status(self, race_id: str, status: str, pmu_statut: Optional[str] = None):
        with self.transaction() as conn:
            cursor = conn.cursor()
            if pmu_statut is None:
                cursor.execute("UPDATE races SET status = ? WHERE race_id = ?", (status, race_id))
            else:
                cursor.execute("UPDATE races SET status = ?, pmu_statut = ? WHERE race_id = ?", (status, pmu_statut, race_id))

    def save_runners(self, race_id: str, runners_list: List[Dict[str, Any]]):
        with self.transaction() as conn:
            cursor = conn.cursor()
            for r in runners_list:
                runner_id = f"{race_id}_{r['num']}"
                cursor.execute("""
                INSERT OR REPLACE INTO runners (
                    runner_id, race_id, num, horse_name, sex, age,
                    driver_jockey, trainer, weight, draw, shoeing,
                    blinkers, morning_odds, odds_t15, final_odds,
                    is_non_partant, press_citation_count,
                    music, earnings, record_chrono, official_rating
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    runner_id,
                    race_id,
                    r["num"],
                    r.get("horse_name", f"Cheval_{r['num']}"),
                    r.get("sex", "M"),
                    r.get("age", 5),
                    r.get("driver_jockey", ""),
                    r.get("trainer", ""),
                    r.get("weight", 60.0),
                    r.get("draw", r["num"]),
                    r.get("shoeing", "FERRE"),
                    r.get("blinkers", "SANS"),
                    r.get("morning_odds", 10.0),
                    r.get("odds_t15", r.get("morning_odds", 10.0)),
                    r.get("final_odds", 10.0),
                    1 if r.get("is_non_partant", False) else 0,
                    r.get("press_citation_count", 0),
                    r.get("music", "1a2a3a"),
                    r.get("earnings", 50000.0),
                    r.get("record_chrono", 74.5),
                    r.get("official_rating", 35.0)
                ))

    def save_prediction(self, prediction_data: Dict[str, Any]):
        horizon = prediction_data.get("horizon", "T15")
        pred_id = f"{prediction_data['race_id']}_{prediction_data['engine_name']}_{horizon}"
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO predictions (
                prediction_id, race_id, engine_name, horizon, created_at,
                lock_time, selection_json, bases_json, outsider_num,
                probabilities_json, metadata_json,
                odds_real, priced_ratio, lock_time_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                pred_id,
                prediction_data["race_id"],
                prediction_data["engine_name"],
                horizon,
                prediction_data.get("created_at", datetime.utcnow().isoformat()),
                prediction_data.get("lock_time", datetime.utcnow().isoformat()),
                json.dumps(prediction_data.get("selection", [])),
                json.dumps(prediction_data.get("bases", [])),
                prediction_data.get("outsider_num"),
                json.dumps(prediction_data.get("probabilities", {})),
                json.dumps(prediction_data.get("metadata", {})),
                (None if prediction_data.get("odds_real") is None else int(bool(prediction_data.get("odds_real")))),
                prediction_data.get("priced_ratio"),
                prediction_data.get("lock_time_utc")
            ))

    # ------------------------------------------------------------------
    # Transparence éditoriale (« aucun chiffre retouché »)
    # ------------------------------------------------------------------
    FICTITIOUS_RACE_IDS = (
        [f"R9C{i}_29082026_STGALMIER" for i in range(1, 9)] +
        [f"R8C{i}_29082026_CAVAILLON" for i in range(1, 9)] +
        [f"R1C{i}_29082026_VINC" for i in range(1, 10)] +
        [f"R1C{i}_28082026_CAB" for i in range(1, 9)]
    )

    def purge_fictitious_data(self) -> Dict[str, int]:
        """Purge définitive des données non réelles :
        1) les 33 courses de référence injectées à la main (chevaux, cotes et
           rapports fictifs) — supprimées de toutes les tables ;
        2) les dividendes ESTIMÉS par l'ancienne formule (SG = cote x 0.85,
           SP = 1 + (cote-1) x 0.28) — supprimés ; la course reste dans
           l'historique et les taux de réussite, mais sort du calcul de ROI
           tant qu'aucun dividende officiel n'est disponible.
        Idempotent : ne fait rien si tout est déjà propre."""
        removed = {"fictitious_races": 0, "estimated_rapports": 0}
        with self.transaction() as conn:
            cursor = conn.cursor()

            # 1) Courses fictives — suppression CHIRURGICALE : certaines vraies
            # courses PMU (ex: Cavaillon 29/08) portent le même identifiant que
            # des courses injectées. On ne supprime que si la course porte la
            # signature des données fabriquées (partants "J.DRIVER"/"T.TRAINER") ;
            # une vraie course ré-importée du flux PMU n'est JAMAIS touchée.
            for race_id in self.FICTITIOUS_RACE_IDS:
                cursor.execute("SELECT 1 FROM races WHERE race_id = ?", (race_id,))
                if not cursor.fetchone():
                    continue
                cursor.execute(
                    "SELECT 1 FROM runners WHERE race_id = ? AND (driver_jockey = 'J.DRIVER' OR trainer = 'T.TRAINER') LIMIT 1",
                    (race_id,)
                )
                if not cursor.fetchone():
                    continue  # données réelles : on préserve
                for table in ("odds_snapshots", "rapports", "race_results", "predictions", "runners", "races"):
                    cursor.execute(f"DELETE FROM {table} WHERE race_id = ?", (race_id,))
                removed["fictitious_races"] += 1

            # 2) Dividendes estimés (signature exacte de l'ancienne formule)
            cursor.execute("""
                SELECT rr.race_id, rr.arrival_order_json FROM race_results rr
                WHERE EXISTS (SELECT 1 FROM rapports rp WHERE rp.race_id = rr.race_id)
            """)
            for row in cursor.fetchall():
                race_id = row["race_id"]
                try:
                    arrival = json.loads(row["arrival_order_json"])
                except Exception:
                    continue
                if not arrival:
                    continue
                winner = arrival[0]
                cursor.execute("SELECT final_odds FROM runners WHERE race_id = ? AND num = ?", (race_id, winner))
                w_row = cursor.fetchone()
                if not w_row or w_row["final_odds"] is None:
                    continue
                w_odds = float(w_row["final_odds"])

                cursor.execute("SELECT bet_type, combination, dividend FROM rapports WHERE race_id = ?", (race_id,))
                raps = cursor.fetchall()
                # L'estimation produisait exactement 1 SG + 3 SP, rien d'autre
                types = [r["bet_type"] for r in raps]
                if sorted(set(types)) != ["SIMPLE_GAGNANT", "SIMPLE_PLACE"] or len(raps) != 4:
                    continue
                sg = next((r for r in raps if r["bet_type"] == "SIMPLE_GAGNANT"), None)
                sp_w = next((r for r in raps if r["bet_type"] == "SIMPLE_PLACE" and str(r["combination"]) == str(winner)), None)
                if not sg or not sp_w or str(sg["combination"]) != str(winner):
                    continue
                est_sg = max(1.10, round(w_odds * 0.85, 2))
                est_sp = max(1.10, round(1.0 + (w_odds - 1.0) * 0.28, 2))
                if abs(float(sg["dividend"]) - est_sg) <= 0.011 and abs(float(sp_w["dividend"]) - est_sp) <= 0.011:
                    cursor.execute("DELETE FROM rapports WHERE race_id = ?", (race_id,))
                    removed["estimated_rapports"] += 1

        return removed

    def has_rapports(self, race_id: str) -> bool:
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT 1 FROM rapports WHERE race_id = ? LIMIT 1", (race_id,))
            return cursor.fetchone() is not None

    def save_rapports(self, race_id: str, rapports: List[Dict[str, Any]]):
        """Enregistre des dividendes OFFICIELS pour une course (sans toucher au reste)."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            for rap in rapports:
                rap_id = f"{race_id}_{rap['bet_type']}_{rap['combination']}"
                cursor.execute("""
                INSERT OR IGNORE INTO rapports (rapport_id, race_id, bet_type, combination, dividend)
                VALUES (?, ?, ?, ?, ?)
                """, (rap_id, race_id, rap["bet_type"], str(rap["combination"]), float(rap["dividend"])))

    def has_prediction(self, race_id: str, engine_name: str, horizon: str) -> bool:
        """True if a prediction is already locked for this (race, engine, horizon).
        Used to guarantee that a locked horizon is NEVER overwritten by later syncs."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM predictions WHERE race_id = ? AND engine_name = ? AND horizon = ? LIMIT 1",
                (race_id, engine_name, horizon)
            )
            return cursor.fetchone() is not None

    def save_odds_snapshots(self, race_id: str, horizon: str, odds_map: Dict[int, float]):
        """Persist the odds of every runner at a given horizon (T_MATIN/T90/T30/T15).
        INSERT OR IGNORE: the first captured snapshot for a horizon is immutable."""
        now_iso = datetime.utcnow().isoformat()
        with self.transaction() as conn:
            cursor = conn.cursor()
            for num, odds in odds_map.items():
                cursor.execute("""
                INSERT OR IGNORE INTO odds_snapshots (race_id, num, horizon, odds, captured_at)
                VALUES (?, ?, ?, ?, ?)
                """, (race_id, int(num), horizon, float(odds) if odds is not None else None, now_iso))

    def get_odds_snapshots(self, race_id: str) -> Dict[int, Dict[str, float]]:
        """Return {num: {horizon: odds}} for a race."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT num, horizon, odds FROM odds_snapshots WHERE race_id = ?", (race_id,))
            out: Dict[int, Dict[str, float]] = {}
            for row in cursor.fetchall():
                out.setdefault(row["num"], {})[row["horizon"]] = row["odds"]
            return out

    def get_locked_horizons(self, race_id: str, engine_name: str = "NEW_VALUE_ENGINE") -> List[str]:
        """List of horizons already locked for a race (for the given engine)."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT DISTINCT horizon FROM predictions WHERE race_id = ? AND engine_name = ?",
                (race_id, engine_name)
            )
            return [row["horizon"] for row in cursor.fetchall()]

    # ──────────────────────────────────────────────────────────────────
    # Résultats versionnés (correctif partenaire, points 2-3-4)
    # ──────────────────────────────────────────────────────────────────
    #
    # `race_results` porte TOUJOURS la version courante (une ligne par course) ;
    # `race_results_history` conserve chaque version (append-only). Le banc,
    # les stats humaines et le site ne jugent que les lignes DEFINITIVE.
    #
    # Raisons de version :
    #   INITIAL                    première lecture (provisoire ou définitive)
    #   PROVISOIRE_VERS_DEFINITIVE le flux confirme, classement identique/complété
    #   COMPLETION                 rangs supplémentaires publiés (aucun rang modifié)
    #   CORRECTION_CLASSEMENT      un rang déjà publié change (réclamation, déclassement…)
    #   ANNULATION                 course annulée après publication d'un résultat
    #   MIGRATION_LEGACY           ligne antérieure au versionnage

    def get_result(self, race_id: str) -> Optional[Dict[str, Any]]:
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM race_results WHERE race_id = ?", (race_id,))
            row = cursor.fetchone()
        return self._result_row_to_dict(row) if row else None

    @staticmethod
    def _result_row_to_dict(row) -> Dict[str, Any]:
        d = dict(row)

        def _load(key, default):
            try:
                return json.loads(d.get(key) or "") if d.get(key) else default
            except Exception:
                return default

        d["arrival_order"] = _load("arrival_order_json", [])
        d["disqualified"] = _load("disqualified_json", [])
        d["ranking"] = _load("ranking_json", [{"rang": i + 1, "num": n, "dead_heat": False}
                                              for i, n in enumerate(d["arrival_order"])])
        d["incidents"] = _load("incidents_json", [])
        d["non_partants"] = _load("non_partants_json", [])
        d["statut"] = d.get("statut") or "DEFINITIVE"
        d["version"] = int(d.get("version") or 1)
        d["nb_corrections"] = int(d.get("nb_corrections") or 0)
        d["source"] = d.get("source") or "PMU_LEGACY"
        return d

    def get_result_history(self, race_id: str) -> List[Dict[str, Any]]:
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM race_results_history WHERE race_id = ? ORDER BY version ASC", (race_id,))
            rows = cursor.fetchall()
        out = []
        for row in rows:
            d = dict(row)
            for key, dst in (("ranking_json", "ranking"), ("incidents_json", "incidents"), ("non_partants_json", "non_partants")):
                try:
                    d[dst] = json.loads(d.get(key) or "[]")
                except Exception:
                    d[dst] = []
            out.append(d)
        return out

    def record_result(self, race_id: str, reading, source_url: Optional[str] = None,
                      rapports: Optional[List[Dict[str, Any]]] = None,
                      now_utc: Optional[datetime] = None) -> Dict[str, Any]:
        """Enregistre une lecture d'arrivée (``results_reader.ArrivalReading``)
        avec versionnage. Retourne ``{"action", "version", "reason", "statut"}``.

        - Lecture invalide ou sans classement : rien n'est écrit (``REJECTED``).
        - Première lecture : version 1 (``INITIAL``).
        - Lecture identique : seul ``last_checked_at`` avance (``UNCHANGED``).
        - Régression (moins d'information qu'en base, source périmée) : ignorée.
        - Complément / passage en définitive / correction : nouvelle version,
          ancienne version conservée dans l'historique.
        La course passe en ``FINISHED`` (archive gelée) uniquement sur une
        arrivée DEFINITIVE ; une arrivée provisoire la met en ``ARRIVEE_PROVISOIRE``."""
        from turf_lab.results_reader import (
            CMP_COMPLETION, CMP_CORRECTION, CMP_IDENTIQUE, CMP_REGRESSION,
            STATUT_ANNULEE, STATUT_DEFINITIVE, STATUT_PROVISOIRE, compare_rankings,
        )
        now = (now_utc or datetime.utcnow()).isoformat()
        current = self.get_result(race_id)

        # ── Annulation ───────────────────────────────────────────────
        if reading.statut == STATUT_ANNULEE:
            with self.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("UPDATE races SET status = 'ANNULEE', pmu_statut = ? WHERE race_id = ?",
                               (reading.pmu_statut or None, race_id))
                if current and current["statut"] != STATUT_ANNULEE:
                    version = current["version"] + 1
                    cursor.execute("""UPDATE race_results SET statut = 'ANNULEE', version = ?, pmu_statut = ?,
                                      updated_at = ?, last_checked_at = ?, source = ?, source_url = COALESCE(?, source_url)
                                      WHERE race_id = ?""",
                                   (version, reading.pmu_statut, now, now, reading.source or current["source"], source_url, race_id))
                    cursor.execute("""INSERT INTO race_results_history (race_id, version, statut, pmu_statut, ranking_json,
                                      incidents_json, non_partants_json, source, reason, recorded_at)
                                      VALUES (?, ?, 'ANNULEE', ?, ?, ?, ?, ?, 'ANNULATION', ?)""",
                                   (race_id, version, reading.pmu_statut, json.dumps(current["ranking"]),
                                    json.dumps(current["incidents"]), json.dumps(current["non_partants"]),
                                    reading.source or current["source"], now))
                    return {"action": "ANNULATION", "version": version, "reason": "ANNULATION", "statut": STATUT_ANNULEE}
            return {"action": "ANNULEE", "version": current["version"] if current else 0, "reason": None, "statut": STATUT_ANNULEE}

        if reading.statut not in (STATUT_PROVISOIRE, STATUT_DEFINITIVE) or not reading.ranking or reading.errors:
            return {"action": "REJECTED", "version": current["version"] if current else 0,
                    "reason": ",".join(reading.errors) or "SANS_CLASSEMENT", "statut": reading.statut}

        ranking_json = json.dumps(reading.ranking)
        incidents_json = json.dumps(reading.incidents)
        np_json = json.dumps(reading.non_partants)
        flat_json = json.dumps(reading.flat_arrival())
        dq_json = json.dumps(reading.disqualified())
        race_status = "FINISHED" if reading.statut == STATUT_DEFINITIVE else "ARRIVEE_PROVISOIRE"

        with self.transaction() as conn:
            cursor = conn.cursor()
            if current is None:
                cursor.execute("""
                INSERT INTO race_results (race_id, arrival_order_json, disqualified_json, recorded_at,
                    statut, version, nb_corrections, source, source_url, pmu_statut, ranking_json, incidents_json,
                    non_partants_json, first_seen_at, definitive_at, updated_at, last_checked_at)
                VALUES (?, ?, ?, ?, ?, 1, 0, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (race_id, flat_json, dq_json, now, reading.statut, reading.source, source_url, reading.pmu_statut,
                      ranking_json, incidents_json, np_json, now,
                      now if reading.statut == STATUT_DEFINITIVE else None, now, now))
                cursor.execute("""INSERT INTO race_results_history (race_id, version, statut, pmu_statut, ranking_json,
                                  incidents_json, non_partants_json, source, reason, recorded_at)
                                  VALUES (?, 1, ?, ?, ?, ?, ?, ?, 'INITIAL', ?)""",
                               (race_id, reading.statut, reading.pmu_statut, ranking_json, incidents_json, np_json,
                                reading.source, now))
                cursor.execute("UPDATE races SET status = ?, pmu_statut = ? WHERE race_id = ?",
                               (race_status, reading.pmu_statut or None, race_id))
                self._save_rapports(cursor, race_id, rapports)
                return {"action": "INITIAL", "version": 1, "reason": "INITIAL", "statut": reading.statut}

            # Une arrivée DEFINITIVE ne redevient jamais PROVISOIRE.
            if current["statut"] == STATUT_DEFINITIVE and reading.statut == STATUT_PROVISOIRE:
                cmp_only = compare_rankings(current["ranking"], reading.ranking, current["incidents"], reading.incidents)
                if cmp_only in (CMP_IDENTIQUE, CMP_REGRESSION):
                    cursor.execute("UPDATE race_results SET last_checked_at = ? WHERE race_id = ?", (now, race_id))
                    self._save_rapports(cursor, race_id, rapports)
                    return {"action": "UNCHANGED", "version": current["version"], "reason": None, "statut": current["statut"]}
                # Classement différent mais source non définitive : on ne touche
                # pas à la version définitive, on signale seulement.
                cursor.execute("UPDATE race_results SET last_checked_at = ? WHERE race_id = ?", (now, race_id))
                return {"action": "DIVERGENCE_PROVISOIRE", "version": current["version"], "reason": cmp_only, "statut": current["statut"]}

            cmp = compare_rankings(current["ranking"], reading.ranking, current["incidents"], reading.incidents)
            statut_up = current["statut"] == STATUT_PROVISOIRE and reading.statut == STATUT_DEFINITIVE

            if cmp == CMP_REGRESSION and not statut_up:
                cursor.execute("UPDATE race_results SET last_checked_at = ? WHERE race_id = ?", (now, race_id))
                return {"action": "IGNORED_REGRESSION", "version": current["version"], "reason": cmp, "statut": current["statut"]}
            if cmp == CMP_IDENTIQUE and not statut_up:
                cursor.execute("UPDATE race_results SET last_checked_at = ? WHERE race_id = ?", (now, race_id))
                self._save_rapports(cursor, race_id, rapports)
                return {"action": "UNCHANGED", "version": current["version"], "reason": None, "statut": current["statut"]}

            if cmp == CMP_REGRESSION and statut_up:
                # Le flux confirme « définitive » sur une source moins complète :
                # on garde le classement le plus riche déjà en base.
                ranking_json = json.dumps(current["ranking"])
                incidents_json = json.dumps(current["incidents"])
                np_json = json.dumps(current["non_partants"])
                flat_json = json.dumps(current["arrival_order"])
                dq_json = json.dumps(current["disqualified"])
                cmp = CMP_IDENTIQUE

            if cmp == CMP_CORRECTION:
                reason = "CORRECTION_CLASSEMENT"
            elif cmp == CMP_COMPLETION:
                reason = "PROVISOIRE_VERS_DEFINITIVE" if statut_up else "COMPLETION"
            else:
                reason = "PROVISOIRE_VERS_DEFINITIVE"
            version = current["version"] + 1
            nb_corr = current["nb_corrections"] + (1 if cmp == CMP_CORRECTION else 0)
            new_statut = STATUT_DEFINITIVE if (statut_up or current["statut"] == STATUT_DEFINITIVE) else reading.statut
            definitive_at = current.get("definitive_at") or (now if new_statut == STATUT_DEFINITIVE else None)

            cursor.execute("""
            UPDATE race_results SET arrival_order_json = ?, disqualified_json = ?, statut = ?, version = ?,
                nb_corrections = ?, source = ?, source_url = COALESCE(?, source_url), pmu_statut = ?, ranking_json = ?,
                incidents_json = ?, non_partants_json = ?, definitive_at = ?, updated_at = ?, last_checked_at = ?
            WHERE race_id = ?
            """, (flat_json, dq_json, new_statut, version, nb_corr, reading.source, source_url, reading.pmu_statut,
                  ranking_json, incidents_json, np_json, definitive_at, now, now, race_id))
            cursor.execute("""INSERT INTO race_results_history (race_id, version, statut, pmu_statut, ranking_json,
                              incidents_json, non_partants_json, source, reason, recorded_at)
                              VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                           (race_id, version, new_statut, reading.pmu_statut, ranking_json, incidents_json, np_json,
                            reading.source, reason, now))
            cursor.execute("UPDATE races SET status = ?, pmu_statut = ? WHERE race_id = ?",
                           ("FINISHED" if new_statut == STATUT_DEFINITIVE else "ARRIVEE_PROVISOIRE",
                            reading.pmu_statut or None, race_id))
            self._save_rapports(cursor, race_id, rapports)
            return {"action": "VERSION", "version": version, "reason": reason, "statut": new_statut}

    @staticmethod
    def _save_rapports(cursor, race_id: str, rapports: Optional[List[Dict[str, Any]]]):
        if not rapports:
            return
        for rap in rapports:
            rap_id = f"{race_id}_{rap['bet_type']}_{rap['combination']}"
            cursor.execute("""
            INSERT OR REPLACE INTO rapports (
                rapport_id, race_id, bet_type, combination, dividend
            ) VALUES (?, ?, ?, ?, ?)
            """, (rap_id, race_id, rap["bet_type"], str(rap["combination"]), float(rap["dividend"])))

    def save_results(self, race_id: str, arrival_order: List[int], disqualified: Optional[List[int]] = None,
                     rapports: Optional[List[Dict[str, Any]]] = None, source: str = "PMU_LEGACY"):
        """Compatibilité (simulateur, archives) : enregistre un classement plat
        comme arrivée DEFINITIVE via le versionnage."""
        from turf_lab.results_reader import ArrivalReading, STATUT_DEFINITIVE, ranking_from_groups
        reading = ArrivalReading(statut=STATUT_DEFINITIVE, definitive_flag=True, source=source)
        reading.ranking = ranking_from_groups([[int(n)] for n in arrival_order])
        reading.incidents = [{"num": int(n), "type": "DISQUALIFIE"} for n in (disqualified or [])]
        return self.record_result(race_id, reading, rapports=rapports)

    def get_results_for_date(self, date_db: str) -> List[Dict[str, Any]]:
        """Toutes les courses d'une date avec leur résultat courant (ou None)."""
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM races WHERE date = ? ORDER BY meeting_number, race_number", (date_db,))
            races = [dict(r) for r in cursor.fetchall()]
            out = []
            for race in races:
                cursor.execute("SELECT * FROM race_results WHERE race_id = ?", (race["race_id"],))
                row = cursor.fetchone()
                race["result"] = self._result_row_to_dict(row) if row else None
                out.append(race)
        return out

    def get_race(self, race_id: str) -> Optional[Dict[str, Any]]:
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM races WHERE race_id = ?", (race_id,))
            row = cursor.fetchone()
            return dict(row) if row else None

    def get_runners(self, race_id: str) -> List[Dict[str, Any]]:
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM runners WHERE race_id = ? ORDER BY num ASC", (race_id,))
            return [dict(r) for r in cursor.fetchall()]

    def get_predictions(self, race_id: str, horizon: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM predictions WHERE race_id = ?", (race_id,))
            rows = cursor.fetchall()

            preds = []
            for row in rows:
                p = dict(row)
                p["selection"] = json.loads(p["selection_json"])
                p["bases"] = json.loads(p["bases_json"]) if p["bases_json"] else []
                p["probabilities"] = json.loads(p["probabilities_json"]) if p["probabilities_json"] else {}
                p["metadata"] = json.loads(p["metadata_json"]) if p["metadata_json"] else {}
                preds.append(p)
            return preds

    def get_finished_races(self) -> List[str]:
        with self.transaction() as conn:
            cursor = conn.cursor()
            # Seules les arrivées DEFINITIVES sont jugées (banc, stats, site).
            cursor.execute("SELECT race_id FROM race_results WHERE COALESCE(statut, 'DEFINITIVE') = 'DEFINITIVE' ORDER BY recorded_at ASC")
            return [row["race_id"] for row in cursor.fetchall()]

    def get_race_evaluation_data(self, race_id: str) -> Optional[Dict[str, Any]]:
        race = self.get_race(race_id)
        if not race:
            return None
        runners = self.get_runners(race_id)
        predictions = self.get_predictions(race_id)
        
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM race_results WHERE race_id = ? AND COALESCE(statut, 'DEFINITIVE') = 'DEFINITIVE'", (race_id,))
            res_row = cursor.fetchone()
            if not res_row:
                return None
            results = self._result_row_to_dict(res_row)

            cursor.execute("SELECT * FROM rapports WHERE race_id = ?", (race_id,))
            rapports = [dict(r) for r in cursor.fetchall()]

        return {
            "race": race,
            "runners": runners,
            "predictions": predictions,
            "results": results,
            "rapports": rapports
        }
