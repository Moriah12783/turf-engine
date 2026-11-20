"""Tentatives de verrou sous le seuil de diffusion : verrous_tentatives et verrous_refus.

Spécification : docs/VERROUS_TENTATIVES.md (version 3, retouches du dev Radar
et du dev NVE du 20/10/2026). Décision de Steph : le labo écrit le code, le dev
Radar et le dev NVE relisent.

  - PARTIEL : verrou posé avec une part cotée d'au moins MIN_LOCK_RATIO et de
    moins de MIN_PRICED_RATIO ; ligne écrite au verrou, après lui, dans
    ``_lock_horizon`` (``enregistrer_partiel``).
  - Journal ``verrous_refus`` : une ligne par passage refusé par la porte de
    verrou, dans la branche GATE_REFUSED de ``_lock_horizon``
    (``journaliser_refus``).
  - REFUSE : calculé à chaque passage pour les seules courses fermées
    (``FINISHED``, ou plus datées du jour UTC), jamais pour une course
    ``ANNULEE`` (``ecrire_refuses``). Passage retenu : la part cotée la plus
    haute avant le départ, la plus ancienne en cas d'égalité ; seuils et
    ``gate_version`` de ce passage.
  - Rapport quotidien committé ``rapports/verrous/AAAA-MM-JJ.md`` pour la
    veille : comptes par horizon, liste « non observée » par motif,
    HORS_PROGRAMME, contrôles C1 à C5 avec les couples en défaut. Comptes et
    identifiants seulement : aucune probabilité, rien de l'ombre.

Enregistrement pur : aucune erreur d'écriture ne remonte au verrou ni au
passage (VERROU_TENTATIVE_ERROR, VERROU_REFUS_ERROR). Aucune règle de verrou,
de diffusion ni de moteur ne change. Seules les courses datées à partir de
MISE_EN_SERVICE sont suivies : aucune ligne n'est reconstituée pour le passé.
"""

import json
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from turf_lab.odds_quality import MIN_LOCK_RATIO, MIN_PRICED_RATIO

# Premier jour de course suivi : le lendemain de la fusion (ligne datée au dev
# Bases avant toute publication). Une course d'un jour antérieur n'a ni ligne
# ni motif « non observée ».
MISE_EN_SERVICE = "2026-11-21"
HORIZONS = ("T_MATIN", "T90", "T30", "T15")
ENGINE = "NEW_VALUE_ENGINE"
# Seuils annoncés avec la spécification : un test échoue s'ils changent sans
# nouvelle annonce (comme test_empreinte_de_la_porte pour la porte de diffusion).
SEUILS_ANNONCES = (0.50, 0.90)
DOSSIER_RAPPORTS = os.path.join("rapports", "verrous")
MOTIFS_NON_OBSERVEE = ("ANNULEE", "HEURE_INCONNUE", "AUCUN_PASSAGE")
_HEURE = re.compile(r"(\d{1,2})[:h](\d{2})")


def _log(tag: str, payload: Dict[str, Any]) -> None:
    print(tag + " " + json.dumps(payload, ensure_ascii=False, sort_keys=True))


def _naif(instant: datetime) -> datetime:
    if instant.tzinfo is None:
        return instant
    return instant.astimezone(timezone.utc).replace(tzinfo=None)


def _gate_version() -> str:
    from turf_lab.publication_gate import GATE_VERSION   # import tardif : aucun cycle
    return GATE_VERSION


def suivie(race_date: Any) -> bool:
    """True si la course est datée à partir de MISE_EN_SERVICE (AAAA-MM-JJ)."""
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", str(race_date or ""))) and str(race_date) >= MISE_EN_SERVICE


