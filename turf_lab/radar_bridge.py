"""Pont RADAR_V4 — les probabilités scellées de Radar (journal `v4-labels`)
entrent dans le banc de mesure comme un QUATRIÈME moteur, verrouillé aux
mêmes horizons et jugé par le même banc que NEW_VALUE_ENGINE, ETPE_ENGINE
et MARKET_BASELINE. Aucun moteur existant n'est modifié.

Règles absolues :
  - Aucune fuite temporelle : une ligne Radar n'alimente un verrou que si
    son `scelle_a` est antérieur ou égal à l'instant du verrou (`as_of_utc`).
  - Même porte de fraîcheur que les autres moteurs (priced_ratio ≥ 0.90),
    appliquée par l'appelant (daily_sync._lock_radar).
  - Radar reste fermé : l'accès passe par la fonction `fn_journal_du_jour`,
    protégée par un jeton de pont (RADAR_BRIDGE_TOKEN). Rien en clair dans
    le dépôt : URL, clé publiable et jeton viennent des secrets GitHub.
  - Si la configuration manque, le pont est DÉSACTIVÉ proprement
    (log RADAR_BRIDGE_DISABLED) : aucun verrou RADAR_V4, rien d'autre ne change.
"""

import gzip
import json
import os
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

ENGINE_NAME = "RADAR_V4"
MIN_COVERAGE = 0.90
ENV_URL = "RADAR_SUPABASE_URL"
ENV_KEY = "RADAR_PUBLISHABLE_KEY"
ENV_TOKEN = "RADAR_BRIDGE_TOKEN"


def bridge_config() -> Optional[Dict[str, str]]:
    """Configuration par variables d'environnement ; None si incomplète."""
    url = (os.environ.get(ENV_URL) or "").strip().rstrip("/")
    key = (os.environ.get(ENV_KEY) or "").strip()
    token = (os.environ.get(ENV_TOKEN) or "").strip()
    if not url or not key or not token:
        return None
    return {"url": url, "key": key, "token": token}


def _parse_ts(value: Any) -> Optional[datetime]:
    """timestamptz PostgREST ('2026-09-07T07:35:00.123456+00:00') → datetime naïf UTC."""
    if not value:
        return None
    s = str(value).strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = (dt - dt.utcoffset()).replace(tzinfo=None)
    return dt


