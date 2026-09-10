"""Export JSON des résultats — livrable partenaire.

Fichiers écrits dans ``site/resultats/`` (publiés avec le site, donc servis
en HTTPS sur https://prono.elite-turf.fr/resultats/…) :

- ``AAAA-MM-JJ.json`` : toutes les courses de la journée (périmètre ingéré)
  avec, pour chacune, identité, classement structuré, statut, source,
  horodatages et version de correction. Une course sans arrivée figure avec
  ``statut.code = "EN_ATTENTE"`` pour que le consommateur puisse sonder.
- ``index.json`` : manifeste des journées disponibles (compteurs par statut,
  empreinte SHA-256 de chaque fichier, date de génération).

Contrat de stabilité : ``schema_version`` est incrémenté à toute rupture ;
un ajout de champ n'est pas une rupture. Les clés sont en français, les
horodatages en UTC ISO-8601 suffixés ``Z``.
"""

import hashlib
import json
import os
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

SCHEMA_VERSION = "1.1"
FOURNISSEUR = "Elite Turf — prono.elite-turf.fr"
ORIGINE = "PMU (flux public turfinfo, lecture HTTPS vérifiée)"


def version_code() -> Dict[str, Optional[str]]:
    """Identité du code qui a produit l'export (retour partenaire : le manifeste
    doit permettre de certifier la version déployée). Renseigné par GitHub
    Actions ; null en exécution locale."""
    return {
        "commit": os.environ.get("GITHUB_SHA") or None,
        "run_id": os.environ.get("GITHUB_RUN_ID") or None,
        "depot": "Moriah12783/turf-engine",
    }


def _z(ts: Optional[str]) -> Optional[str]:
    """Horodatage UTC normalisé : ISO-8601, secondes, suffixe Z."""
    if not ts:
        return None
    s = str(ts)
    if s.endswith("Z"):
        return s
    if "+" in s[10:]:
        return s
    try:
        dt = datetime.fromisoformat(s)
        return dt.replace(microsecond=0).isoformat() + "Z"
    except Exception:
        return s + "Z"


def _sha256(obj: Any) -> str:
    payload = json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _rapports_block(rapports: List[Dict[str, Any]]) -> Dict[str, Any]:
    sg: Dict[str, float] = {}
    sp: Dict[str, float] = {}
    autres: List[Dict[str, Any]] = []
    for r in rapports:
        t = str(r.get("bet_type") or "").upper()
        comb = str(r.get("combination") or "")
        div = float(r.get("dividend") or 0.0)
        if t == "SIMPLE_GAGNANT":
            sg[comb] = div
        elif t == "SIMPLE_PLACE":
            sp[comb] = div
        else:
            autres.append({"type_pari": t, "combinaison": comb, "dividende_pour_1_eur": div})
    return {
        "disponibles": bool(rapports),
        "unite": "EUR pour 1 EUR de mise",
        "simple_gagnant": sg,
        "simple_place": sp,
        "autres": autres,
    }