def depart_utc(race: Dict[str, Any]) -> Optional[datetime]:
    """Heure de départ UTC sans fuseau, telle que ``races`` la donne au moment
    du calcul : ``start_time_utc`` du flux, sinon l'heure GMT affichée."""
    brut = race.get("start_time_utc")
    if brut:
        try:
            return _naif(datetime.fromisoformat(str(brut).replace("Z", "+00:00")))
        except ValueError:
            pass
    m = _HEURE.search(str(race.get("scheduled_start_time") or ""))
    if not m:
        return None
    try:
        d = date.fromisoformat(str(race.get("date") or ""))
        return datetime(d.year, d.month, d.day, int(m.group(1)), int(m.group(2)))
    except ValueError:
        return None


# ── Écritures au verrou (appelées par _lock_horizon) ─────────────────────
def journaliser_refus(db, race_data: Dict[str, Any], horizon: str, ratio: float,
                      compte: Tuple[int, int], now_utc: datetime) -> bool:
    """Un passage refusé par la porte de verrou : une ligne au journal. Jamais
    d'erreur remontée : le passage continue comme avant."""
    try:
        if not suivie(race_data.get("date")):
            return False
        ligne = {"race_id": race_data.get("race_id"), "horizon": horizon,
                 "observed_at_utc": _naif(now_utc).isoformat(), "part_cotee": round(float(ratio), 4),
                 "n_partants": int(compte[0]), "n_cotes": int(compte[1]),
                 "min_lock_ratio": MIN_LOCK_RATIO, "min_priced_ratio": MIN_PRICED_RATIO,
                 "gate_version": _gate_version()}
        return bool(db.save_verrou_refus(ligne))
    except Exception as exc:
        _log("VERROU_REFUS_ERROR", {"race_id": (race_data or {}).get("race_id"), "horizon": horizon,
                                    "erreur": str(exc)[:200]})
        return False


def enregistrer_partiel(db, race_data: Dict[str, Any], horizon: str, ratio: float,
                        compte: Tuple[int, int], lock_time_utc: str) -> bool:
    """Verrou posé sous le seuil de diffusion : ligne PARTIEL, écrite après le
    verrou. Même part que ``predictions.priced_ratio``, même instant que
    ``lock_time_utc``. Une erreur est journalisée et n'annule jamais le verrou."""
    try:
        if not suivie(race_data.get("date")) or ratio >= MIN_PRICED_RATIO or ratio < MIN_LOCK_RATIO:
            return False
        ligne = {"race_id": race_data.get("race_id"), "horizon": horizon, "issue": "PARTIEL",
                 "part_cotee": round(float(ratio), 4), "n_partants": int(compte[0]), "n_cotes": int(compte[1]),
                 "decided_at_utc": lock_time_utc, "heure_max_utc": lock_time_utc,
                 "min_lock_ratio": MIN_LOCK_RATIO, "min_priced_ratio": MIN_PRICED_RATIO,
                 "gate_version": _gate_version()}
        ajoutee = bool(db.save_verrou_tentative(ligne))
        if ajoutee:
            _log("VERROU_TENTATIVE", _publiable(ligne))
        return ajoutee
    except Exception as exc:
        _log("VERROU_TENTATIVE_ERROR", {"race_id": (race_data or {}).get("race_id"), "horizon": horizon,
                                        "issue": "PARTIEL", "erreur": str(exc)[:200]})
        return False


def _publiable(ligne: Dict[str, Any]) -> Dict[str, Any]:
    """Ligne de journal : comptes et parts seulement."""
    return {k: ligne[k] for k in ("race_id", "horizon", "issue", "part_cotee", "n_partants", "n_cotes",
                                  "decided_at_utc", "heure_max_utc", "gate_version")}


# ── REFUSE (règle 3 : à chaque passage, courses fermées seulement) ──────
def _fermee(race: Dict[str, Any], aujourd_hui: str) -> bool:
    """Course qui ne peut plus être verrouillée : FINISHED, ou plus datée du
    jour UTC. Jamais une course ANNULEE (liste « non observée »)."""
    statut = str(race.get("status") or "").upper()
    if statut == "ANNULEE":
        return False
    return statut == "FINISHED" or str(race.get("date") or "") < aujourd_hui