class RadarBridgeClient:
    """Client HTTP minimal (urllib, comme daily_sync.get_json) avec cache
    mémoire par date : UNE requête par date et par passe, jamais par course."""

    def __init__(self, config: Optional[Dict[str, str]], timeout: int = 10):
        self.config = config
        self.timeout = timeout
        self._cache: Dict[str, List[Dict[str, Any]]] = {}

    @property
    def enabled(self) -> bool:
        return self.config is not None

    def _rpc(self, function: str, params: Dict[str, Any]) -> Any:
        """Appel PostgREST /rest/v1/rpc/<function> (POST JSON, TLS vérifié)."""
        assert self.config is not None
        url = f"{self.config['url']}/rest/v1/rpc/{function}"
        body = json.dumps(params).encode("utf-8")
        headers = {
            "apikey": self.config["key"],
            "Authorization": f"Bearer {self.config['key']}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        ctx = ssl.create_default_context()
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        with urllib.request.urlopen(req, context=ctx, timeout=self.timeout) as response:
            data = response.read()
            if response.info().get("Content-Encoding") == "gzip":
                data = gzip.decompress(data)
            return json.loads(data.decode("utf-8"))

    def _post(self, date_iso: str) -> Optional[List[Dict[str, Any]]]:
        assert self.config is not None
        payload = self._rpc("fn_journal_du_jour", {"p_date": date_iso, "p_token": self.config["token"]})
        return payload if isinstance(payload, list) else None

    def push_report_summary(self, summary: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Sens retour du pont : dépose le résumé du banc dans Radar
        (`fn_pont_rapport`, même jeton). Une tentative + un retry ; jamais
        d'exception vers l'appelant (log RADAR_BRIDGE_REPORT_ERROR). La réponse
        de Radar (statut ACCEPTE / REFUSE_*) est journalisée telle quelle."""
        if not self.enabled:
            return None
        last_error = ""
        for attempt in (1, 2):
            try:
                result = self._rpc("fn_pont_rapport", {"p_token": self.config["token"], "p_rapport": summary})
                if isinstance(result, dict):
                    print("RADAR_BRIDGE_REPORT_PUSHED " + json.dumps({
                        "statut": result.get("statut"), "detail": result.get("detail"),
                        "communes_tous": result.get("communes_tous"), "commit": summary.get("commit"),
                    }))
                    return result
                last_error = "reponse inattendue"
            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code}"
                if e.code in (400, 401, 403, 404, 422):
                    break
            except Exception as e:  # réseau, timeout, JSON
                last_error = repr(e)
        print("RADAR_BRIDGE_REPORT_ERROR " + json.dumps({"commit": summary.get("commit"), "error": last_error}))
        return None

    def fetch_radar_journal(self, date_iso: str) -> List[Dict[str, Any]]:
        """Lignes du journal Radar pour la date (AAAA-MM-JJ). Une tentative + un
        retry ; en cas d'échec, liste vide et log RADAR_BRIDGE_ERROR (jamais
        d'exception vers la synchronisation)."""
        if not self.enabled:
            return []
        if date_iso in self._cache:
            return self._cache[date_iso]
        rows: List[Dict[str, Any]] = []
        last_error = ""
        for attempt in (1, 2):
            try:
                payload = self._post(date_iso)
                if payload is not None:
                    rows = payload
                    break
                last_error = "reponse inattendue"
            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code}"
                if e.code in (400, 401, 403, 404, 422):
                    break  # jeton / date hors fenêtre : inutile de réessayer
            except Exception as e:  # réseau, timeout, JSON
                last_error = repr(e)
        if not rows and last_error:
            print("RADAR_BRIDGE_ERROR " + json.dumps({"date": date_iso, "error": last_error}))
        self._cache[date_iso] = rows
        return rows

    def clear_cache(self) -> None:
        self._cache.clear()


def fetch_radar_journal(date_iso: str) -> List[Dict[str, Any]]:
    """Raccourci sans cache partagé (usage script / debug)."""
    return RadarBridgeClient(bridge_config()).fetch_radar_journal(date_iso)


SUMMARY_SCHEMA = "1.0"
SUMMARY_HORIZONS = ["T_MATIN", "T90", "T30", "T15", "TOUS"]


def _num(value: Any) -> Optional[float]:
    try:
        return None if value is None else float(value)
    except (TypeError, ValueError):
        return None


def build_report_summary(report: Dict[str, Any], commit: Optional[str] = None,
                         run_id: Optional[str] = None, generated_at_utc: Optional[str] = None) -> Dict[str, Any]:
    """Résumé compact (quelques Ko) de benchmark_report.json pour l'auditeur
    Radar : identité du code, effectifs, et par horizon du bloc courses_communes
    le Brier / Top1 / Top3 / Top8 / ROI gagnant de chaque moteur. Aucune donnée
    par course : uniquement des agrégats déjà publiés."""
    evaluations = report.get("evaluations") or {}
    communes = report.get("courses_communes") or {}
    horizons_in = communes.get("horizons") or {}
    logs = report.get("historical_logs") or []
    first_log = logs[0].get("race_id") if logs and isinstance(logs[0], dict) else None

    horizons_out: Dict[str, Any] = {}
    for h in SUMMARY_HORIZONS:
        block = horizons_in.get(h) or {}
        metriques = block.get("metriques") or {}
        engines_out: Dict[str, Any] = {}
        for engine, m in metriques.items():
            hits = m.get("hit_rates") or {}
            calib = m.get("statistical_calibration") or {}
            fin = (m.get("financial_performance") or {}).get("simple_gagnant") or {}
            engines_out[engine] = {
                "brier": _num(calib.get("brier_score")),
                "top1_pct": _num(hits.get("top1_win_rate_pct")),
                "top3_pct": _num(hits.get("winner_in_top3_pct")),
                "top8_pct": _num(hits.get("winner_in_top8_pct")),
                "roi_gagnant_pct": _num(fin.get("roi_pct")),
                "total_races": m.get("total_races"),
            }
        horizons_out[h] = {"courses": block.get("courses"), "moteurs": engines_out}

    return {
        "schema": SUMMARY_SCHEMA,
        "commit": commit,
        "run_id": run_id,
        "genere_le_utc": generated_at_utc or datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_finished_races": report.get("total_finished_races"),
        "horizon_bench_start_date": report.get("horizon_bench_start_date"),
        "engines_communes": list(communes.get("engines") or []),
        "total_races_par_moteur": {name: (ev or {}).get("total_races") for name, ev in evaluations.items()},
        "premier_log_race_id": first_log,
        "nb_logs": len(logs),
        "horizons": horizons_out,
    }


def push_report_summary(report: Dict[str, Any], commit: Optional[str] = None,
                        run_id: Optional[str] = None,
                        client: Optional[RadarBridgeClient] = None) -> Optional[Dict[str, Any]]:
    """Construit le résumé et le dépose dans Radar. Pont désactivé (secrets
    absents) → None, log RADAR_BRIDGE_REPORT_SKIPPED, rien d'autre ne change."""
    cli = client if client is not None else RadarBridgeClient(bridge_config())
    if not cli.enabled:
        print("RADAR_BRIDGE_REPORT_SKIPPED " + json.dumps({"reason": "bridge_disabled"}))
        return None
    return cli.push_report_summary(build_report_summary(report, commit=commit, run_id=run_id))


class RadarV4Engine:
    """Quatrième moteur du banc : sélection et probabilités = journal Radar.
    Même contrat que MarketOddsEngine.predict(race, runners), plus `as_of_utc`."""

    def __init__(self, client: Optional[RadarBridgeClient] = None, engine_name: str = ENGINE_NAME):
        self.engine_name = engine_name
        self.client = client if client is not None else RadarBridgeClient(bridge_config())
        if not self.client.enabled:
            print("RADAR_BRIDGE_DISABLED " + json.dumps({
                "missing": [v for v in (ENV_URL, ENV_KEY, ENV_TOKEN) if not (os.environ.get(v) or "").strip()]
            }))

    @property
    def enabled(self) -> bool:
        return self.client.enabled

    @staticmethod
    def _absent(engine_name: str, coverage: float, reason: str) -> Dict[str, Any]:
        return {
            "engine_name": engine_name, "selection": [], "bases": [], "outsider_num": None,
            "probabilities": {},
            "metadata": {"type": "radar_v4", "status": "ABSENT", "coverage": round(coverage, 3), "reason": reason},
        }

    def predict(self, race: Dict[str, Any], runners: List[Dict[str, Any]],
                as_of_utc: Optional[datetime] = None,
                journal_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
        """`journal_rows` permet d'injecter les lignes (tests) ; sinon elles
        sont lues via le client (cache par date)."""
        if as_of_utc is None:
            as_of_utc = datetime.utcnow()
        active = [r for r in runners if not r.get("is_non_partant", False)]
        if not active:
            return self._absent(self.engine_name, 0.0, "NO_RUNNERS")

        date_iso = str(race.get("date", ""))
        rows = journal_rows if journal_rows is not None else self.client.fetch_radar_journal(date_iso)
        if not rows:
            return self._absent(self.engine_name, 0.0, "NO_JOURNAL")

        r_num = int(race.get("meeting_number", 0))
        c_num = int(race.get("race_number", 0))
        # Appariement (num_reunion, num_course, num_pmu) ; garde anti-fuite sur scelle_a.
        by_num: Dict[int, Dict[str, Any]] = {}
        for row in rows:
            try:
                if int(row.get("num_reunion")) != r_num or int(row.get("num_course")) != c_num:
                    continue
            except (TypeError, ValueError):
                continue
            scelle_a = _parse_ts(row.get("scelle_a"))
            if scelle_a is None or scelle_a > as_of_utc:
                continue
            if row.get("p_win") is None:
                continue
            try:
                by_num[int(row.get("num_pmu"))] = row
            except (TypeError, ValueError):
                continue

        matched = [r for r in active if int(r["num"]) in by_num]
        coverage = len(matched) / len(active)
        if coverage < MIN_COVERAGE:
            return self._absent(self.engine_name, coverage, "COVERAGE")

        # Probabilités renormalisées sur les partants actifs appariés.
        raw = {int(r["num"]): float(by_num[int(r["num"])]["p_win"]) for r in matched}
        total = sum(raw.values()) or 1.0
        probs = {str(n): round(p / total, 4) for n, p in raw.items()}
        ordered = sorted(raw.keys(), key=lambda n: (-raw[n], n))
        selection = ordered[:8]
        bases = ordered[:2]
        outsider = ordered[7] if len(ordered) >= 8 else None

        odds_now = {int(r["num"]): r.get("odds_t15", r.get("final_odds")) for r in matched}
        value_indices = {}
        for n in ordered:
            o = odds_now.get(n)
            if o is not None and float(o) > 1.0:
                value_indices[str(n)] = round(probs[str(n)] * float(o), 2)

        sample = by_num[ordered[0]]
        return {
            "engine_name": self.engine_name,
            "selection": selection,
            "bases": bases,
            "outsider_num": outsider,
            "probabilities": probs,
            "metadata": {
                "type": "radar_v4",
                "status": "OK",
                "edition": sample.get("edition"),
                "scelle_a": sample.get("scelle_a"),
                "modele": sample.get("modele"),
                "coverage": round(coverage, 3),
                "as_of_utc": as_of_utc.isoformat(),
                "value_indices": value_indices,
            },
        }
