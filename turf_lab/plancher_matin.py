"""Plancher horaire de l'édition du matin : source unique.

Décision de Steph du 12/11/2026 : le verrou de l'édition du matin (T_MATIN)
passe à 08h30 heure de Paris à compter du lundi 23/11/2026. Avant cette date,
le plancher reste à 06h30 UTC. À partir d'elle, 08h30 à Paris donne 07h30 UTC
en hiver et 06h30 UTC en été.

Tous les fichiers qui dépendent de cette heure la lisent ici, jamais en dur :
  - daily_sync.due_horizons : ouverture de T_MATIN ;
  - publication_gate : plancher de diffusion (règle PG2, motif
    BEFORE_MORNING_FLOOR) ;
  - fondamental_nuit : limite d'écriture du calcul de nuit (plancher − 10 min) ;
  - fondamental_nuit.yml : limite d'écriture du filet GitHub et limite d'envoi
    sur R2 (plancher − 2 min), par la ligne de commande ci-dessous ;
  - html_report : libellés publics.
L'horloge Cloudflare reste en UTC (05h05 et 05h50), hors de la bascule.

Heures sans fuseau. Toutes les heures rendues sont des heures UTC SANS fuseau,
comme ``now_utc`` (datetime.utcnow()) et ``lock_time_utc`` dans daily_sync et
publication_gate : une comparaison entre une heure avec fuseau et une heure
sans fuseau lèverait TypeError. La seule conversion part d'une date : 08h30 à
Paris avec son fuseau, convertie en UTC, puis fuseau retiré. Jamais
``astimezone()`` sur une heure sans fuseau : il prendrait l'heure locale de la
machine.

Windows. Sans le paquet tzdata, ZoneInfo("Europe/Paris") est introuvable. Le
module applique alors la règle européenne : heure d'été du dernier dimanche de
mars au dernier dimanche d'octobre, changement à 01h00 UTC. Un test vérifie
que les deux chemins donnent le même plancher chaque jour de 2026 à 2040.

Ligne de commande (code retour 0 seulement si l'action est autorisée ; tout
autre cas, erreur comprise, rend un code non nul : refus par défaut) :
    python -m turf_lab.plancher_matin ecriture   # ECRITURE_AUTORISEE ... | ECRITURE_REFUSEE ...
    python -m turf_lab.plancher_matin envoi      # ENVOI_AUTORISE ...     | ENVOI_REFUSE ...
    python -m turf_lab.plancher_matin journal    # ligne PLANCHER_MATIN
"""

import argparse
import json
import sys
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Union

BASCULE = date(2026, 11, 23)                 # premier jour du plancher à 08h30 heure de Paris
ANCIEN_PLANCHER_UTC = (6, 30)                # avant BASCULE : 06h30 UTC
PLANCHER_PARIS = (8, 30)                     # à partir de BASCULE : 08h30 heure de Paris
FUSEAU = "Europe/Paris"
MARGE_ECRITURE = timedelta(minutes=10)       # calcul de nuit : aucune écriture après plancher − 10 min
MARGE_ENVOI = timedelta(minutes=2)           # aucun envoi sur R2 après plancher − 2 min


def _zone():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(FUSEAU)
    except Exception:  # ZoneInfoNotFoundError sous Windows sans tzdata, ImportError
        return None


_ZONE = _zone()


def source_fuseau() -> str:
    """« zoneinfo » si la base des fuseaux est disponible, sinon « regle_ue »."""
    return "zoneinfo" if _ZONE is not None else "regle_ue"


def _jour(date_course: Union[date, datetime, str]) -> date:
    if isinstance(date_course, datetime):
        return date_course.date()
    if isinstance(date_course, date):
        return date_course
    return datetime.strptime(str(date_course), "%Y-%m-%d").date()


def _dernier_dimanche(annee: int, mois: int) -> date:
    fin = date(annee + (mois == 12), mois % 12 + 1, 1) - timedelta(days=1)
    return fin - timedelta(days=(fin.weekday() - 6) % 7)


def decalage_regle_ue(jour: date) -> int:
    """Heures d'avance de Paris sur UTC à 08h30 locales, par la règle
    européenne. Le changement d'heure a lieu à 01h00 UTC : à 08h30, le jour
    même du changement suit déjà la nouvelle heure."""
    ete = _dernier_dimanche(jour.year, 3) <= jour < _dernier_dimanche(jour.year, 10)
    return 2 if ete else 1


def _plancher_paris_utc(jour: date, zone) -> datetime:
    h, m = PLANCHER_PARIS
    if zone is not None:
        local = datetime(jour.year, jour.month, jour.day, h, m, tzinfo=zone)
        return local.astimezone(timezone.utc).replace(tzinfo=None)
    return datetime(jour.year, jour.month, jour.day, h, m) - timedelta(hours=decalage_regle_ue(jour))