def passage_retenu(passages: Sequence[Dict[str, Any]], depart: Optional[datetime]) -> Optional[Dict[str, Any]]:
    """Règle 1 : parmi les passages antérieurs au départ (tous si le départ est
    inconnu), la part cotée la plus haute ; en cas d'égalité, le plus ancien."""
    avant = [p for p in passages
             if depart is None or datetime.fromisoformat(str(p["observed_at_utc"])) < depart]
    if not avant:
        return None
    return sorted(avant, key=lambda p: (-float(p["part_cotee"]), str(p["observed_at_utc"]), int(p["id"])))[0]


def ecrire_refuses(db, now_utc: Optional[datetime] = None) -> int:
    """Écrit les REFUSE des courses fermées : au moins un passage au journal,
    aucune édition NVE, pas encore de ligne. Rendu identique quel que soit le
    moment du calcul ; la clé unique ignore les répétitions. Retourne le nombre
    de lignes ajoutées. Jamais d'erreur remontée."""
    now = _naif(now_utc or datetime.utcnow())
    aujourd_hui = now.date().isoformat()
    ajoutees = 0
    try:
        with db.transaction() as conn:
            candidats = conn.execute("""
                SELECT DISTINCT j.race_id, j.horizon FROM verrous_refus j
                WHERE NOT EXISTS (SELECT 1 FROM verrous_tentatives v
                                  WHERE v.race_id = j.race_id AND v.horizon = j.horizon)
                  AND NOT EXISTS (SELECT 1 FROM predictions p WHERE p.race_id = j.race_id
                                  AND p.horizon = j.horizon AND p.engine_name = ?)
                ORDER BY j.race_id, j.horizon""", (ENGINE,)).fetchall()
            courses = {}
            passages: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
            for c in candidats:
                rid = c["race_id"]
                if rid not in courses:
                    row = conn.execute("SELECT * FROM races WHERE race_id = ?", (rid,)).fetchone()
                    courses[rid] = dict(row) if row else None
                passages[(rid, c["horizon"])] = [dict(r) for r in conn.execute(
                    "SELECT * FROM verrous_refus WHERE race_id = ? AND horizon = ? ORDER BY id",
                    (rid, c["horizon"]))]
    except Exception as exc:
        _log("VERROU_TENTATIVE_ERROR", {"etape": "REFUSE", "erreur": str(exc)[:200]})
        return 0
    for (rid, horizon), journal in passages.items():
        race = courses.get(rid)
        if not race or not suivie(race.get("date")) or not _fermee(race, aujourd_hui):
            continue
        retenu = passage_retenu(journal, depart_utc(race))
        if retenu is None:
            continue
        ligne = {"race_id": rid, "horizon": horizon, "issue": "REFUSE",
                 "part_cotee": float(retenu["part_cotee"]), "n_partants": int(retenu["n_partants"]),
                 "n_cotes": int(retenu["n_cotes"]), "decided_at_utc": now.isoformat(),
                 "heure_max_utc": str(retenu["observed_at_utc"]),
                 "min_lock_ratio": float(retenu["min_lock_ratio"]),
                 "min_priced_ratio": float(retenu["min_priced_ratio"]), "gate_version": str(retenu["gate_version"])}
        try:
            if db.save_verrou_tentative(ligne):
                ajoutees += 1
                _log("VERROU_TENTATIVE", _publiable(ligne))
        except Exception as exc:
            _log("VERROU_TENTATIVE_ERROR", {"race_id": rid, "horizon": horizon, "issue": "REFUSE",
                                            "erreur": str(exc)[:200]})
    return ajoutees


