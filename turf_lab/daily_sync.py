"""Automated daily sync module: Ingests real PMU race feeds, manages Non-Partants (NP),
extracts official finish orders and payouts, generates complete 4-horizon predictions (T_MATIN, T90, T30, T15),
ensures historical archive persistence, and resolves results.
"""

import json
import os
import re
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple
from turf_lab.database import TurfDatabase
from turf_lab.engine import NewValueEngine
from turf_lab.baselines import ETPEEngineProxy, MarketOddsEngine
from turf_lab.human_stats import HumanStatsBook
from turf_lab.odds_quality import MIN_PRICED_RATIO, priced_ratio
from turf_lab.radar_bridge import ENGINE_NAME as RADAR_ENGINE, RadarV4Engine
from turf_lab import secure_http
from turf_lab.results_reader import (
    SOURCE_COURSE, SOURCE_PROGRAMME, STATUT_ANNULEE, STATUT_DEFINITIVE, STATUT_PROVISOIRE,
    ArrivalReading, read_arrival,
)


class PMUDataFetcher:
    """Client for public PMU open JSON endpoints.

    Point 1 du correctif partenaire : toutes les requêtes passent par
    ``secure_http.get_json`` — HTTPS obligatoire, certificat et nom d'hôte
    vérifiés, échec TLS journalisé (``TLS_ERROR``) et jamais contourné."""

    BASE_URL = "https://online.turfinfo.api.pmu.fr/rest/client/7/programme"

    @staticmethod
    def get_json(url: str, timeout: int = 10) -> Optional[Any]:
        return secure_http.get_json(url, timeout=timeout)

    def url_programme(self, date_str: str) -> str:
        return f"{self.BASE_URL}/{date_str}"

    def url_course(self, date_str: str, r_num: int, c_num: int) -> str:
        return f"{self.BASE_URL}/{date_str}/R{r_num}/C{c_num}"

    def fetch_programme(self, date_str: str) -> Optional[Dict[str, Any]]:
        """date_str format: DDMMYYYY (e.g. '31082026')"""
        return self.get_json(self.url_programme(date_str))

    def fetch_course_info(self, date_str: str, r_num: int, c_num: int) -> Optional[Dict[str, Any]]:
        return self.get_json(self.url_course(date_str, r_num, c_num))

    def fetch_participants(self, date_str: str, r_num: int, c_num: int) -> Optional[Dict[str, Any]]:
        url = f"{self.BASE_URL}/{date_str}/R{r_num}/C{c_num}/participants"
        return self.get_json(url)

    def fetch_rapports(self, date_str: str, r_num: int, c_num: int) -> Optional[Any]:
        url = f"{self.BASE_URL}/{date_str}/R{r_num}/C{c_num}/rapports-definitifs"
        return self.get_json(url)


