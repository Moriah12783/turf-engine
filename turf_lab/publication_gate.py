"""Porte de diffusion UNIQUE — can_publish(db, race_id, horizon) -> (bool, reason).

Principe (Axe 3 du Plan Value Radar) :
    Aucune sélection n'est diffusée sur une édition dont les cotes ne sont
    pas réelles. Le silence vaut mieux qu'une édition fausse.

Tout script / route / automate qui produit un message abonné (WhatsApp,
Telegram, n8n, site) DOIT passer par can_publish. Refus => aucun message,
et un log `PUBLICATION_REFUSED {...}` avec la raison.

Raisons possibles (stables, à ne pas renommer — utilisées par n8n/tests) :
    RACE_UNKNOWN       course inconnue en base
    RACE_STARTED       course partie (ou terminée) — jamais de diffusion rétroactive
    BEFORE_0630        avant 06:30 GMT le jour de la course (plancher), ou verrou posé avant
    ODDS_DEFAULT       aucune cote réelle (édition posée avant l'ouverture du marché)
    PRICED_RATIO_LOW   moins de 90 % des partants actifs cotés
    NOT_LOCKED         l'horizon demandé n'est pas verrouillé pour cette course
    OK                 diffusable

Usage CLI (pour n8n / scripts) :
    python -m turf_lab.publication_gate RACE_ID [HORIZON] [--db turf_bench.db]
    python -m turf_lab.publication_gate --date 2026-09-07 [--horizon T_MATIN]
Code retour 0 si diffusable (pour un RACE_ID), 1 sinon. Sortie JSON.
"""

import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from turf_lab.database import TurfDatabase
from turf_lab.odds_quality import MIN_PRICED_RATIO, priced_ratio

ENGINE_NAME = "NEW_VALUE_ENGINE"
MORNING_FLOOR_HOUR = 6
MORNING_FLOOR_MINUTE = 30

REASON_LABELS = {
    "RACE_UNKNOWN": "course inconnue",
    "RACE_STARTED": "course partie — aucune diffusion rétroactive",
    "BEFORE_0630": "avant 06h30 GMT le jour de la course",
    "ODDS_DEFAULT": "cotes non ouvertes (valeurs par défaut)",
    "PRICED_RATIO_LOW": "moins de 90 % des partants cotés",
    "NOT_LOCKED": "horizon non verrouillé",
    "OK": "diffusable",
}


def _floor_datetime(race_date_db: str) -> Optional[datetime]:
    try:
        d = datetime.strptime(race_date_db, "%Y-%m-%d")
    except Exception:
        return None
    return d.replace(hour=MORNING_FLOOR_HOUR, minute=MORNING_FLOOR_MINUTE, second=0, microsecond=0)


def _parse_iso(value: Any) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", ""))
    except Exception:
        return None


def _minutes_to_start(race: Dict[str, Any], now_utc: datetime) -> Optional[float]:
    # Import local : évite tout cycle (daily_sync n'importe jamais ce module).
    from turf_lab.daily_sync import DailySyncManager
    return DailySyncManager.minutes_to_start(
        race.get("scheduled_start_time", ""), race.get("date", ""), now_utc
    )


def _log_refusal(race_id: str, horizon: str, reason: str, now_utc: datetime, extra: Optional[Dict[str, Any]] = None) -> None:
    payload = {"race_id": race_id, "horizon": horizon, "reason": reason, "now_utc": now_utc.isoformat()}
    if extra:
        payload.update(extra)
    print("PUBLICATION_REFUSED " + json.dumps(payload, ensure_ascii=False))