# ── Liste « non observée » et contrôles C1 à C5 ─────────────────────────
def _courses(conn, debut: str, fin: str) -> List[Dict[str, Any]]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM races WHERE date >= ? AND date <= ? ORDER BY race_id", (max(debut, MISE_EN_SERVICE), fin))]


def _editions(conn) -> set:
    return {(r["race_id"], r["horizon"]) for r in conn.execute(
        "SELECT race_id, horizon FROM predictions WHERE engine_name = ?", (ENGINE,))}


def _journal(conn, debut: str, fin: str) -> Dict[Tuple[str, str], List[Dict[str, Any]]]:
    """Passages du journal des courses datées de ``debut`` à ``fin``."""
    out: Dict[Tuple[str, str], List[Dict[str, Any]]] = {}
    for r in conn.execute("""
            SELECT j.* FROM verrous_refus j JOIN races r ON r.race_id = j.race_id
            WHERE r.date >= ? AND r.date <= ? ORDER BY j.id""", (max(debut, MISE_EN_SERVICE), fin)):
        out.setdefault((r["race_id"], r["horizon"]), []).append(dict(r))
    return out


def _observe(race: Dict[str, Any], passages: Sequence[Dict[str, Any]]) -> bool:
    """Au moins un passage refusé avant le départ enregistré dans ``races`` (règle
    1) : un REFUSE est alors attendu une fois la course fermée. Sinon l'horizon
    relève de la liste « non observée », jamais absent des deux."""
    return passage_retenu(passages, depart_utc(race)) is not None


def non_observes(db, debut: str, fin: Optional[str] = None) -> List[Dict[str, str]]:
    """Horizons sans édition NVE et sans passage au journal (motif ANNULEE,
    HEURE_INCONNUE ou AUCUN_PASSAGE), plus les horizons sans édition d'une
    course annulée : aucun REFUSE n'y est jamais écrit."""
    fin = fin or debut
    with db.transaction() as conn:
        courses = _courses(conn, debut, fin)
        editions = _editions(conn)
        journal = _journal(conn, debut, fin)
    out = []
    for race in courses:
        for h in HORIZONS:
            cle = (race["race_id"], h)
            if cle in editions:
                continue
            if str(race.get("status") or "").upper() == "ANNULEE":
                motif = "ANNULEE"
            elif _observe(race, journal.get(cle, [])):
                continue                                   # un REFUSE est attendu (ou en défaut C1)
            elif depart_utc(race) is None:
                motif = "HEURE_INCONNUE"
            else:
                motif = "AUCUN_PASSAGE"                    # aucun passage, ou seulement après le départ
            out.append({"race_id": race["race_id"], "horizon": h, "motif": motif})
    return out


