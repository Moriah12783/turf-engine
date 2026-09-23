"""Persistance de la base sur Cloudflare R2 — étape 1 de la sortie de GitHub.

La base ``turf_bench.db`` ne voyage plus dans Git (plafond GitHub de 100 Mo
par fichier, atteint vers fin novembre 2026 au rythme actuel). R2 devient la
source de vérité ; chaque passe :

  1. ``pull`` : télécharge la base depuis R2, vérifie son empreinte SHA-256 et
     son intégrité SQLite, puis la pose à la place de la copie locale ;
  2. la passe normale tourne (``main.py --action sync``) ;
  3. ``push`` : renvoie la base vers R2 et pose la sauvegarde du jour
     (``backups/turf_bench_AAAA-MM-JJ.db``).

Règles absolues :
  - Mode LEGACY tant qu'aucun secret R2 n'est posé : rien ne change, la base
    continue d'être committée (journal ``R2_DISABLED``). Configuration
    partielle => ÉCHEC visible, jamais de demi-mesure silencieuse.
  - Amorçage UNIQUE : si le bucket ne contient ni la base ni le témoin
    ``state/seed.json``, la copie Git du checkout sert de point de départ.
    Base absente APRÈS amorçage => ÉCHEC : on ne repart jamais d'une copie
    périmée qui écraserait l'historique.
  - Anti-rétrécissement : les tables append-only (courses, éditions
    verrouillées, arrivées, photographies de cotes) ne peuvent pas perdre de
    lignes entre le pull et le push (sauf ``--allow-shrink`` explicite).
  - Écriture concurrente : si la base distante a changé depuis le pull
    (ETag différent), le push est refusé.
  - Aucune donnée d'accès dans le dépôt : tout vient des secrets GitHub.

Usage :
    python -m turf_lab.r2_store pull  --db turf_bench.db
    python -m turf_lab.r2_store push  --db turf_bench.db [--allow-shrink]
    python -m turf_lab.r2_store fetch --db copie.db     # lecture seule (devs)
    python -m turf_lab.r2_store status
"""

import argparse
import hashlib
import json
import os
import sqlite3
import sys
from datetime import datetime
from typing import Any, Dict, Optional

ENV_ACCOUNT = "R2_ACCOUNT_ID"
ENV_ACCOUNT_FALLBACK = "CLOUDFLARE_ACCOUNT_ID"
ENV_KEY_ID = "R2_ACCESS_KEY_ID"
ENV_SECRET = "R2_SECRET_ACCESS_KEY"
ENV_BUCKET = "R2_BUCKET"
# Optionnel : autre point d'accès S3 (tests locaux, MinIO). Par défaut R2.
ENV_ENDPOINT = "R2_ENDPOINT_URL"

DB_KEY = "turf_bench.db"
SEED_MARKER_KEY = "state/seed.json"
BACKUP_PREFIX = "backups/turf_bench_"

# Tables append-only protégées par la garde anti-rétrécissement.
GUARDED_TABLES = ("races", "predictions", "race_results", "odds_snapshots")
# Tables comptées pour la traçabilité (métadonnées de l'objet R2).
COUNTED_TABLES = GUARDED_TABLES + ("runners", "rapports")

CHUNK = 1 << 20


class R2ConfigError(Exception):
    """Configuration R2 incomplète (certains secrets posés, pas tous)."""


class R2GuardError(Exception):
    """Refus de sécurité : la base ne doit pas être lue ou écrite."""