def build_course_entry(race: Dict[str, Any], runners: List[Dict[str, Any]],
                       result: Optional[Dict[str, Any]], history: List[Dict[str, Any]],
                       rapports: List[Dict[str, Any]]) -> Dict[str, Any]:
    names = {int(r["num"]): r.get("horse_name") for r in runners}
    non_partants_runners = sorted(int(r["num"]) for r in runners if r.get("is_non_partant"))

    race_status = str(race.get("status") or "SCHEDULED").upper()
    if result:
        code = result["statut"]
    elif race_status == "ANNULEE":
        code = "ANNULEE"
    else:
        code = "EN_ATTENTE"
    finalite = (result or {}).get("finalite") if result else None
    if code == "DEFINITIVE" and finalite not in ("VERIFIEE_PMU", "LEGACY_NON_VERIFIEE"):
        finalite = "VERIFIEE_PMU" if (result or {}).get("verified_at") else "LEGACY_NON_VERIFIEE"
    if code != "DEFINITIVE":
        finalite = None

    classement = []
    if result and code in ("PROVISOIRE", "DEFINITIVE"):
        for r in result["ranking"]:
            classement.append({
                "rang": int(r["rang"]), "num": int(r["num"]),
                "nom": names.get(int(r["num"])), "dead_heat": bool(r.get("dead_heat", False)),
            })
    non_classes = []
    if result:
        for i in result["incidents"]:
            non_classes.append({"num": int(i["num"]), "nom": names.get(int(i["num"])), "incident": str(i["type"])})
    np_set = set(non_partants_runners) | set(int(n) for n in (result["non_partants"] if result else []))
    non_partants = [{"num": n, "nom": names.get(n)} for n in sorted(np_set)]

    historique = []
    for h in history:
        historique.append({
            "version": int(h["version"]), "statut": h["statut"], "raison": h["reason"],
            "horodatage_utc": _z(h["recorded_at"]), "source": h.get("source"),
            "classement": [int(x["num"]) for x in h.get("ranking", [])],
        })

    entry = {
        "course_id": race["race_id"],
        "identite": {
            "date": race.get("date"),
            "reunion": int(race.get("meeting_number") or 0),
            "course": int(race.get("race_number") or 0),
            "code": f"R{race.get('meeting_number')}C{race.get('race_number')}",
            "hippodrome": race.get("hippodrome"),
            "libelle": race.get("name"),
            "discipline": race.get("discipline"),
            "distance_m": race.get("distance"),
            # Nullable : renseignée UNIQUEMENT depuis heureDepart du flux PMU ;
            # jamais dérivée de l'affichage ni d'un horaire standard.
            "heure_depart_utc": _z(race.get("start_time_utc")) if race.get("start_time_utc") else None,
            "heure_depart_source": "PMU_HEUREDEPART" if race.get("start_time_utc") else None,
            "heure_depart_affichee": race.get("scheduled_start_time"),
            "partants_declares": race.get("declared_runners"),
            "partants_actifs": len([r for r in runners if not r.get("is_non_partant")]) if runners else None,
        },
        "statut": {
            "code": code,
            "definitive": code == "DEFINITIVE",
            # VERIFIEE_PMU : finalité vue par le nouveau lecteur (drapeau PMU) ;
            # LEGACY_NON_VERIFIEE : arrivée tenue pour définitive par l'ancien
            # système, jamais re-vérifiée — à exclure de toute preuve prospective.
            "finalite": finalite,
            "annulee": code == "ANNULEE",
            "pmu_statut": (result or {}).get("pmu_statut") or race.get("pmu_statut"),
        },
        "classement": classement,
        "arrivee": [c["num"] for c in classement],
        "non_classes": non_classes,
        "non_partants": non_partants,
        "rapports": _rapports_block(rapports),
        "source": {
            "fournisseur": FOURNISSEUR,
            "origine": ORIGINE,
            "canal": (result or {}).get("source"),
            "url_origine": (result or {}).get("source_url"),
        },
        "horodatages": {
            "premiere_lecture_utc": _z((result or {}).get("first_seen_at")),
            # Finalité datée UNIQUEMENT par une lecture vérifiée (null pour une
            # ligne héritée non re-vérifiée) — jamais antidatée.
            "definitive_depuis_utc": _z((result or {}).get("verified_at")),
            "verifiee_le_utc": _z((result or {}).get("verified_at")),
            "enregistree_ancien_systeme_utc": _z((result or {}).get("legacy_recorded_at")),
            "derniere_modification_utc": _z((result or {}).get("updated_at")),
            "derniere_verification_utc": _z((result or {}).get("last_checked_at")),
        },
        "correction": {
            "version": int((result or {}).get("version") or 0),
            "nb_corrections": int((result or {}).get("nb_corrections") or 0),
            "historique": historique,
        },
    }
    return entry


def build_day_export(db, date_db: str, generated_at: Optional[datetime] = None) -> Dict[str, Any]:
    generated_at = generated_at or datetime.utcnow()
    courses = []
    for race in db.get_results_for_date(date_db):
        race_id = race["race_id"]
        runners = db.get_runners(race_id)
        result = race.get("result")
        history = db.get_result_history(race_id) if result else []
        with db.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT bet_type, combination, dividend FROM rapports WHERE race_id = ?", (race_id,))
            rapports = [dict(r) for r in cursor.fetchall()]
        courses.append(build_course_entry(race, runners, result, history, rapports))

    compte = {"EN_ATTENTE": 0, "PROVISOIRE": 0, "DEFINITIVE": 0, "ANNULEE": 0}
    for c in courses:
        compte[c["statut"]["code"]] = compte.get(c["statut"]["code"], 0) + 1

    return {
        "schema_version": SCHEMA_VERSION,
        "fournisseur": FOURNISSEUR,
        "origine": ORIGINE,
        "version_code": version_code(),
        "date_course": date_db,
        "genere_le_utc": generated_at.replace(microsecond=0).isoformat() + "Z",
        "nb_courses": len(courses),
        "compte_par_statut": compte,
        "empreinte_sha256": _sha256(courses),
        "courses": courses,
    }


CORRECTION_REASONS = ("CORRECTION_CLASSEMENT", "ANNULATION")