def plancher_matin_utc(date_course: Union[date, datetime, str]) -> datetime:
    """Heure UTC sans fuseau à partir de laquelle l'édition du matin de cette
    date peut être verrouillée et diffusée. ValueError si la date est illisible."""
    jour = _jour(date_course)
    if jour < BASCULE:
        h, m = ANCIEN_PLANCHER_UTC
        return datetime(jour.year, jour.month, jour.day, h, m)
    return _plancher_paris_utc(jour, _ZONE)


def limite_ecriture_nuit_utc(date_course: Union[date, datetime, str]) -> datetime:
    """Après cette heure (UTC sans fuseau), le calcul de nuit n'écrit plus rien."""
    return plancher_matin_utc(date_course) - MARGE_ECRITURE


def limite_envoi_utc(date_course: Union[date, datetime, str]) -> datetime:
    """Après cette heure (UTC sans fuseau), aucune base n'est envoyée sur R2."""
    return plancher_matin_utc(date_course) - MARGE_ENVOI


def en_utc_naif(instant: datetime) -> datetime:
    """Heure UTC sans fuseau. Une heure avec fuseau est convertie en UTC ; une
    heure sans fuseau est déjà de l'UTC (convention du dépôt) et rendue telle
    quelle, sans jamais passer par l'heure locale de la machine."""
    if instant.tzinfo is None:
        return instant
    return instant.astimezone(timezone.utc).replace(tzinfo=None)


def maintenant_utc() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def heure_paris_du_plancher(date_course: Union[date, datetime, str]) -> str:
    """Heure de Paris (HH:MM) du plancher de cette date."""
    jour = _jour(date_course)
    plancher = plancher_matin_utc(jour)
    if _ZONE is not None:
        local = plancher.replace(tzinfo=timezone.utc).astimezone(_ZONE)
    else:
        local = plancher + timedelta(hours=decalage_regle_ue(jour))
    return local.strftime("%H:%M")


def libelle_plancher(date_course: Union[date, datetime, str]) -> str:
    """Libellé public : « 06h30 GMT » avant la bascule, puis « 08h30 heure de
    Paris (07h30 GMT) » en hiver. GMT est l'heure d'Abidjan."""
    jour = _jour(date_course)
    gmt = plancher_matin_utc(jour).strftime("%Hh%M")
    if jour < BASCULE:
        return f"{gmt} GMT"
    h, m = PLANCHER_PARIS
    return f"{h:02d}h{m:02d} heure de Paris ({gmt} GMT)"


def etat_du_jour(instant_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """Plancher et limites du jour UTC de l'instant donné (agrégats seulement)."""
    now = en_utc_naif(instant_utc) if instant_utc is not None else maintenant_utc()
    jour = now.date()
    return {
        "jour": jour.isoformat(),
        "regle": "08h30_PARIS" if jour >= BASCULE else "06h30_UTC",
        "bascule": BASCULE.isoformat(),
        "plancher_utc": plancher_matin_utc(jour).strftime("%H:%M"),
        "plancher_paris": heure_paris_du_plancher(jour),
        "limite_ecriture_utc": limite_ecriture_nuit_utc(jour).strftime("%H:%M"),
        "limite_envoi_utc": limite_envoi_utc(jour).strftime("%H:%M"),
        "fuseau": source_fuseau(),
    }


def journal(instant_utc: Optional[datetime] = None) -> Dict[str, Any]:
    """Imprime la ligne PLANCHER_MATIN (une par passage de daily_sync)."""
    etat = etat_du_jour(instant_utc)
    print("PLANCHER_MATIN " + json.dumps(etat, ensure_ascii=False, sort_keys=True))
    return etat


def _verdict(action: str, now: datetime) -> int:
    jour = now.date()
    if action == "ecriture":
        limite, oui, non = limite_ecriture_nuit_utc(jour), "ECRITURE_AUTORISEE", "ECRITURE_REFUSEE"
    else:
        limite, oui, non = limite_envoi_utc(jour), "ENVOI_AUTORISE", "ENVOI_REFUSE"
    ok = now < limite
    print(f"{oui if ok else non} maintenant={now:%H:%M} UTC limite={limite:%H:%M} UTC "
          f"jour={jour.isoformat()} plancher={plancher_matin_utc(jour):%H:%M} UTC")
    return 0 if ok else 1


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Plancher horaire de l'édition du matin")
    parser.add_argument("action", choices=["ecriture", "envoi", "journal"])
    parser.add_argument("--maintenant", default=None, help="Instant UTC ISO (tests seulement)")
    args = parser.parse_args(argv)
    try:
        now = en_utc_naif(datetime.fromisoformat(args.maintenant)) if args.maintenant else maintenant_utc()
        if args.action == "journal":
            journal(now)
            return 0
        return _verdict(args.action, now)
    except Exception as exc:  # refus par défaut : jamais de code 0 sur une erreur
        print(f"PLANCHER_ERREUR {type(exc).__name__}: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