def _log(tag: str, payload: Dict[str, Any]) -> None:
    print(tag + " " + json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _now_utc() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


# ── Configuration ───────────────────────────────────────────────────────
def r2_config(env: Optional[Dict[str, str]] = None) -> Optional[Dict[str, str]]:
    """Configuration R2 depuis l'environnement.

    None si AUCUN secret R2 n'est posé (mode legacy) ; R2ConfigError si la
    configuration est partielle."""
    env = os.environ if env is None else env
    key_id = (env.get(ENV_KEY_ID) or "").strip()
    secret = (env.get(ENV_SECRET) or "").strip()
    bucket = (env.get(ENV_BUCKET) or "").strip()
    account = (env.get(ENV_ACCOUNT) or env.get(ENV_ACCOUNT_FALLBACK) or "").strip()
    if not (key_id or secret or bucket):
        return None
    missing = [name for name, value in ((ENV_KEY_ID, key_id), (ENV_SECRET, secret),
                                        (ENV_BUCKET, bucket), (ENV_ACCOUNT, account)) if not value]
    if missing:
        raise R2ConfigError("Configuration R2 incomplète, manquant : " + ", ".join(missing))
    cfg = {"account_id": account, "key_id": key_id, "secret": secret, "bucket": bucket}
    endpoint = (env.get(ENV_ENDPOINT) or "").strip()
    if endpoint:
        cfg["endpoint_url"] = endpoint
    return cfg


def client_config():
    """Réglages botocore pour R2. Recommandation Cloudflare pour boto3 >= 1.36 :
    pas de sommes de contrôle CRC imposées par défaut (R2 ne les exige pas)."""
    from botocore.config import Config
    return Config(
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        retries={"max_attempts": 5, "mode": "standard"},
        connect_timeout=15,
        read_timeout=120,
    )


def make_client(cfg: Dict[str, str]):
    """Client S3 pointé sur R2. boto3 n'est importé qu'en mode R2 : le mode
    legacy n'a aucune dépendance nouvelle."""
    import boto3
    return boto3.client(
        "s3",
        endpoint_url=cfg.get("endpoint_url") or f"https://{cfg['account_id']}.r2.cloudflarestorage.com",
        aws_access_key_id=cfg["key_id"],
        aws_secret_access_key=cfg["secret"],
        region_name="auto",
        config=client_config(),
    )


# ── Outils fichiers / SQLite ────────────────────────────────────────────
def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(CHUNK), b""):
            h.update(chunk)
    return h.hexdigest()


def table_counts(db_path: str) -> Dict[str, int]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        existing = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return {t: int(conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0])
                for t in COUNTED_TABLES if t in existing}
    finally:
        conn.close()


def check_sqlite(db_path: str) -> Dict[str, int]:
    """Base SQLite saine et non vide, sinon R2GuardError. Renvoie les comptes."""
    try:
        conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
        try:
            verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        counts = table_counts(db_path)
    except sqlite3.DatabaseError as exc:
        raise R2GuardError(f"BASE_ILLISIBLE : {exc}")
    if verdict != "ok":
        raise R2GuardError(f"INTEGRITE_SQLITE : {verdict}")
    if counts.get("races", 0) <= 0:
        raise R2GuardError("BASE_VIDE : aucune course")
    return counts


def _is_not_found(exc: Exception) -> bool:
    code = str((getattr(exc, "response", None) or {}).get("Error", {}).get("Code", ""))
    return code in ("404", "NoSuchKey", "NotFound")


def _head(client, bucket: str, key: str) -> Optional[Dict[str, Any]]:
    """Métadonnées de l'objet, None s'il n'existe pas. Toute autre erreur
    (réseau, droits) remonte : on ne confond jamais « absent » et « illisible »."""
    try:
        return client.head_object(Bucket=bucket, Key=key)
    except Exception as exc:
        if _is_not_found(exc):
            return None
        raise


def _download(client, bucket: str, key: str, dest: str) -> Dict[str, Any]:
    obj = client.get_object(Bucket=bucket, Key=key)
    body = obj["Body"]
    with open(dest, "wb") as f:
        while True:
            chunk = body.read(CHUNK)
            if not chunk:
                break
            f.write(chunk)
    return obj


def _state_path(db_path: str) -> str:
    return db_path + ".r2state.json"


def _write_state(path: str, state: Dict[str, Any]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=1, sort_keys=True)


# ── Opérations ──────────────────────────────────────────────────────────
def fetch(client, bucket: str, db_path: str) -> Dict[str, Any]:
    """Télécharge et vérifie la base, sans jamais écrire sur R2 (accès
    lecture seule suffisant). Remplace ``db_path`` seulement si tout est sain."""
    tmp = db_path + ".r2download"
    try:
        obj = _download(client, bucket, DB_KEY, tmp)
    except Exception as exc:
        if os.path.exists(tmp):
            os.remove(tmp)
        if _is_not_found(exc):
            raise R2GuardError("BASE_ABSENTE_SUR_R2")
        raise
    try:
        digest = sha256_file(tmp)
        expected = (obj.get("Metadata") or {}).get("sha256")
        if expected and expected != digest:
            raise R2GuardError(f"EMPREINTE_INVALIDE : attendu {expected}, obtenu {digest}")
        counts = check_sqlite(tmp)
    except Exception:
        os.remove(tmp)
        raise
    os.replace(tmp, db_path)
    return {"etag": obj.get("ETag"), "sha256": digest, "counts": counts,
            "sha256_verifie": bool(expected)}