def build_corrections_inventory(db, generated_at: Optional[datetime] = None) -> Dict[str, Any]:
    """Inventaire vérifiable de TOUTES les corrections (retour partenaire,
    point 4) : pour chaque événement CORRECTION_CLASSEMENT / ANNULATION,
    la course, les versions avant/après, les classements et incidents
    avant/après, la raison et l'horodatage. Le décompte par raison couvre
    l'ensemble des versions de l'historique."""
    generated_at = generated_at or datetime.utcnow()
    with db.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("""SELECT h.*, r.date FROM race_results_history h JOIN races r ON r.race_id = h.race_id
                          ORDER BY r.date, h.race_id, h.version""")
        rows = [dict(x) for x in cursor.fetchall()]
    by_race: Dict[str, List[Dict[str, Any]]] = {}
    for row in rows:
        by_race.setdefault(row["race_id"], []).append(row)
    compte: Dict[str, int] = {}
    events: List[Dict[str, Any]] = []
    for race_id, hist in by_race.items():
        for i, h in enumerate(hist):
            compte[h["reason"]] = compte.get(h["reason"], 0) + 1
            if h["reason"] not in CORRECTION_REASONS or i == 0:
                continue
            prev = hist[i - 1]

            def _rk(x):
                try:
                    return [int(r["num"]) for r in json.loads(x or "[]")]
                except Exception:
                    return []

            def _inc(x):
                try:
                    return json.loads(x or "[]")
                except Exception:
                    return []
            events.append({
                "course_id": race_id, "date": h["date"], "raison": h["reason"],
                "version_avant": int(prev["version"]), "version_apres": int(h["version"]),
                "statut_avant": prev["statut"], "statut_apres": h["statut"],
                "classement_avant": _rk(prev["ranking_json"]), "classement_apres": _rk(h["ranking_json"]),
                "incidents_avant": _inc(prev["incidents_json"]), "incidents_apres": _inc(h["incidents_json"]),
                "source_avant": prev.get("source"), "source_apres": h.get("source"),
                "horodatage_utc": _z(h["recorded_at"]),
            })
    return {
        "schema_version": SCHEMA_VERSION,
        "fournisseur": FOURNISSEUR,
        "version_code": version_code(),
        "genere_le_utc": generated_at.replace(microsecond=0).isoformat() + "Z",
        "nb_corrections": len(events),
        "compte_par_raison": dict(sorted(compte.items())),
        "corrections": events,
    }


def export_results_json(db, site_dir: str, days: Optional[int] = None, today: Optional[datetime] = None) -> Dict[str, Any]:
    """Écrit ``site/resultats/AAAA-MM-JJ.json`` pour TOUTES les journées
    présentes en base (``days`` limite la fenêtre si précisé), puis
    ``site/resultats/corrections.json`` (inventaire) et
    ``site/resultats/index.json`` (manifeste de tous les fichiers présents)."""
    today = today or datetime.utcnow()
    out_dir = os.path.join(site_dir, "resultats")
    os.makedirs(out_dir, exist_ok=True)

    with db.transaction() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT date FROM races WHERE date IS NOT NULL ORDER BY date DESC")
        dates = [row["date"] for row in cursor.fetchall()]
    if days is not None:
        cutoff = (today - timedelta(days=days - 1)).strftime("%Y-%m-%d")
        dates = [d for d in dates if d >= cutoff]

    written: Dict[str, Dict[str, Any]] = {}
    for d in dates:
        payload = build_day_export(db, d, generated_at=today)
        path = os.path.join(out_dir, f"{d}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        written[d] = {"nb_courses": payload["nb_courses"], "compte_par_statut": payload["compte_par_statut"],
                      "empreinte_sha256": payload["empreinte_sha256"], "genere_le_utc": payload["genere_le_utc"]}

    # Manifeste : tous les fichiers journaliers présents dans le dossier.
    journees: Dict[str, Dict[str, Any]] = {}
    for fname in sorted(os.listdir(out_dir)):
        if not (fname.endswith(".json") and len(fname) == 15 and fname[4] == "-" and fname[7] == "-"):
            continue
        d = fname[:-5]
        if d in written:
            journees[d] = dict(written[d], fichier=f"resultats/{fname}")
            continue
        try:
            with open(os.path.join(out_dir, fname), "r", encoding="utf-8") as f:
                old = json.load(f)
            journees[d] = {"nb_courses": old.get("nb_courses"), "compte_par_statut": old.get("compte_par_statut"),
                           "empreinte_sha256": old.get("empreinte_sha256"), "genere_le_utc": old.get("genere_le_utc"),
                           "fichier": f"resultats/{fname}"}
        except Exception:
            continue

    inventaire = build_corrections_inventory(db, generated_at=today)
    with open(os.path.join(out_dir, "corrections.json"), "w", encoding="utf-8") as f:
        json.dump(inventaire, f, ensure_ascii=False, indent=1)

    index = {
        "schema_version": SCHEMA_VERSION,
        "fournisseur": FOURNISSEUR,
        "version_code": version_code(),
        "genere_le_utc": today.replace(microsecond=0).isoformat() + "Z",
        "url_base": "https://prono.elite-turf.fr/",
        "nb_journees": len(journees),
        "inventaire_corrections": {"fichier": "resultats/corrections.json", "nb_corrections": inventaire["nb_corrections"],
                                   "empreinte_sha256": _sha256(inventaire["corrections"])},
        "journees": dict(sorted(journees.items(), reverse=True)),
    }
    with open(os.path.join(out_dir, "index.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    return {"jours_ecrits": sorted(written), "jours_indexes": len(journees), "dossier": out_dir,
            "nb_corrections": inventaire["nb_corrections"]}
