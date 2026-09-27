"""Export de l'historique Radar (Supabase) vers R2 — phase 1 du plan Benter.

Source : base « Radar Elite Predictive », rôle ``lecteur_benter`` (lecture
seule, 6 tables, 2 connexions, 120 s par requête, transaction inactive
fermée après 60 s : d'où l'autocommit), chaîne de connexion dans
le secret ``RADAR_HISTORY_DSN``. Cible : ``history/turf_history.db`` sur R2,
distinct de la base en direct (``turf_bench.db``) — la production n'est
jamais touchée.

Règles convenues avec le dev Radar (27-28/09/2026) :
  - pagination PAR DATE (jamais de requête unique sur 186 000 lignes) ;
  - créneau 00h00–05h00 UTC, arrêt STRICT : aucune requête après 04h55
    (``--force-hors-creneau`` pour un run manuel) ;
  - lecture seule, aucune écriture côté Radar.

Garanties d'écriture données par le Radar (28/09/2026) :
  - chaque date de la reprise des rapports est écrite en une seule fois :
    une date est visible complète ou pas du tout ;
  - ``rapports_definitifs`` ne fait que s'enrichir (arrivée le lendemain à
    74 %, jusqu'à 17 jours en reprise) ; les 5 autres tables ne sont écrites
    que le jour même de la course ;
  - tout changement de ce mode d'écriture est annoncé par ligne datée.

Le fichier local est un MIROIR PAR DATE : chaque date exportée est
remplacée en entier (idempotent), et consignée dans ``export_log`` avec son
nombre de lignes. Chaque nuit, une date est (ré)exportée si elle est
nouvelle, si son nombre de lignes a changé côté Radar (reprise, correction)
ou si elle fait partie des 3 derniers jours (données encore mouvantes).

La reprise des rapports arrive donc toute seule, date par date : une date
absente n'est pas lue ; dès qu'elle est écrite (complète), le comptage la
détecte et la nuit suivante la lit ; un ajout tardif change le comptage et
la fait relire. Aucun horodatage (``captured_at``) n'est utilisé.

Mises en garde de données (voir docs/HISTORIQUE_RADAR.md) :
  - ``participants.cote_reference`` est réécrite au fil de la journée :
    jamais une cote à un instant donné (prendre ``cotes_snapshots``) ;
  - statistiques humaines incomplètes avant mi-octobre 2025 (chauffe) ;
  - Radar comme source de combinaison : seulement après le 30/04/2026.

Usage :
    python -m turf_lab.history_export probe
    python -m turf_lab.history_export run [--start AAAA-MM-JJ] [--end AAAA-MM-JJ]
           [--tables t1,t2] [--refresh-days 3] [--max-minutes 45] [--force-hors-creneau]
    python -m turf_lab.history_export status
    python -m turf_lab.history_export fetch [--db chemin]   # lecture : miroir vérifié, sans accès Radar
"""

import argparse
import json
import os
import sqlite3
import sys
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Dict, List, Optional, Sequence, Tuple

from turf_lab.r2_store import (R2ConfigError, _head, _is_not_found, make_client, r2_config,
                               sha256_file)

ENV_DSN = "RADAR_HISTORY_DSN"
HISTORY_KEY = "history/turf_history.db"
HISTORY_PREV_KEY = "history/turf_history_prev.db"
DEFAULT_DB = "turf_history.db"
FIRST_DATE = "2025-07-17"