def can_publish(db: TurfDatabase, race_id: str, horizon: str = "T_MATIN",
                now_utc: Optional[datetime] = None, log: bool = True) -> Tuple[bool, str]:
    """Décide si la sélection (moteur NEW_VALUE_ENGINE, horizon donné) d'une
    course peut être diffusée MAINTENANT. Exige :
      - course connue et non partie ;
      - now ≥ 06:30 GMT le jour de la course (plancher) ;
      - cotes réelles sur ≥ 90 % des partants actifs (état courant en base) ;
      - horizon verrouillé, avec odds_real = true, priced_ratio ≥ 0.90 et
        lock_time_utc ≥ 06:30 GMT (état persisté au verrou)."""
    if now_utc is None:
        now_utc = datetime.utcnow()

    def refuse(reason: str, **extra: Any) -> Tuple[bool, str]:
        if log:
            _log_refusal(race_id, horizon, reason, now_utc, extra or None)
        return False, reason

    race = db.get_race(race_id)
    if not race:
        return refuse("RACE_UNKNOWN")

    if str(race.get("status", "")).upper() in ("FINISHED", "ARRIVEE_PROVISOIRE"):
        return refuse("RACE_STARTED")
    if str(race.get("status", "")).upper() == "ANNULEE":
        return refuse("RACE_CANCELLED")
    mins = _minutes_to_start(race, now_utc)
    if mins is not None and mins <= 0:
        return refuse("RACE_STARTED", minutes_to_start=round(mins, 1))

    floor_dt = _floor_datetime(str(race.get("date", "")))
    if floor_dt is not None and now_utc < floor_dt:
        return refuse("BEFORE_0630")

    runners = db.get_runners(race_id)
    ratio_now = priced_ratio(runners)
    if ratio_now <= 0.0:
        return refuse("ODDS_DEFAULT", priced_ratio=0.0)
    if ratio_now < MIN_PRICED_RATIO:
        return refuse("PRICED_RATIO_LOW", priced_ratio=round(ratio_now, 3))

    preds = [p for p in db.get_predictions(race_id)
             if p.get("engine_name") == ENGINE_NAME and p.get("horizon") == horizon]
    if not preds:
        return refuse("NOT_LOCKED")
    pred = preds[0]

    odds_real = pred.get("odds_real")
    if odds_real is not None and not bool(odds_real):
        return refuse("ODDS_DEFAULT", priced_ratio=pred.get("priced_ratio"))
    locked_ratio = pred.get("priced_ratio")
    if locked_ratio is not None and float(locked_ratio) < MIN_PRICED_RATIO:
        return refuse("PRICED_RATIO_LOW", priced_ratio=round(float(locked_ratio), 3))

    lock_dt = _parse_iso(pred.get("lock_time_utc")) or _parse_iso(pred.get("lock_time"))
    if floor_dt is not None and lock_dt is not None and lock_dt < floor_dt:
        return refuse("BEFORE_0630", lock_time_utc=lock_dt.isoformat())

    return True, "OK"


def decisions_for_date(db: TurfDatabase, date_db: str, horizon: str = "T_MATIN",
                       now_utc: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Décision de diffusion pour toutes les courses d'une date (pour n8n)."""
    with db.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT race_id FROM races WHERE date = ? ORDER BY meeting_number, race_number", (date_db,))
        race_ids = [row["race_id"] for row in cursor.fetchall()]
    out = []
    for rid in race_ids:
        ok, reason = can_publish(db, rid, horizon, now_utc=now_utc, log=False)
        out.append({"race_id": rid, "horizon": horizon, "publishable": ok, "reason": reason,
                    "label": REASON_LABELS.get(reason, reason)})
    return out


def main(argv: Optional[List[str]] = None) -> int:
    script_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    parser = argparse.ArgumentParser(description="Porte de diffusion — verrou de fraîcheur")
    parser.add_argument("race_id", nargs="?", help="Identifiant de course (ex. R1C1_07092026_CRAON)")
    parser.add_argument("horizon_pos", nargs="?", default=None, help="Horizon (T_MATIN, T90, T30, T15)")
    parser.add_argument("--horizon", default=None)
    parser.add_argument("--date", default=None, help="Toutes les courses de la date (YYYY-MM-DD)")
    parser.add_argument("--db", default=os.path.join(script_dir, "turf_bench.db"))
    args = parser.parse_args(argv)

    horizon = args.horizon or args.horizon_pos or "T_MATIN"
    db = TurfDatabase(args.db)

    if args.date:
        rows = decisions_for_date(db, args.date, horizon)
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    if not args.race_id:
        parser.error("race_id ou --date requis")
    ok, reason = can_publish(db, args.race_id, horizon)
    print(json.dumps({"race_id": args.race_id, "horizon": horizon, "publishable": ok,
                      "reason": reason, "label": REASON_LABELS.get(reason, reason)}, ensure_ascii=False))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