class DailySyncManager:
    """Orchestrates daily ingestion, Non-Partant handling, multi-horizon locking (T_MATIN, T90, T30, T15), and results resolution."""

    # Vocabulaire PMU réel -> vocabulaire interne du moteur.
    # Sans cette normalisation, ATTELE/MONTE étaient traités comme du galop
    # (déferrage neutralisé, pénalité DAI ignorée, pondérations plat).
    DISCIPLINE_MAP = {
        "ATTELE": "TROT_ATTELE",
        "MONTE": "TROT_MONTE",
        "TROT_ATTELE": "TROT_ATTELE",
        "TROT_MONTE": "TROT_MONTE",
        "PLAT": "PLAT",
        "HAIE": "OBSTACLE_HAIE",
        "STEEPLECHASE": "OBSTACLE_STEEPLE",
        "STEEPLE": "OBSTACLE_STEEPLE",
        "CROSS": "OBSTACLE_CROSS",
        "STEEPLE_CROSS": "OBSTACLE_CROSS",
    }

    # ── PARITÉ LONACI : pays des réunions ingérées ───────────────────────
    # LONACI = réunions françaises du flux PMU (+ Maroc via SOREC, source
    # séparée à brancher plus tard). Ajouter un code ISO ici suffit pour
    # élargir le périmètre (ex. "BEL" si LONACI ajoute la Belgique).
    SUBSCRIBER_COUNTRIES = {"FRA"}

    def __init__(self, db: TurfDatabase):
        self.db = db
        self.fetcher = PMUDataFetcher()
        self.new_engine = NewValueEngine()
        self.etpe_engine = ETPEEngineProxy()
        self.market_engine = MarketOddsEngine()
        # F2 — carnet de stats humaines : construit à la demande, UNE fois
        # par passe, uniquement depuis les courses déjà terminées (aucune
        # information du futur ne peut fuiter dans un pronostic).
        self._human_book = None  # type: HumanStatsBook
        # Compteur de verrous refusés par la porte de fraîcheur (remis à zéro
        # à chaque passe, reporté dans les stats de sync_date).
        self.gate_refused = 0
        # Pont RADAR_V4 (4e moteur du banc) : désactivé proprement si la
        # configuration (RADAR_SUPABASE_URL / RADAR_PUBLISHABLE_KEY /
        # RADAR_BRIDGE_TOKEN) est absente — rien d'autre ne change.
        self.radar_engine = RadarV4Engine()
        self.gate_refused_radar = 0
        self.radar_absent = 0
        # Transparence « aucun chiffre retouché » : purge définitive des
        # courses de référence fictives et des dividendes estimés (idempotent).
        purged = self.db.purge_fictitious_data()
        if purged["fictitious_races"] or purged["estimated_rapports"]:
            print(f"[+] Purge transparence : {purged['fictitious_races']} courses fictives et "
                  f"{purged['estimated_rapports']} jeux de dividendes estimés supprimés.")

    @staticmethod
    def paris_utc_offset(dt_utc: datetime) -> int:
        """Décalage réel Paris vs UTC/GMT : +2 en heure d'été, +1 en heure d'hiver.
        Règle officielle de l'Union Européenne : l'heure d'été court du dernier
        dimanche de mars (01:00 UTC) au dernier dimanche d'octobre (01:00 UTC)."""
        def last_sunday_utc(year: int, month: int) -> datetime:
            d = datetime(year, month, 31)
            while d.weekday() != 6:  # 6 = dimanche
                d -= timedelta(days=1)
            return d.replace(hour=1)

        year = dt_utc.year
        if last_sunday_utc(year, 3) <= dt_utc < last_sunday_utc(year, 10):
            return 2  # heure d'été
        return 1      # heure d'hiver

    @classmethod
    def parse_pmu_time(cls, course_obj: Dict[str, Any], date_str_db: str, c_num: int) -> Tuple[str, str]:
        """Converts PMU timestamp or time string into precise GMT (Abidjan) and Paris strings.
        Le double affichage « HH:MM GMT (HH:MM Paris) » est conservé, avec le
        décalage Paris réel selon la saison (heure d'été/hiver)."""
        # Décalage Paris valable pour la date de la course (calé à midi UTC)
        try:
            probe = datetime.strptime(date_str_db, "%Y-%m-%d").replace(hour=12)
        except Exception:
            probe = datetime.utcnow()
        off = cls.paris_utc_offset(probe)

        # 1. Check heureDepart (timestamp in ms)
        h_dep = course_obj.get("heureDepart")
        if h_dep and isinstance(h_dep, (int, float)) and h_dep > 0:
            if h_dep > 1e11:  # ms
                dt_utc = datetime.utcfromtimestamp(h_dep / 1000.0)
            else:  # seconds
                dt_utc = datetime.utcfromtimestamp(h_dep)
            gmt_str = dt_utc.strftime("%H:%M")
            paris_str = (dt_utc + timedelta(hours=cls.paris_utc_offset(dt_utc))).strftime("%H:%M")
            return f"{gmt_str} GMT ({paris_str} Paris)", dt_utc.isoformat()

        # 2. Check heure string e.g. "15h15" or "15:15" (heure locale Paris)
        h_str = course_obj.get("heure") or course_obj.get("heureTexte") or course_obj.get("heureDepartString")
        if h_str and isinstance(h_str, str):
            clean = h_str.replace("h", ":").replace("H", ":").strip()
            parts = clean.split(":")
            if len(parts) >= 2:
                try:
                    p_h = int(parts[0])
                    p_m = int(parts[1])
                    gmt_h = (p_h - off) % 24
                    return f"{gmt_h:02d}:{p_m:02d} GMT ({p_h:02d}:{p_m:02d} Paris)", f"{date_str_db}T{gmt_h:02d}:{p_m:02d}:00Z"
                except Exception:
                    pass

        # 3. Known standard timetable based on race number (e.g. C1 ~ 11h55 GMT / 13h55 Paris, C2 ~ 12h30, C3 ~ 13h15...)
        standard_gmt_hours = {1: (11, 55), 2: (12, 30), 3: (13, 15), 4: (13, 50), 5: (14, 25), 6: (15, 0), 7: (15, 35), 8: (16, 10), 9: (16, 45)}
        gh, gm = standard_gmt_hours.get(c_num, (11 + (c_num * 35 // 60), (c_num * 35) % 60))
        ph = (gh + off) % 24
        return f"{gh:02d}:{gm:02d} GMT ({ph:02d}:{gm:02d} Paris)", f"{date_str_db}T{gh:02d}:{gm:02d}:00Z"

    # ------------------------------------------------------------------
    # Fenêtres de verrouillage par horizon (en minutes avant le départ).
    # Une petite tolérance absorbe la latence des crons GitHub Actions
    # (un cron planifié à H peut démarrer à H+3..H+8).
    # ------------------------------------------------------------------
    HORIZON_WINDOWS = [
        ("T90", 100),   # verrouillé dès que départ <= 100 min
        ("T30", 35),    # verrouillé dès que départ <= 35 min
        ("T15", 18),    # verrouillé dès que départ <= 18 min (vraie cote T-15)
    ]
    ALL_HORIZONS = ["T_MATIN", "T90", "T30", "T15"]
    ENGINE_KEYS = ["NEW", "ETPE", "PRESS", "MARKET"]

    @staticmethod
    def minutes_to_start(time_display: str, race_date_db: str, now_utc: Optional[datetime] = None) -> Optional[float]:
        """Minutes restantes avant le départ, à partir du libellé 'HH:MM GMT (...)' et de la date de course.
        Négatif si la course est déjà partie. None si l'heure est illisible."""
        if now_utc is None:
            now_utc = datetime.utcnow()
        if not time_display:
            return None
        m = re.match(r"^\s*(\d{1,2}):(\d{2})", str(time_display))
        if not m:
            return None
        try:
            race_dt = datetime.strptime(race_date_db, "%Y-%m-%d").replace(hour=int(m.group(1)), minute=int(m.group(2)))
        except Exception:
            return None
        return (race_dt - now_utc).total_seconds() / 60.0

    def due_horizons(self, time_display: str, race_date_db: str, now_utc: Optional[datetime] = None) -> List[str]:
        """Horizons qui DOIVENT être verrouillés maintenant pour cette course.
        - T_MATIN est toujours dû (verrouillé à la première vue de la course).
        - T90/T30/T15 deviennent dus quand on entre dans leur fenêtre temporelle.
        - Pour une date passée (rattrapage/backfill), tous les horizons sont dus
          afin de conserver le comportement historique.
        """
        if now_utc is None:
            now_utc = datetime.utcnow()
        today_db = now_utc.strftime("%Y-%m-%d")

        # INTÉGRITÉ ÉDITORIALE : aucun verrou rétroactif, jamais.
        # Un pronostic posé après le départ (avec les cotes finales) serait
        # faussement « prophétique ». Si une passe a été manquée, l'horizon
        # reste vide — c'est visible et honnête.
        if race_date_db != today_db:
            return []

        diff = self.minutes_to_start(time_display, race_date_db, now_utc)
        if diff is not None and diff <= 0:
            return []  # course partie : plus aucun verrouillage

        due = []
        # T_MATIN = édition du MATIN : posée à partir de 06:30 GMT le jour de la
        # course. Le programme PMU apparaît dès la veille au soir, mais les cotes
        # n'ouvrent vraiment que le matin — verrouiller pendant la nuit figerait
        # des cotes par défaut (15.0) et fausserait le signal Smart Money.
        if now_utc.hour > 6 or (now_utc.hour == 6 and now_utc.minute >= 30):
            due.append("T_MATIN")

        if diff is None:
            return due
        for horizon, window in self.HORIZON_WINDOWS:
            if diff <= window:
                if "T_MATIN" not in due:
                    due.append("T_MATIN")  # jamais d'horizon live sans référence matin
                due.append(horizon)
        return due

    def _lock_horizon(self, race_data: Dict[str, Any], runners: List[Dict[str, Any]], horizon: str) -> int:
        """Verrouille les 4 moteurs pour un horizon donné, UNE SEULE FOIS.
        Retourne le nombre de verrous réellement posés (0 si déjà verrouillé).
        Le pronostic est calculé avec les cotes du moment => chaque horizon
        capture un état de marché différent, sans jamais écraser le précédent."""
        race_id = race_data["race_id"]
        if self.db.has_prediction(race_id, "NEW_VALUE_ENGINE", horizon):
            return 0

        # ── VERROU DE FRAÎCHEUR (porte de données, Axe 3) ────────────────
        # Aucun horizon n'est verrouillé sur une édition dont les cotes ne
        # sont pas réelles : il faut ≥ 90 % de partants actifs cotés (≠ 15.0).
        # La règle 06h30 de due_horizons() reste le PLANCHER horaire ; cette
        # porte s'ajoute par-dessus. Refus => l'horizon revient à la passe
        # suivante (due_horizons le représente tant que la fenêtre est ouverte).
        ratio = priced_ratio(runners)
        now_utc = datetime.utcnow()
        if ratio < MIN_PRICED_RATIO:
            self.gate_refused += 1
            print("GATE_REFUSED " + json.dumps({
                "race_id": race_id, "horizon": horizon,
                "priced_ratio": round(ratio, 3), "now_utc": now_utc.isoformat()
            }))
            return 0
        lock_time_utc = now_utc.isoformat()

        # F2 — enrichissement des partants avec les stats humaines apprises
        # (drivers/entraîneurs/couples) avant tout calcul de pronostic.
        if self._human_book is None:
            self._human_book = HumanStatsBook(self.db)
        self._human_book.enrich_runners(runners)

        engines = [
            ("NEW", self.new_engine),
            ("ETPE", self.etpe_engine),
            ("MARKET", self.market_engine),
        ]
        for key, eng in engines:
            p = eng.predict(race_data, runners)
            p["prediction_id"] = f"{race_id}_{key}_{horizon}"
            p["race_id"] = race_id
            p["horizon"] = horizon
            # Preuve de fraîcheur persistée avec l'édition (immuable).
            p["odds_real"] = True
            p["priced_ratio"] = round(ratio, 4)
            p["lock_time_utc"] = lock_time_utc
            self.db.save_prediction(p)

        # Photographie des cotes au moment du verrouillage (historique permanent)
        odds_map = {r["num"]: r.get("odds_t15", r.get("final_odds")) for r in runners}
        self.db.save_odds_snapshots(race_id, horizon, odds_map)
        return 1

    def _lock_radar(self, race_data: Dict[str, Any], runners: List[Dict[str, Any]], horizon: str,
                    now_utc: Optional[datetime] = None) -> int:
        """Verrouille le moteur RADAR_V4 (probabilités scellées du journal Radar)
        pour un horizon, UNE SEULE FOIS, aux mêmes cotes et au même instant que
        les trois autres moteurs. Retourne 1 si un verrou a été posé, sinon 0.

        - Pont désactivé (config absente) → 0, rien ne change.
        - Même porte de fraîcheur : priced_ratio ≥ MIN_PRICED_RATIO, sinon
          GATE_REFUSED (engine RADAR_V4) et retour à la passe suivante.
        - Ligne Radar absente / couverture < 90 % / scelle_a postérieur au verrou
          → RADAR_ABSENT, rien n'est enregistré (l'édition du soir arrive à une
          passe suivante). Jamais de verrou rétroactif (due_horizons décide)."""
        if not self.radar_engine.enabled:
            return 0
        race_id = race_data["race_id"]
        if self.db.has_prediction(race_id, RADAR_ENGINE, horizon):
            return 0
        if now_utc is None:
            now_utc = datetime.utcnow()
        ratio = priced_ratio(runners)
        if ratio < MIN_PRICED_RATIO:
            self.gate_refused_radar += 1
            print("GATE_REFUSED " + json.dumps({
                "race_id": race_id, "horizon": horizon, "engine": RADAR_ENGINE,
                "priced_ratio": round(ratio, 3), "now_utc": now_utc.isoformat()
            }))
            return 0
        p = self.radar_engine.predict(race_data, runners, as_of_utc=now_utc)
        meta = p.get("metadata", {}) or {}
        if meta.get("status") != "OK" or not p.get("selection"):
            self.radar_absent += 1
            print("RADAR_ABSENT " + json.dumps({
                "race_id": race_id, "horizon": horizon,
                "coverage": meta.get("coverage"), "reason": meta.get("reason"),
                "now_utc": now_utc.isoformat()
            }))
            return 0
        p["prediction_id"] = f"{race_id}_RADAR_{horizon}"
        p["race_id"] = race_id
        p["horizon"] = horizon
        p["odds_real"] = True
        p["priced_ratio"] = round(ratio, 4)
        p["lock_time_utc"] = now_utc.isoformat()
        self.db.save_prediction(p)
        return 1

    def inject_recent_real_meetings(self):
        """Désactivé : plus aucune donnée fictive n'est injectée (transparence)."""
        return None

    def _fetch_official_rapports(self, date_str_api: str, r_num: int, c_num: int) -> List[Dict[str, Any]]:
        """Récupère les dividendes définitifs OFFICIELS du flux PMU (jamais estimés).

        Structure réelle de l'API /rapports-definitifs :
            [ { "typePari": "SIMPLE_GAGNANT",
                "rapports": [ { "dividendePourUnEuro": 720, "combinaison": "11", ... } ] },
              ... ]
        (le dividende est en centimes pour 1 EUR de mise : 720 => 7,20 EUR).
        L'ancien lecteur cherchait 'dividende' au premier niveau => toujours vide,
        ce qui déclenchait l'estimation. Corrigé ici, avec repli sur l'ancien
        format à plat par sécurité."""
        rapports: List[Dict[str, Any]] = []
        rap_data = self.fetcher.fetch_rapports(date_str_api, r_num, c_num)
        if not rap_data:
            return rapports

        rap_list = rap_data if isinstance(rap_data, list) else rap_data.get("rapports", [])
        for rp in rap_list:
            if not isinstance(rp, dict):
                continue
            t = rp.get("typePari", "")
            if not t:
                continue
            nested = rp.get("rapports")
            if isinstance(nested, list):
                # Format officiel : liste de combinaisons par type de pari
                for line in nested:
                    if not isinstance(line, dict):
                        continue
                    raw_div = line.get("dividendePourUnEuro", line.get("dividende", 0.0)) or 0.0
                    div = float(raw_div) / 100.0
                    comb = str(line.get("combinaison", ""))
                    if div > 0 and comb:
                        rapports.append({"bet_type": t, "combination": comb, "dividend": div})
            else:
                # Repli : ancien format à plat
                div = float(rp.get("dividende", 0.0) or 0.0) / 100.0
                comb = str(rp.get("combinaison", ""))
                if div > 0 and comb:
                    rapports.append({"bet_type": t, "combination": comb, "dividend": div})
        return rapports

    # ------------------------------------------------------------------
    # Résultats : lecture qualifiée + versionnage (points 2, 3, 4)
    # ------------------------------------------------------------------

    def _url_programme(self, date_str_api: str) -> str:
        fn = getattr(self.fetcher, "url_programme", None)
        return fn(date_str_api) if callable(fn) else f"{PMUDataFetcher.BASE_URL}/{date_str_api}"

    def _url_course(self, date_str_api: str, r_num: int, c_num: int) -> str:
        fn = getattr(self.fetcher, "url_course", None)
        return fn(date_str_api, r_num, c_num) if callable(fn) else f"{PMUDataFetcher.BASE_URL}/{date_str_api}/R{r_num}/C{c_num}"

    def _track_result(self, race_id: str, reading: ArrivalReading, source_url: str,
                      stats: Dict[str, int], rapports: Optional[List[Dict[str, Any]]] = None,
                      now_utc: Optional[datetime] = None) -> Dict[str, Any]:
        """Enregistre une lecture d'arrivée avec versionnage et journalise
        chaque changement d'état (``RESULT_*``). Ne lève jamais."""
        now_utc = now_utc or datetime.utcnow()
        if reading.errors:
            stats["results_rejected"] = stats.get("results_rejected", 0) + 1
            print("RESULT_REJECTED " + json.dumps({
                "race_id": race_id, "statut": reading.statut, "errors": reading.errors,
                "now_utc": now_utc.isoformat()}, ensure_ascii=False))
            return {"action": "REJECTED"}
        res = self.db.record_result(race_id, reading, source_url=source_url, rapports=rapports, now_utc=now_utc)
        action = res.get("action")
        if action == "INITIAL":
            stats["results_resolved"] += 1
            key = "results_definitifs" if res["statut"] == STATUT_DEFINITIVE else "results_provisoires"
            stats[key] = stats.get(key, 0) + 1
            print("RESULT_RECORDED " + json.dumps({
                "race_id": race_id, "statut": res["statut"], "version": 1, "source": reading.source,
                "pmu_statut": reading.pmu_statut, "classes": len(reading.ranking),
                "incidents": len(reading.incidents), "now_utc": now_utc.isoformat()}))
        elif action == "VERSION":
            stats["results_resolved"] += 1
            if res["reason"] == "CORRECTION_CLASSEMENT":
                stats["results_corrections"] = stats.get("results_corrections", 0) + 1
                print("RESULT_CORRECTED " + json.dumps({
                    "race_id": race_id, "version": res["version"], "statut": res["statut"],
                    "source": reading.source, "pmu_statut": reading.pmu_statut, "now_utc": now_utc.isoformat()}))
            else:
                stats["results_updated"] = stats.get("results_updated", 0) + 1
                print("RESULT_UPDATED " + json.dumps({
                    "race_id": race_id, "version": res["version"], "statut": res["statut"], "reason": res["reason"],
                    "source": reading.source, "pmu_statut": reading.pmu_statut, "now_utc": now_utc.isoformat()}))
        elif action == "ANNULATION":
            stats["results_annulations"] = stats.get("results_annulations", 0) + 1
            print("RESULT_CANCELLED " + json.dumps({"race_id": race_id, "version": res["version"],
                                                    "pmu_statut": reading.pmu_statut, "now_utc": now_utc.isoformat()}))
        elif action == "DIVERGENCE_PROVISOIRE":
            print("RESULT_DIVERGENCE " + json.dumps({
                "race_id": race_id, "version": res["version"], "detail": res.get("reason"),
                "source": reading.source, "now_utc": now_utc.isoformat()}))
        return res

    def verify_results(self, target_date: Optional[datetime] = None) -> Dict[str, int]:
        """Re-vérification des arrivées d'une date à partir du SEUL programme
        du jour (une requête) : passage provisoire → définitive, compléments
        de classement, corrections, annulations. Aucun pronostic, aucune cote
        n'est touché. Utilisé par ``--action verify`` et par les ancres 08h30 /
        21h30 via sync_date (les courses gelées y passent aussi par ici)."""
        if target_date is None:
            target_date = datetime.now()
        date_str_api = target_date.strftime("%d%m%Y")
        stats = {"results_checked": 0, "results_resolved": 0}
        secure_http.STATS.reset()
        programme = self.fetcher.fetch_programme(date_str_api)
        if not programme or not isinstance(programme, dict) or "programme" not in programme:
            stats.update(secure_http.STATS.as_dict())
            return stats
        src_url = self._url_programme(date_str_api)
        for r in programme.get("programme", {}).get("reunions", []):
            r_num = r.get("numOfficiel", 1)
            hippo = r.get("hippodrome", {}).get("libelleCourt", "HIPPO")
            for c in r.get("courses", []):
                c_num = c.get("numOrdre", 1)
                race_id = f"R{r_num}C{c_num}_{date_str_api}_{hippo}"
                race = self.db.get_race(race_id)
                if not race:
                    continue
                active = [x["num"] for x in self.db.get_runners(race_id) if not x.get("is_non_partant")]
                reading = read_arrival(c, None, active or None, source=SOURCE_PROGRAMME)
                stats["results_checked"] += 1
                if reading.statut in (STATUT_PROVISOIRE, STATUT_DEFINITIVE, STATUT_ANNULEE):
                    self._track_result(race_id, reading, src_url, stats)
        stats.update(secure_http.STATS.as_dict())
        return stats

    def sync_date(self, target_date: Optional[datetime] = None) -> Dict[str, int]:
        if target_date is None:
            target_date = datetime.now()

        date_str_api = target_date.strftime("%d%m%Y")
        date_str_db = target_date.strftime("%Y-%m-%d")
        now_utc = datetime.utcnow()

        stats = {"races_added": 0, "predictions_locked": 0, "results_resolved": 0, "np_detected": 0, "races_frozen": 0, "gate_refused": 0,
                 "radar_locked": 0, "radar_absent": 0, "gate_refused_radar": 0,
                 "results_provisoires": 0, "results_definitifs": 0, "results_updated": 0, "results_corrections": 0,
                 "results_rejected": 0, "tls_errors": 0}
        self.gate_refused = 0
        self.gate_refused_radar = 0
        self.radar_absent = 0
        secure_http.STATS.reset()
        # Une requête Radar par date et par passe (cache vidé à chaque passe).
        self.radar_engine.client.clear_cache()

        programme = self.fetcher.fetch_programme(date_str_api)
        if not programme or not isinstance(programme, dict) or "programme" not in programme:
            stats["tls_errors"] = secure_http.STATS.tls_errors
            return stats
        programme_url = self._url_programme(date_str_api)

        reunions = programme.get("programme", {}).get("reunions", [])

        for r in reunions:
            r_num = r.get("numOfficiel", 1)
            hippo = r.get("hippodrome", {}).get("libelleCourt", "HIPPO")
            courses = r.get("courses", [])

            # ── PARITÉ LONACI : périmètre abonnés ────────────────────────
            # LONACI (le canal de jeu des abonnés) n'offre que les réunions
            # FRANÇAISES du flux PMU (plus les réunions marocaines, servies
            # par la source SOREC — hors de ce flux). Les réunions
            # étrangères (ESP/GBR/SWE/ARG/CHL…) sont injouables pour les
            # abonnés et souvent sans cotes : elles ne sont plus ingérées.
            # Clause de transition : une réunion hors périmètre dont des
            # courses sont DÉJÀ en base (verrouillées avant ce réglage)
            # reste traitée jusqu'à résolution de ses arrivées — on ne
            # laisse jamais une course « En attente » pour toujours.
            pays_code = str((r.get("pays") or {}).get("code", "FRA")).upper()
            if pays_code not in self.SUBSCRIBER_COUNTRIES:
                first_course_num = courses[0].get("numOrdre", 1) if courses else 1
                probe_id = f"R{r_num}C{first_course_num}_{date_str_api}_{hippo}"
                if not self.db.get_race(probe_id):
                    continue  # réunion hors périmètre, jamais ingérée : ignorée

            for c in courses:
                c_num = c.get("numOrdre", 1)
                race_id = f"R{r_num}C{c_num}_{date_str_api}_{hippo}"
                raw_disc = str(c.get("discipline", "TROT_ATTELE")).upper()
                discipline = self.DISCIPLINE_MAP.get(raw_disc, raw_disc)
                distance = c.get("distance", 2700)
                name = c.get("libelle", f"Prix de {hippo}")
                corde = c.get("corde", "CORDE_A_DROITE")
                rope = "DROITE" if "DROITE" in corde else "GAUCHE"
                autostart = "AUTOSTART" in c.get("specialite", "")

                time_display, start_iso = self.parse_pmu_time(c, date_str_db, c_num)
                if start_iso and not start_iso.endswith("Z") and "+" not in start_iso:
                    start_iso = start_iso + "Z"

                race_data = {
                    "race_id": race_id,
                    "date": date_str_db,
                    "meeting_number": r_num,
                    "race_number": c_num,
                    "name": name,
                    "hippodrome": hippo,
                    "discipline": discipline,
                    "distance": distance,
                    "track_type": "SABLE" if "TROT" in discipline else "HERBE",
                    "track_condition": "BON",
                    "rope": rope,
                    "autostart": autostart,
                    "scheduled_start_time": time_display,
                    "status": "SCHEDULED",
                    # Identité de course enrichie (export résultats)
                    "start_time_utc": start_iso,
                    "pmu_statut": str(c.get("statut") or "") or None,
                    "declared_runners": c.get("nombreDeclaresPartants"),
                }

                # 0. Course déjà clôturée en base => archive GELÉE.
                # On ne retouche plus jamais ni les partants, ni les cotes,
                # ni les pronostics d'une course terminée (persistance définitive).
                existing_race = self.db.get_race(race_id)
                if existing_race and existing_race.get("status") in ("FINISHED", "ANNULEE"):
                    stats["races_frozen"] += 1
                    # Exception 1 au gel : SUIVI DES CORRECTIONS (point 3). L'objet
                    # course du programme — déjà téléchargé, aucune requête de
                    # plus — est relu à chaque passe : complément de classement,
                    # correction après réclamation, annulation. Chaque changement
                    # crée une version, l'ancienne reste dans l'historique.
                    active_prev = [x["num"] for x in self.db.get_runners(race_id) if not x.get("is_non_partant")]
                    reading_prev = read_arrival(c, None, active_prev or None, source=SOURCE_PROGRAMME)
                    has_reading = reading_prev.statut in (STATUT_PROVISOIRE, STATUT_DEFINITIVE) and bool(reading_prev.ranking)
                    if has_reading or reading_prev.statut == STATUT_ANNULEE:
                        self._track_result(race_id, reading_prev, programme_url, stats, now_utc=now_utc)
                    # Exception 2 au gel : compléter les dividendes OFFICIELS
                    # s'ils manquent encore (publiés en léger différé par le PMU).
                    # PLAFONNÉ à 8 tentatives par passe : certaines courses
                    # étrangères n'ont jamais de dividendes, et des dizaines
                    # d'appels à 10s de timeout chacun allongeaient les runs
                    # jusqu'à engorger la file d'attente. Le reste attend la
                    # passe suivante.
                    if stats.get("rapports_backfill_attempts", 0) < 8 and not self.db.has_rapports(race_id):
                        stats["rapports_backfill_attempts"] = stats.get("rapports_backfill_attempts", 0) + 1
                        late_rapports = self._fetch_official_rapports(date_str_api, r_num, c_num)
                        if late_rapports:
                            self.db.save_rapports(race_id, late_rapports)
                            stats["rapports_backfilled"] = stats.get("rapports_backfilled", 0) + 1
                    continue
                if existing_race and existing_race.get("status"):
                    race_data["status"] = existing_race["status"]

                # 1. Fetch participants
                part_data = self.fetcher.fetch_participants(date_str_api, r_num, c_num)
                if not part_data or not isinstance(part_data, dict) or "participants" not in part_data:
                    continue

                runners = []

                for p in part_data["participants"]:
                    p_num = p.get("numPmu", 1)
                    p_name = p.get("nom", f"Cheval_{p_num}")
                    music = p.get("musique", "")
                    driver = p.get("driver", "")
                    trainer = p.get("entraineur", "")

                    statut = str(p.get("statut", "")).upper()
                    is_np = statut in ("NON_PARTANT", "NP", "FORFAIT") or bool(p.get("nonPartant", False))
                    if is_np:
                        stats["np_detected"] += 1
                    # NB : le ``statut`` d'un partant vaut PARTANT/NON_PARTANT ; les
                    # disqualifications sont lues dans ``incidents`` (course) et
                    # ``incident`` (partant) par results_reader — point 4.

                    # Cote de référence OFFICIELLE du flux PMU (le champ réel est
                    # 'dernierRapportReference' — l'ancien 'rapportReference'
                    # n'existe pas dans le flux), repli sur la première cote
                    # directe vue, sinon défaut neutre 15.0.
                    ref_obj = p.get("dernierRapportReference") or p.get("rapportReference") or {}
                    live_obj = p.get("dernierRapportDirect") or {}
                    live_odds = float(live_obj.get("rapport", 0.0) or 0.0)
                    m_odds = float(ref_obj.get("rapport", 0.0) or 0.0)
                    if m_odds <= 0:
                        m_odds = live_odds if live_odds > 0 else 15.0
                    if live_odds <= 0:
                        live_odds = m_odds

                    # Capteurs RÉELS du flux (remplacent les constantes 74.0/34.0/5) :
                    # - chrono trot : reductionKilometrique en millisecondes (78300 = 78,3 s/km)
                    red_km = float(p.get("reductionKilometrique", 0.0) or 0.0)
                    record_chrono = round(red_km / 1000.0, 2) if red_km > 1000 else red_km
                    # - valeur officielle handicap (galop)
                    official_rating = float(p.get("handicapValeur", 0.0) or 0.0)
                    # - poids en kg : le flux livre des hectogrammes (585 = 58,5 kg)
                    poids_raw = float(p.get("poidsConditionMonte") or p.get("handicapPoids") or 0.0)
                    if poids_raw > 200:
                        poids_raw = poids_raw / 10.0
                    weight_kg = poids_raw if poids_raw > 0 else 60.0
                    # - gains carrière réels (objet gainsParticipant, en centimes)
                    gains_obj = p.get("gainsParticipant") or {}
                    earnings_eur = float(gains_obj.get("gainsCarriere", p.get("gainsCarriere", 0.0)) or 0.0) / 100.0

                    shoeing = p.get("deferre", "FERRE")
                    if shoeing == "DEFERRE_ANTERIEURS_POSTERIEURS":
                        shoeing_code = "D4"
                    elif shoeing == "DEFERRE_POSTERIEURS":
                        shoeing_code = "DP"
                    elif shoeing == "DEFERRE_ANTERIEURS":
                        shoeing_code = "DA"
                    else:
                        shoeing_code = "FERRE"

                    runners.append({
                        "num": p_num,
                        "horse_name": p_name,
                        "sex": p.get("sexe", "M"),
                        "age": p.get("age", 5),
                        "driver_jockey": driver,
                        "trainer": trainer,
                        "weight": weight_kg,
                        "draw": p.get("placeCorde", p_num),
                        "shoeing": shoeing_code,
                        "blinkers": p.get("oeilleres", "SANS"),
                        "morning_odds": m_odds,
                        "odds_t15": live_odds,
                        "final_odds": live_odds,
                        "is_non_partant": is_np,
                        # Presse : AUCUNE donnée réelle branchée -> 0 (jamais de
                        # constante fictive ; le moteur redistribue ce poids).
                        "press_citation_count": 0,
                        "music": music,
                        "earnings": earnings_eur,
                        "record_chrono": record_chrono,
                        "official_rating": official_rating
                    })

                if not runners:
                    continue

                # 1bis. Préservation des cotes archivées (jamais écrasées) :
                #  - morning_odds : figée à la première capture de la course
                #  - odds_t15    : figée dès que l'horizon T15 est verrouillé
                if existing_race:
                    existing_runners = {r["num"]: r for r in self.db.get_runners(race_id)}
                    t15_locked = "T15" in self.db.get_locked_horizons(race_id)
                    for r in runners:
                        prev = existing_runners.get(r["num"])
                        if prev:
                            prev_matin = prev.get("morning_odds")
                            # 15.0 est la valeur PAR DÉFAUT (marché fermé, cote
                            # inconnue) : la cote du matin n'est figée qu'une
                            # fois une VRAIE valeur capturée.
                            if prev_matin is not None and float(prev_matin) != 15.0:
                                r["morning_odds"] = prev_matin
                            if t15_locked and prev.get("odds_t15") is not None:
                                r["odds_t15"] = prev["odds_t15"]

                self.db.save_race(race_data)
                self.db.save_runners(race_id, runners)
                stats["races_added"] += 1

                # 2. Verrouillage par fenêtres temporelles réelles.
                # T_MATIN est posé à la première vue de la course ; T90, T30 puis
                # T15 sont posés uniquement quand leur fenêtre est atteinte, avec
                # les cotes du moment. Un horizon verrouillé n'est JAMAIS recalculé.
                due = self.due_horizons(time_display, date_str_db, now_utc)
                for h in due:
                    stats["predictions_locked"] += self._lock_horizon(race_data, runners, h)
                    # 4e moteur (pont Radar) : indépendant du résultat ci-dessus,
                    # une seule fois par (course, horizon), même porte de fraîcheur.
                    stats["radar_locked"] += self._lock_radar(race_data, runners, h, now_utc)
                stats["gate_refused"] = self.gate_refused
                stats["gate_refused_radar"] = self.gate_refused_radar
                stats["radar_absent"] = self.radar_absent

                # 3. Arrivée officielle (points 2 et 4) : lecture QUALIFIÉE.
                # Source primaire : l'objet course du programme (statut,
                # drapeau arriveeDefinitive, ordreArrivee en listes de listes,
                # incidents). Repli : l'endpoint course, puis l'ordreArrivee des
                # partants (=> PROVISOIRE au mieux). Une arrivée n'est
                # DEFINITIVE que si le flux le dit ; la course n'est gelée
                # (FINISHED) qu'à ce moment-là. Tout changement ultérieur est
                # versionné (point 3).
                active_nums = [x["num"] for x in runners if not x.get("is_non_partant")]
                reading = read_arrival(c, part_data["participants"], active_nums, source=SOURCE_PROGRAMME)
                source_url = programme_url
                if reading.statut in (STATUT_PROVISOIRE, STATUT_DEFINITIVE) and (not reading.ranking or reading.errors):
                    # Programme incomplet ou périmé (cache) : une requête ciblée.
                    course_info = self.fetcher.fetch_course_info(date_str_api, r_num, c_num)
                    if course_info and isinstance(course_info, dict):
                        reading2 = read_arrival(course_info, part_data["participants"], active_nums, source=SOURCE_COURSE)
                        if reading2.ranking and not reading2.errors:
                            reading, source_url = reading2, self._url_course(date_str_api, r_num, c_num)

                if reading.statut == STATUT_ANNULEE:
                    self._track_result(race_id, reading, source_url, stats, now_utc=now_utc)
                elif reading.statut in (STATUT_PROVISOIRE, STATUT_DEFINITIVE) and reading.ranking:
                    # UNIQUEMENT les dividendes OFFICIELS PMU. Aucun dividende
                    # n'est estimé/inventé : sans rapports officiels, la course
                    # reste dans l'historique et les taux de réussite mais est
                    # exclue du calcul de ROI (transparence éditoriale).
                    rapports = None
                    if reading.statut == STATUT_DEFINITIVE or not self.db.get_result(race_id):
                        rapports = self._fetch_official_rapports(date_str_api, r_num, c_num)
                    self._track_result(race_id, reading, source_url, stats, rapports=rapports, now_utc=now_utc)

        stats["tls_errors"] = secure_http.STATS.tls_errors
        return stats