def pull(client, bucket: str, db_path: str, now: Optional[str] = None) -> Dict[str, Any]:
    """Début de passe : base R2 -> copie locale, ou amorçage unique."""
    now = now or _now_utc()
    if _head(client, bucket, DB_KEY) is None:
        if _head(client, bucket, SEED_MARKER_KEY) is not None:
            raise R2GuardError("BASE_ABSENTE_APRES_AMORCAGE : restaurer depuis backups/ "
                               "au lieu de repartir de la copie Git")
        if not os.path.exists(db_path):
            raise R2GuardError("AMORCAGE_IMPOSSIBLE : aucune copie locale de la base")
        counts = check_sqlite(db_path)
        state = {"mode": "seed", "etag": None, "sha256": sha256_file(db_path), "counts": counts}
        _log("R2_SEED_PENDING", {"bucket": bucket, "counts": counts})
    else:
        info = fetch(client, bucket, db_path)
        state = {"mode": "r2", "etag": info["etag"], "sha256": info["sha256"], "counts": info["counts"]}
        _log("R2_PULL_OK", {"bucket": bucket, "counts": info["counts"],
                            "sha256_verifie": info["sha256_verifie"]})
    state["pulled_at_utc"] = now
    _write_state(_state_path(db_path), state)
    return state