def controles(db, debut: str, fin: str) -> Dict[str, List[Tuple[str, str]]]:
    """C1 à C5 sur les courses datées de ``debut`` à ``fin`` : couples (course,
    horizon) en défaut. Zéro attendu partout."""
    d, f = max(debut, MISE_EN_SERVICE), fin
    with db.transaction() as conn:
        def q(sql: str, *params) -> List[Tuple[str, str]]:
            return sorted({(r[0], r[1]) for r in conn.execute(sql, params)})
        # C1, en Python avec la règle 1 : un horizon sans édition NVE, sans
        # REFUSE, d'une course non annulée, et dont un passage refusé précède
        # le départ. Les autres relèvent de la liste « non observée ».
        editions = _editions(conn)
        refuses = {(r["race_id"], r["horizon"]) for r in conn.execute(
            "SELECT race_id, horizon FROM verrous_tentatives WHERE issue = 'REFUSE'")}
        journal = _journal(conn, d, f)
        c1 = sorted((race["race_id"], h) for race in _courses(conn, d, f) for h in HORIZONS
                    if (race["race_id"], h) not in editions and (race["race_id"], h) not in refuses
                    and str(race.get("status") or "").upper() != "ANNULEE"
                    and _observe(race, journal.get((race["race_id"], h), [])))
        c2 = q("""
            SELECT v.race_id, v.horizon FROM verrous_tentatives v
            JOIN races r ON r.race_id = v.race_id
            JOIN predictions p ON p.race_id = v.race_id AND p.horizon = v.horizon AND p.engine_name = ?
            WHERE v.issue = 'REFUSE' AND r.date >= ? AND r.date <= ?""", ENGINE, d, f)
        c3 = q("""
            SELECT p.race_id, p.horizon FROM predictions p
            JOIN races r ON r.race_id = p.race_id
            LEFT JOIN verrous_tentatives v ON v.race_id = p.race_id AND v.horizon = p.horizon
                                          AND v.issue = 'PARTIEL'
            WHERE p.engine_name = ? AND p.odds_real = 0 AND r.date >= ? AND r.date <= ?
              AND (v.race_id IS NULL OR v.part_cotee <> p.priced_ratio OR v.decided_at_utc <> p.lock_time_utc)""",
               ENGINE, d, f)
        c3 += q("""
            SELECT v.race_id, v.horizon FROM verrous_tentatives v
            JOIN races r ON r.race_id = v.race_id
            LEFT JOIN predictions p ON p.race_id = v.race_id AND p.horizon = v.horizon
                                   AND p.engine_name = ? AND p.odds_real = 0
            WHERE v.issue = 'PARTIEL' AND p.race_id IS NULL AND r.date >= ? AND r.date <= ?""", ENGINE, d, f)
        c4 = q("""
            SELECT v.race_id, v.horizon FROM verrous_tentatives v JOIN races r ON r.race_id = v.race_id
            WHERE v.issue = 'REFUSE' AND v.part_cotee >= v.min_lock_ratio AND r.date >= ? AND r.date <= ?""", d, f)
        c5 = q("""
            SELECT v.race_id, v.horizon FROM verrous_tentatives v JOIN races r ON r.race_id = v.race_id
            WHERE v.issue = 'PARTIEL' AND (v.part_cotee < v.min_lock_ratio OR v.part_cotee >= v.min_priced_ratio)
              AND r.date >= ? AND r.date <= ?""", d, f)
    return {"C1": c1, "C2": c2, "C3": sorted(set(c3)), "C4": c4, "C5": c5}


# ── Rapport quotidien ────────────────────────────────────────────────────
def hors_programme(db, programme_ids: Iterable[str]) -> List[str]:
    """Courses du programme PMU (périmètre des passes) absentes de ``races``."""
    with db.transaction() as conn:
        connues = {r[0] for r in conn.execute("SELECT race_id FROM races")}
    return sorted(set(programme_ids) - connues)