# Colonnes EXPLICITES : une colonne disparue côté Radar fait échouer
# l'export (visible) au lieu de produire un miroir silencieusement amputé.
TABLES: Dict[str, Tuple[str, ...]] = {
    "courses": (
        "id", "date_course", "num_reunion", "num_course", "hippodrome_code", "hippodrome_libelle",
        "discipline", "specialite", "distance", "parcours", "type_piste", "corde",
        "penetrometre_valeur", "penetrometre_intitule", "categorie", "conditions", "montant_prix",
        "nombre_partants", "heure_depart", "duree_course_ms", "statut", "captured_at",
        "est_quinte", "annulee"),
    "participants": (
        "id", "date_course", "num_reunion", "num_course", "num_pmu", "nom", "age", "sexe", "race",
        "statut", "incident", "place_corde", "oeilleres", "proprietaire", "entraineur", "driver",
        "driver_change", "musique", "nombre_courses", "nombre_victoires", "nombre_places",
        "gains_carriere", "gains_annee_en_cours", "nom_pere", "nom_mere", "handicap_poids",
        "cote_direct", "cote_reference", "commentaire", "allure", "ordre_arrivee", "captured_at"),
    "arrivees": (
        "id", "date_course", "num_reunion", "num_course", "ordre_arrivee", "incidents",
        "duree_course_ms", "captured_at"),
    "cotes_snapshots": (
        "id", "date_course", "num_reunion", "num_course", "num_pmu", "cote", "favoris",
        "minutes_avant_depart", "captured_at"),
    "rapports_definitifs": (
        "date_course", "num_reunion", "num_course", "type_pari", "libelle", "combinaison",
        "dividende_pour_1e", "nombre_gagnants", "rembourse", "captured_at"),
    # Journal des changements de cote (depuis le 15/09/2026) : la seule
    # source de la cote à un instant donné, clé de l'étape 2 du plan Benter.
    "participants_cotes_hist": (
        "id", "changed_at", "date_course", "num_reunion", "num_course", "num_pmu", "operation",
        "cote_reference_avant", "cote_reference_apres", "cote_direct_avant", "cote_direct_apres"),
}
ORDER_BY = {
    "rapports_definitifs": "num_reunion, num_course, type_pari, libelle, combinaison",
}
CRENEAU_UTC = (0, 5)            # [00h, 05h[
MARGE_FIN_CRENEAU = timedelta(minutes=5)   # dernière requête au plus tard à 04h55
MAX_DROP_RATIO = 0.01           # refus d'envoi si le miroir perd > 1 % de lignes


class HistoryGuardError(Exception):
    """Refus de sécurité : l'export ne doit pas continuer ou être envoyé."""


def _log(tag: str, payload: Dict[str, Any]) -> None:
    print(tag + " " + json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str))


# ── Conversion des valeurs Postgres -> SQLite ───────────────────────────
def to_sqlite(value: Any) -> Any:
    if isinstance(value, bool):          # avant int : bool en est une sous-classe
        return int(value)
    if value is None or isinstance(value, (int, float, str, bytes)):
        return value
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    return str(value)


# ── Source : Postgres Radar (rôle lecteur_benter) ───────────────────────
class PgSource:
    """Accès en lecture seule. psycopg n'est importé qu'ici : les tests
    utilisent une source factice."""

    def __init__(self, dsn: str):
        import psycopg
        # autocommit OBLIGATOIRE : le rôle ferme toute transaction inactive
        # plus de 60 s. prepare_threshold=None : aucune instruction préparée
        # côté serveur, compatible avec le pooler Supabase quel que soit son mode.
        self.conn = psycopg.connect(dsn, autocommit=True, connect_timeout=30,
                                    application_name="turf-engine-history")
        self.conn.prepare_threshold = None

    def query(self, sql: str, params: Sequence[Any] = ()) -> List[Tuple[Any, ...]]:
        with self.conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def whoami(self) -> Dict[str, Any]:
        user, count = self.query("select current_user, count(*) from courses")[0]
        return {"current_user": user, "courses": int(count)}

    def date_counts(self, table: str) -> Dict[str, int]:
        """Nombre de lignes par date (parcours d'index, aucune ligne lue)."""
        return {to_sqlite(d): int(n) for d, n in self.query(
            f"select date_course, count(*) from {table} group by 1 order by 1")}

    def fetch(self, table: str, day: str) -> List[Tuple[Any, ...]]:
        cols = ", ".join(TABLES[table])
        order = ORDER_BY.get(table, "id")
        rows = self.query(f"select {cols} from {table} where date_course = %s order by {order}", (day,))
        return [tuple(to_sqlite(v) for v in row) for row in rows]

    def close(self) -> None:
        self.conn.close()