def push(client, bucket: str, db_path: str, now: Optional[str] = None,
         allow_shrink: bool = False, env: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Fin de passe : copie locale -> R2 (+ sauvegarde du jour, + témoin
    d'amorçage la première fois). Refuse tout ce qui pourrait perdre des données."""
    env = os.environ if env is None else env
    now = now or _now_utc()
    state_file = _state_path(db_path)
    if not os.path.exists(state_file):
        raise R2GuardError("ETAT_PULL_ABSENT : push interdit sans pull préalable dans la même passe")
    with open(state_file, "r", encoding="utf-8") as f:
        state = json.load(f)

    counts = check_sqlite(db_path)
    shrunk = {t: {"avant": state["counts"].get(t, 0), "apres": counts.get(t, 0)}
              for t in GUARDED_TABLES if counts.get(t, 0) < state["counts"].get(t, 0)}
    if shrunk and not allow_shrink:
        raise R2GuardError("RETRECISSEMENT_REFUSE : " + json.dumps(shrunk, sort_keys=True))

    head = _head(client, bucket, DB_KEY)
    if state["mode"] == "seed":
        if head is not None:
            raise R2GuardError("AMORCAGE_CONCURRENT : une base est apparue sur R2 pendant la passe")
    elif head is None:
        raise R2GuardError("BASE_DISTANTE_DISPARUE pendant la passe")
    elif head.get("ETag") != state["etag"]:
        raise R2GuardError("BASE_DISTANTE_MODIFIEE depuis le pull (écriture concurrente)")

    digest = sha256_file(db_path)
    backup_key = f"{BACKUP_PREFIX}{now[:10]}.db"
    if state["mode"] == "r2" and digest == state["sha256"]:
        # Base inchangée : pas de réécriture, mais la sauvegarde du jour existe.
        if _head(client, bucket, backup_key) is None:
            client.copy_object(Bucket=bucket, Key=backup_key,
                               CopySource={"Bucket": bucket, "Key": DB_KEY})
        _log("R2_PUSH_SKIPPED", {"motif": "BASE_INCHANGEE", "sha256": digest})
        return {"uploaded": False, "sha256": digest, "counts": counts, "backup": backup_key}

    metadata = {
        "sha256": digest,
        "counts": json.dumps(counts, separators=(",", ":"), sort_keys=True),
        "pushed-at": now,
        "run-id": env.get("GITHUB_RUN_ID") or "",
        "commit": env.get("GITHUB_SHA") or "",
    }
    with open(db_path, "rb") as f:
        client.put_object(Bucket=bucket, Key=DB_KEY, Body=f, Metadata=metadata,
                          ContentType="application/vnd.sqlite3")

    # Vérification post-écriture : l'objet relu porte bien notre empreinte.
    written = _head(client, bucket, DB_KEY)
    if written is None or (written.get("Metadata") or {}).get("sha256") != digest:
        raise R2GuardError("VERIFICATION_ECRITURE : l'objet relu ne porte pas l'empreinte envoyée")

    client.copy_object(Bucket=bucket, Key=backup_key, CopySource={"Bucket": bucket, "Key": DB_KEY})

    if state["mode"] == "seed":
        marker = {"seeded_at_utc": now, "sha256": digest, "counts": counts,
                  "commit": env.get("GITHUB_SHA") or None, "run_id": env.get("GITHUB_RUN_ID") or None}
        client.put_object(Bucket=bucket, Key=SEED_MARKER_KEY, ContentType="application/json",
                          Body=json.dumps(marker, ensure_ascii=False, sort_keys=True).encode("utf-8"))
        _log("R2_SEED_OK", {"bucket": bucket, "sha256": digest, "counts": counts})

    # Un second push dans la même passe repart de l'état qui vient d'être écrit.
    _write_state(state_file, {"mode": "r2", "etag": written.get("ETag"), "sha256": digest,
                              "counts": counts, "pulled_at_utc": state.get("pulled_at_utc")})
    _log("R2_PUSH_OK", {"bucket": bucket, "sha256": digest, "counts": counts,
                        "shrink_autorise": bool(shrunk), "backup": backup_key})
    return {"uploaded": True, "sha256": digest, "counts": counts, "backup": backup_key}


def status(client, bucket: str) -> Dict[str, Any]:
    head = _head(client, bucket, DB_KEY)
    marker = _head(client, bucket, SEED_MARKER_KEY)
    out = {"bucket": bucket, "base_presente": head is not None, "amorcee": marker is not None}
    if head is not None:
        meta = head.get("Metadata") or {}
        out.update({"taille_octets": head.get("ContentLength"), "sha256": meta.get("sha256"),
                    "pushed_at": meta.get("pushed-at"), "run_id": meta.get("run-id"),
                    "counts": json.loads(meta["counts"]) if meta.get("counts") else None})
    return out


# ── CLI ─────────────────────────────────────────────────────────────────
def _export_mode(mode: str) -> None:
    """Expose R2_MODE aux étapes suivantes du workflow GitHub."""
    github_env = os.environ.get("GITHUB_ENV")
    if github_env:
        with open(github_env, "a", encoding="utf-8") as f:
            f.write(f"R2_MODE={mode}\n")


def main(argv: Optional[list] = None, client_factory=make_client) -> int:
    parser = argparse.ArgumentParser(description="Persistance de turf_bench.db sur Cloudflare R2")
    parser.add_argument("action", choices=["pull", "push", "fetch", "status"])
    parser.add_argument("--db", default="turf_bench.db")
    parser.add_argument("--allow-shrink", action="store_true",
                        help="Autorise exceptionnellement une base avec moins de lignes (purge volontaire).")
    args = parser.parse_args(argv)

    try:
        cfg = r2_config()
    except R2ConfigError as exc:
        _log("R2_CONFIG_ERROR", {"erreur": str(exc)})
        return 2
    if cfg is None:
        _log("R2_DISABLED", {"motif": "aucun secret R2 : mode legacy (base committée dans Git)"})
        if args.action == "pull":
            _export_mode("legacy")
            return 0
        return 2 if args.action in ("fetch", "status") else 0

    client = client_factory(cfg)
    try:
        if args.action == "pull":
            pull(client, cfg["bucket"], args.db)
            _export_mode("r2")
        elif args.action == "push":
            push(client, cfg["bucket"], args.db, allow_shrink=args.allow_shrink)
        elif args.action == "fetch":
            info = fetch(client, cfg["bucket"], args.db)
            _log("R2_FETCH_OK", {"db": args.db, "counts": info["counts"], "sha256": info["sha256"]})
        else:
            print(json.dumps(status(client, cfg["bucket"]), ensure_ascii=False, indent=1))
    except R2GuardError as exc:
        _log("R2_GUARD_REFUSED", {"action": args.action, "motif": str(exc)})
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