def rapport(db, jour: str, programme_ids: Optional[Iterable[str]]) -> str:
    """Rapport de la veille, en Markdown : comptes et identifiants seulement."""
    with db.transaction() as conn:
        lignes = [dict(r) for r in conn.execute("""
            SELECT v.horizon, v.issue FROM verrous_tentatives v JOIN races r ON r.race_id = v.race_id
            WHERE r.date = ?""", (jour,))]
        n_courses = conn.execute("SELECT COUNT(*) FROM races WHERE date = ?", (jour,)).fetchone()[0]
    nobs = non_observes(db, jour)
    ctrl = controles(db, MISE_EN_SERVICE, jour)
    hp = hors_programme(db, programme_ids) if programme_ids is not None else None
    out = [f"# Verrous sous le seuil de diffusion : {jour}", "",
           "Rapport régénéré à chaque passage de `daily_sync` depuis les tables en ajout seul "
           "`verrous_tentatives` et `verrous_refus` (spécification `docs/VERROUS_TENTATIVES.md`). "
           "Comptes et identifiants seulement : aucune probabilité, rien de l'ombre.", "",
           f"- Courses du jour en base : {n_courses}",
           f"- Mise en service : {MISE_EN_SERVICE}", "",
           "## Tentatives par horizon", "", "| Horizon | PARTIEL | REFUSE |", "|---|---|---|"]
    for h in HORIZONS:
        out.append(f"| {h} | {sum(1 for l in lignes if l['horizon'] == h and l['issue'] == 'PARTIEL')} "
                   f"| {sum(1 for l in lignes if l['horizon'] == h and l['issue'] == 'REFUSE')} |")
    out.append(f"| **Total** | {sum(1 for l in lignes if l['issue'] == 'PARTIEL')} "
               f"| {sum(1 for l in lignes if l['issue'] == 'REFUSE')} |")
    out += ["", "## Horizons « non observée »", "", "| Motif | Horizons |", "|---|---|"]
    for motif in MOTIFS_NON_OBSERVEE:
        out.append(f"| {motif} | {sum(1 for n in nobs if n['motif'] == motif)} |")
    if nobs:
        out += [""] + [f"- `{n['race_id']}` {n['horizon']} : {n['motif']}" for n in nobs]
    out += ["", "## Hors programme", ""]
    if hp is None:
        out.append("Programme PMU de la veille indisponible à ce passage.")
    else:
        out.append(f"Courses du programme PMU de la veille (périmètre des passes) absentes de `races` : {len(hp)}")
        out += [f"- `{rid}`" for rid in hp]
    out += ["", f"## Concordance, du {MISE_EN_SERVICE} au {jour} (zéro attendu)", "",
            "| Contrôle | Couples en défaut |", "|---|---|"]
    for nom in ("C1", "C2", "C3", "C4", "C5"):
        out.append(f"| {nom} | {len(ctrl[nom])} |")
    for nom in ("C1", "C2", "C3", "C4", "C5"):
        if ctrl[nom]:
            out += ["", f"{nom} :"] + [f"- `{rid}` {h}" for rid, h in ctrl[nom]]
    return "\n".join(out) + "\n"


def ecrire_rapport(db, jour: str, racine: str, programme_ids: Optional[Iterable[str]]) -> str:
    """Écrit ``rapports/verrous/AAAA-MM-JJ.md`` (le contenu ne change que si les
    tables ont changé : Git ne committe alors rien). Retourne le chemin."""
    dossier = os.path.join(racine, DOSSIER_RAPPORTS)
    os.makedirs(dossier, exist_ok=True)
    chemin = os.path.join(dossier, f"{jour}.md")
    texte = rapport(db, jour, programme_ids)
    ancien = open(chemin, encoding="utf-8").read() if os.path.exists(chemin) else None
    if texte != ancien:
        with open(chemin, "w", encoding="utf-8") as f:
            f.write(texte)
    return chemin


def passage(db, manager, racine: str, now_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """Fin de chaque passage de daily_sync : REFUSE des courses fermées, PUIS
    rapport de la veille (le premier passage du jour UTC écrit donc les REFUSE
    de la veille avant son rapport). Jamais d'erreur remontée au passage."""
    now = _naif(now_utc or datetime.utcnow())
    veille = (now.date() - timedelta(days=1)).isoformat()
    etat: Dict[str, Any] = {"refuse_ecrits": 0, "rapport": None, "veille": veille}
    try:
        etat["refuse_ecrits"] = ecrire_refuses(db, now)
        if veille >= MISE_EN_SERVICE:
            ids = manager.programme_race_ids(datetime.fromisoformat(veille))
            if ids is None:
                etat["rapport"] = "REPORTE_PROGRAMME_INDISPONIBLE"      # rattrapé au passage suivant
            else:
                chemin = ecrire_rapport(db, veille, racine, ids)
                etat["rapport"] = os.path.relpath(chemin, racine)
    except Exception as exc:
        etat["erreur"] = str(exc)[:200]
        _log("VERROUS_ERREUR", etat)
        return etat
    _log("VERROUS_PASSAGE", etat)
    return etat