# ── Miroir SQLite local ─────────────────────────────────────────────────
class HistoryDB:
    def __init__(self, path: str):
        self.path = path
        self.conn = sqlite3.connect(path)
        self.conn.execute("PRAGMA journal_mode=DELETE")
        for table, cols in TABLES.items():
            self.conn.execute(f"CREATE TABLE IF NOT EXISTS {table} ({', '.join(cols)})")
            self.conn.execute(f"CREATE INDEX IF NOT EXISTS idx_{table}_date ON {table}(date_course)")
        self.conn.execute("""CREATE TABLE IF NOT EXISTS export_log (
            tbl TEXT NOT NULL, date_course TEXT NOT NULL, nb_lignes INTEGER NOT NULL,
            exporte_le_utc TEXT NOT NULL, PRIMARY KEY (tbl, date_course))""")
        self.conn.commit()

    def exported(self, table: str) -> Dict[str, int]:
        """date -> nombre de lignes lors du dernier export."""
        return dict(self.conn.execute("SELECT date_course, nb_lignes FROM export_log WHERE tbl = ?", (table,)))

    def replace_date(self, table: str, day: str, rows: Sequence[Tuple[Any, ...]], now: str) -> None:
        cols = TABLES[table]
        with self.conn:
            self.conn.execute(f"DELETE FROM {table} WHERE date_course = ?", (day,))
            self.conn.executemany(
                f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", rows)
            self.conn.execute("INSERT OR REPLACE INTO export_log VALUES (?, ?, ?, ?)",
                              (table, day, len(rows), now))

    def counts(self) -> Dict[str, int]:
        return {t: int(self.conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]) for t in TABLES}

    def coverage(self) -> Dict[str, Dict[str, Any]]:
        out = {}
        for t in TABLES:
            mn, mx, nd = self.conn.execute(
                "SELECT MIN(date_course), MAX(date_course), COUNT(*) FROM export_log WHERE tbl = ?", (t,)).fetchone()
            out[t] = {"premiere_date": mn, "derniere_date": mx, "dates": nd}
        return out

    def close(self) -> None:
        self.conn.close()


# ── Planification ───────────────────────────────────────────────────────
def in_window(now: datetime) -> bool:
    return CRENEAU_UTC[0] <= now.hour < CRENEAU_UTC[1]


def plan_dates(source_counts: Dict[str, int], exported: Dict[str, int], today: str, refresh_days: int,
               start: Optional[str] = None, end: Optional[str] = None) -> List[str]:
    """Dates à (ré)exporter, les plus anciennes d'abord.

    Plage forcée (start/end) : toutes les dates de la plage. Sinon : dates
    jamais exportées, dates dont le nombre de lignes a changé côté source, et
    les ``refresh_days`` derniers jours (données encore mouvantes). La date du
    jour n'est jamais exportée (journée en cours)."""
    refresh_from = (date.fromisoformat(today) - timedelta(days=refresh_days)).isoformat()
    chosen = []
    for d in sorted(source_counts):
        if d >= today or (start and d < start) or (end and d > end):
            continue
        if start or end or exported.get(d) != source_counts[d] or d >= refresh_from:
            chosen.append(d)
    return chosen


