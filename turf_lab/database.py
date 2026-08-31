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

    def save_race(self, race_data: Dict[str, Any]):
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO races (
                race_id, date, meeting_number, race_number, name,
                hippodrome, discipline, distance, track_type,
                track_condition, rope, autostart, scheduled_start_time, status
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                race_data.get("status", "SCHEDULED")
            ))

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
                probabilities_json, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                json.dumps(prediction_data.get("metadata", {}))
            ))

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

    def save_results(self, race_id: str, arrival_order: List[int], disqualified: Optional[List[int]] = None, rapports: Optional[List[Dict[str, Any]]] = None):
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR REPLACE INTO race_results (
                race_id, arrival_order_json, disqualified_json, recorded_at
            ) VALUES (?, ?, ?, ?)
            """, (
                race_id,
                json.dumps(arrival_order),
                json.dumps(disqualified or []),
                datetime.utcnow().isoformat()
            ))

            cursor.execute("UPDATE races SET status = 'FINISHED' WHERE race_id = ?", (race_id,))

            if rapports:
                for rap in rapports:
                    rap_id = f"{race_id}_{rap['bet_type']}_{rap['combination']}"
                    cursor.execute("""
                    INSERT OR REPLACE INTO rapports (
                        rapport_id, race_id, bet_type, combination, dividend
                    ) VALUES (?, ?, ?, ?, ?)
                    """, (
                        rap_id,
                        race_id,
                        rap["bet_type"],
                        str(rap["combination"]),
                        float(rap["dividend"])
                    ))

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
            cursor.execute("SELECT race_id FROM race_results ORDER BY recorded_at ASC")
            return [row["race_id"] for row in cursor.fetchall()]

    def get_race_evaluation_data(self, race_id: str) -> Optional[Dict[str, Any]]:
        race = self.get_race(race_id)
        if not race:
            return None
        runners = self.get_runners(race_id)
        predictions = self.get_predictions(race_id)
        
        with self.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM race_results WHERE race_id = ?", (race_id,))
            res_row = cursor.fetchone()
            if not res_row:
                return None
            results = {
                "arrival_order": json.loads(res_row["arrival_order_json"]),
                "disqualified": json.loads(res_row["disqualified_json"]) if res_row["disqualified_json"] else [],
                "recorded_at": res_row["recorded_at"]
            }

            cursor.execute("SELECT * FROM rapports WHERE race_id = ?", (race_id,))
            rapports = [dict(r) for r in cursor.fetchall()]

        return {
            "race": race,
            "runners": runners,
            "predictions": predictions,
            "results": results,
            "rapports": rapports
        }