def run_export(source, db: HistoryDB, tables: Sequence[str], today: str, refresh_days: int = 3,
               start: Optional[str] = None, end: Optional[str] = None, max_seconds: float = 45 * 60,
               clock=time.monotonic, now_utc=None) -> Dict[str, Any]:
    """Exporte date par date ; s'arrête proprement si le budget de temps est
    épuisé (la nuit suivante reprend là où l'export s'est arrêté)."""
    started = clock()
    stamp = now_utc or (lambda: datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    report = {"tables": {}, "interrompu": False}
    for table in tables:
        if clock() - started > max_seconds:          # aucune requête, même de comptage
            report["interrompu"] = True
            break
        todo = plan_dates(source.date_counts(table), db.exported(table), today, refresh_days, start, end)
        done = rows = 0
        for day in todo:
            if clock() - started > max_seconds:
                report["interrompu"] = True
                break
            batch = source.fetch(table, day)
            db.replace_date(table, day, batch, stamp())
            done += 1
            rows += len(batch)
        report["tables"][table] = {"dates_prevues": len(todo), "dates_exportees": done, "lignes": rows}
        _log("HISTORY_TABLE", {"table": table, **report["tables"][table]})
        if report["interrompu"]:
            break
    return report


# ── R2 : récupération et envoi du miroir ────────────────────────────────
def pull_history(client, bucket: str, path: str, drop_local_if_absent: bool = True) -> Dict[str, Any]:
    """Récupère le miroir existant (empreinte vérifiée). Absent = premier
    export : l'export repart alors d'un fichier vide (jamais d'une copie
    locale inconnue) ; ``fetch`` laisse au contraire la copie locale en place."""
    head = _head(client, bucket, HISTORY_KEY)
    if head is None:
        if drop_local_if_absent and os.path.exists(path):
            os.remove(path)
        return {"present": False, "counts": {}}
    tmp = path + ".r2download"
    obj = client.get_object(Bucket=bucket, Key=HISTORY_KEY)
    with open(tmp, "wb") as f:
        for chunk in iter(lambda: obj["Body"].read(1 << 20), b""):
            f.write(chunk)
    expected = (obj.get("Metadata") or {}).get("sha256")
    if expected and expected != sha256_file(tmp):
        os.remove(tmp)
        raise HistoryGuardError("EMPREINTE_INVALIDE sur history/turf_history.db")
    os.replace(tmp, path)
    counts = json.loads((obj.get("Metadata") or {}).get("counts") or "{}")
    return {"present": True, "counts": counts, "etag": obj.get("ETag")}


def push_history(client, bucket: str, path: str, previous_counts: Dict[str, int],
                 counts: Dict[str, int], allow_drop: bool = False) -> Dict[str, Any]:
    """Envoie le miroir. Refuse une chute brutale du nombre de lignes (droit
    révoqué, table vidée, bug) ; garde la version précédente en
    ``history/turf_history_prev.db``."""
    before, after = sum(previous_counts.values()), sum(counts.values())
    if before and after < before * (1 - MAX_DROP_RATIO) and not allow_drop:
        raise HistoryGuardError(f"CHUTE_REFUSEE : {before} -> {after} lignes")
    if _head(client, bucket, HISTORY_KEY) is not None:
        client.copy_object(Bucket=bucket, Key=HISTORY_PREV_KEY, CopySource={"Bucket": bucket, "Key": HISTORY_KEY})
    digest = sha256_file(path)
    with open(path, "rb") as f:
        client.put_object(Bucket=bucket, Key=HISTORY_KEY, Body=f, ContentType="application/vnd.sqlite3",
                          Metadata={"sha256": digest,
                                    "counts": json.dumps(counts, separators=(",", ":"), sort_keys=True),
                                    "source": "radar-elite-predictive/lecteur_benter"})
    return {"sha256": digest, "lignes_avant": before, "lignes_apres": after}


# ── CLI ─────────────────────────────────────────────────────────────────
def main(argv: Optional[List[str]] = None, source_factory=PgSource, client_factory=make_client,
         now: Optional[datetime] = None) -> int:
    parser = argparse.ArgumentParser(description="Export de l'historique Radar vers R2")
    parser.add_argument("action", choices=["probe", "run", "status", "fetch"])
    parser.add_argument("--db", default=DEFAULT_DB)
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--tables", default=",".join(TABLES))
    parser.add_argument("--refresh-days", type=int, default=3)
    parser.add_argument("--max-minutes", type=float, default=45)
    parser.add_argument("--force-hors-creneau", action="store_true")
    parser.add_argument("--allow-drop", action="store_true")
    args = parser.parse_args(argv)
    now = now or datetime.now(timezone.utc)

    tables = [t.strip() for t in args.tables.split(",") if t.strip()]
    unknown = [t for t in tables if t not in TABLES]
    if unknown:
        _log("HISTORY_CONFIG_ERROR", {"tables_inconnues": unknown})
        return 2
    dsn = (os.environ.get(ENV_DSN) or "").strip()
    if args.action in ("probe", "run") and not dsn:
        _log("HISTORY_CONFIG_ERROR", {"erreur": f"secret {ENV_DSN} absent"})
        return 2
    try:
        cfg = r2_config()
    except R2ConfigError as exc:
        _log("HISTORY_CONFIG_ERROR", {"erreur": str(exc)})
        return 2
    if cfg is None and args.action in ("run", "status", "fetch"):
        _log("HISTORY_CONFIG_ERROR", {"erreur": "secrets R2 absents"})
        return 2

    if args.action == "probe":
        source = source_factory(dsn)
        try:
            _log("HISTORY_PROBE_OK", source.whoami())
        finally:
            source.close()
        return 0

    client = client_factory(cfg)
    if args.action == "status":
        head = _head(client, cfg["bucket"], HISTORY_KEY)
        meta = (head or {}).get("Metadata") or {}
        print(json.dumps({"present": head is not None, "taille_octets": (head or {}).get("ContentLength"),
                          "sha256": meta.get("sha256"),
                          "counts": json.loads(meta["counts"]) if meta.get("counts") else None},
                         ensure_ascii=False, indent=1))
        return 0

    if args.action == "fetch":
        try:
            got = pull_history(client, cfg["bucket"], args.db, drop_local_if_absent=False)
        except HistoryGuardError as exc:
            _log("HISTORY_GUARD_REFUSED", {"motif": str(exc)})
            return 3
        _log("HISTORY_FETCH_OK" if got["present"] else "HISTORY_ABSENT",
             {"chemin": args.db, "counts": got["counts"]})
        return 0 if got["present"] else 1

    budget = args.max_minutes * 60
    if not args.force_hors_creneau:
        # Arrêt strict demandé par le Radar : dernière requête à 04h55 au plus tard.
        fin = now.replace(hour=CRENEAU_UTC[1], minute=0, second=0, microsecond=0) - MARGE_FIN_CRENEAU
        budget = min(budget, (fin - now).total_seconds())
        if not in_window(now) or budget <= 0:
            _log("HISTORY_HORS_CRENEAU", {"heure_utc": now.strftime("%H:%M"), "creneau": "00:00-04:55 UTC"})
            return 0

    try:
        previous = pull_history(client, cfg["bucket"], args.db)
        source = source_factory(dsn)
        db = HistoryDB(args.db)
        try:
            _log("HISTORY_PROBE_OK", source.whoami())
            report = run_export(source, db, tables, today=now.date().isoformat(),
                                refresh_days=args.refresh_days, start=args.start, end=args.end,
                                max_seconds=budget)
            counts, coverage = db.counts(), db.coverage()
        finally:
            db.close()
            source.close()
        sent = push_history(client, cfg["bucket"], args.db, previous["counts"], counts, args.allow_drop)
    except HistoryGuardError as exc:
        _log("HISTORY_GUARD_REFUSED", {"motif": str(exc)})
        return 3
    _log("HISTORY_EXPORT_OK", {"interrompu": report["interrompu"], "counts": counts,
                               "couverture": coverage, **sent})
    return 0


if __name__ == "__main__":
    sys.exit(main())
