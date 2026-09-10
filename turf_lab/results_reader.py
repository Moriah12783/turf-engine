"""Lecture du classement PMU — points 2 et 4 du correctif partenaire.

Ce module lit l'arrivée d'une course telle que le flux public PMU la publie
et la transforme en une lecture STRUCTURÉE, VALIDÉE et QUALIFIÉE :

- ``statut`` : EN_ATTENTE | PROVISOIRE | DEFINITIVE | ANNULEE. Une arrivée
  n'est DEFINITIVE que si le flux le dit (``arriveeDefinitive`` /
  ``isArriveeDefinitive`` vrai, ou statut ``ARRIVEE_DEFINITIVE*``). Un simple
  ``ordreArrivee`` présent sur les partants n'est qu'une arrivée PROVISOIRE.
- ``ranking`` : classement avec rangs de compétition et dead-heats préservés.
  Le flux publie ``ordreArrivee`` sous forme de liste de listes :
  ``[[14],[3,5],[6]]`` signifie 1er : 14 ; 2e ex æquo : 3 et 5 ; 4e : 6.
  L'ancien lecteur aplatissait ces listes (le 5 devenait « 3e ») et
  itérait le booléen ``arriveeDefinitive`` comme s'il s'agissait de la
  liste d'arrivée.
- ``incidents`` : chevaux non classés (disqualifiés, arrêtés, tombés…) lus
  dans ``incidents[]`` de la course et dans ``incident`` du partant. Le
  ``statut`` d'un partant vaut PARTANT / NON_PARTANT : il ne porte JAMAIS la
  disqualification — c'est pourquoi l'ancienne liste ``disqualified`` était
  toujours vide.
- ``non_partants`` : ``incidents[type=NON_PARTANT]`` et ``statut=NON_PARTANT``.

Aucune dépendance réseau : le module ne lit que des dictionnaires.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple

STATUT_EN_ATTENTE = "EN_ATTENTE"
STATUT_PROVISOIRE = "PROVISOIRE"
STATUT_DEFINITIVE = "DEFINITIVE"
STATUT_ANNULEE = "ANNULEE"

SOURCE_PROGRAMME = "PMU_PROGRAMME"       # objet course du programme du jour
SOURCE_COURSE = "PMU_COURSE"             # endpoint /R{n}/C{m}
SOURCE_PARTICIPANTS = "PMU_PARTICIPANTS"  # ordreArrivee des partants (repli)

NON_PARTANT_TYPES = {"NON_PARTANT", "NP", "FORFAIT"}
DISQUALIFICATION_MARKER = "DISQUALIF"


@dataclass
class ArrivalReading:
    statut: str = STATUT_EN_ATTENTE
    pmu_statut: str = ""
    pmu_categorie: str = ""
    definitive_flag: bool = False
    complete: bool = False
    ranking: List[Dict[str, Any]] = field(default_factory=list)   # [{"rang", "num", "dead_heat"}]
    incidents: List[Dict[str, Any]] = field(default_factory=list)  # [{"num", "type"}]
    non_partants: List[int] = field(default_factory=list)
    source: str = ""
    errors: List[str] = field(default_factory=list)

    # ── Vues dérivées ────────────────────────────────────────────────
    @property
    def valid(self) -> bool:
        return not self.errors

    @property
    def has_result(self) -> bool:
        return self.statut in (STATUT_PROVISOIRE, STATUT_DEFINITIVE) and bool(self.ranking)

    def flat_arrival(self) -> List[int]:
        """Liste plate des numéros classés (compatibilité banc historique).
        Les ex æquo sont voisins, dans l'ordre croissant de numéro."""
        return [int(r["num"]) for r in self.ranking]

    def disqualified(self) -> List[int]:
        return sorted({int(i["num"]) for i in self.incidents if DISQUALIFICATION_MARKER in str(i["type"]).upper()})

    def signature(self) -> Tuple[Tuple[Tuple[int, int], ...], Tuple[Tuple[int, str], ...]]:
        """Empreinte comparable (classement + incidents)."""
        rk = tuple((int(r["rang"]), int(r["num"])) for r in self.ranking)
        inc = tuple(sorted((int(i["num"]), str(i["type"])) for i in self.incidents))
        return rk, inc


# ──────────────────────────────────────────────────────────────────────
# Statut
# ──────────────────────────────────────────────────────────────────────

def classify_statut(course_obj: Optional[Dict[str, Any]]) -> Tuple[str, str, str, bool, bool]:
    """Retourne (statut interne, statut PMU brut, catégorie PMU, drapeau définitif, complet)."""
    course_obj = course_obj or {}
    raw = str(course_obj.get("statut") or "").upper()
    cat = str(course_obj.get("categorieStatut") or "").upper()
    flag_a = course_obj.get("arriveeDefinitive")
    flag_b = course_obj.get("isArriveeDefinitive")
    # Anciennes versions du flux : ``arriveeDefinitive`` pouvait être la liste
    # d'arrivée elle-même ; une liste non vide vaut « définitive ».
    definitive = (flag_a is True) or (flag_b is True) or (isinstance(flag_a, list) and len(flag_a) > 0)
    if raw.startswith("ARRIVEE_DEFINITIVE"):
        definitive = True
    complete = raw.endswith("_COMPLETE")

    if "ANNUL" in raw or "ANNUL" in cat:
        return STATUT_ANNULEE, raw, cat, False, False
    if definitive:
        return STATUT_DEFINITIVE, raw, cat, True, complete
    if cat == "ARRIVEE" or "PROVISOIRE" in raw or raw == "FIN_COURSE":
        return STATUT_PROVISOIRE, raw, cat, False, False
    return STATUT_EN_ATTENTE, raw, cat, False, False


# ──────────────────────────────────────────────────────────────────────
# Classement
# ──────────────────────────────────────────────────────────────────────

def _to_int(value: Any) -> Optional[int]:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def parse_ordre_arrivee(raw: Any) -> List[List[int]]:
    """Normalise ``ordreArrivee`` en groupes ordonnés de numéros.

    Accepte : liste de listes (format officiel, dead-heats), liste plate
    d'entiers (un cheval par rang), chaîne « 14-3-5 ». Un booléen ou toute
    autre valeur donne une liste vide (jamais d'itération sur ``True``)."""
    groups: List[List[int]] = []
    if isinstance(raw, str):
        raw = [tok for tok in raw.replace(",", "-").split("-") if tok.strip()]
    if not isinstance(raw, (list, tuple)):
        return groups
    for item in raw:
        if isinstance(item, (list, tuple)):
            nums = [n for n in (_to_int(x) for x in item) if n is not None]
            if nums:
                groups.append(sorted(set(nums)))
        else:
            n = _to_int(item)
            if n is not None:
                groups.append([n])
    return groups


def ranking_from_groups(groups: Sequence[Sequence[int]]) -> List[Dict[str, Any]]:
    """Rangs de compétition : après un ex æquo à la 2e place, le suivant est 4e."""
    ranking: List[Dict[str, Any]] = []
    rang = 1
    for group in groups:
        nums = sorted(int(n) for n in group)
        for n in nums:
            ranking.append({"rang": rang, "num": n, "dead_heat": len(nums) > 1})
        rang += len(nums)
    return ranking


def groups_from_participants(participants: Iterable[Dict[str, Any]]) -> List[List[int]]:
    """Repli : reconstruit les groupes depuis ``ordreArrivee`` (entier) des
    partants. Deux partants au même rang forment un ex æquo."""
    by_rank: Dict[int, List[int]] = {}
    for p in participants or []:
        pos = _to_int(p.get("ordreArrivee"))
        num = _to_int(p.get("numPmu"))
        if pos is None or num is None:
            continue
        by_rank.setdefault(pos, []).append(num)
    return [sorted(set(by_rank[k])) for k in sorted(by_rank)]


# ──────────────────────────────────────────────────────────────────────
# Incidents / non-partants
# ──────────────────────────────────────────────────────────────────────

def read_incidents(course_obj: Optional[Dict[str, Any]],
                   participants: Optional[Iterable[Dict[str, Any]]] = None
                   ) -> Tuple[List[Dict[str, Any]], List[int]]:
    incidents: Dict[int, str] = {}
    non_partants: Set[int] = set()

    for inc in (course_obj or {}).get("incidents") or []:
        if not isinstance(inc, dict):
            continue
        typ = str(inc.get("type") or "").upper()
        nums = inc.get("numeroParticipants") or inc.get("numerosParticipants") or []
        if isinstance(nums, (int, str)):
            nums = [nums]
        for n in (_to_int(x) for x in nums):
            if n is None:
                continue
            if typ in NON_PARTANT_TYPES:
                non_partants.add(n)
            elif typ:
                incidents[n] = typ

    for p in participants or []:
        num = _to_int(p.get("numPmu"))
        if num is None:
            continue
        statut = str(p.get("statut") or "").upper()
        if statut in NON_PARTANT_TYPES or bool(p.get("nonPartant", False)):
            non_partants.add(num)
        typ = str(p.get("incident") or "").upper()
        if typ in NON_PARTANT_TYPES:
            non_partants.add(num)
        elif typ and num not in incidents:
            incidents[num] = typ

    inc_list = [{"num": n, "type": incidents[n]} for n in sorted(incidents)]
    return inc_list, sorted(non_partants)


# ──────────────────────────────────────────────────────────────────────
# Validation
# ──────────────────────────────────────────────────────────────────────

def validate_reading(reading: ArrivalReading, active_nums: Optional[Iterable[int]] = None) -> List[str]:
    errors: List[str] = []
    placed = [int(r["num"]) for r in reading.ranking]
    if len(placed) != len(set(placed)):
        errors.append("NUMERO_EN_DOUBLE")
    inc_nums = {int(i["num"]) for i in reading.incidents}
    np_nums = set(reading.non_partants)
    if set(placed) & inc_nums:
        errors.append("CLASSE_ET_NON_CLASSE")
    if set(placed) & np_nums:
        errors.append("CLASSE_ET_NON_PARTANT")
    if active_nums is not None:
        allowed = {int(n) for n in active_nums}
        unknown = sorted(set(placed) - allowed)
        if unknown:
            errors.append("NUMERO_INCONNU:" + ",".join(str(n) for n in unknown))
    # Rangs de compétition cohérents
    expected = ranking_from_groups(_regroup(reading.ranking))
    if [(r["rang"], r["num"]) for r in expected] != [(int(r["rang"]), int(r["num"])) for r in reading.ranking]:
        errors.append("RANGS_INCOHERENTS")
    if reading.statut in (STATUT_PROVISOIRE, STATUT_DEFINITIVE) and not reading.ranking:
        errors.append("ARRIVEE_SANS_CLASSEMENT")
    return errors


def _regroup(ranking: Sequence[Dict[str, Any]]) -> List[List[int]]:
    groups: Dict[int, List[int]] = {}
    for r in ranking:
        groups.setdefault(int(r["rang"]), []).append(int(r["num"]))
    return [sorted(groups[k]) for k in sorted(groups)]


# ──────────────────────────────────────────────────────────────────────
# Point d'entrée
# ──────────────────────────────────────────────────────────────────────

def read_arrival(course_obj: Optional[Dict[str, Any]],
                 participants: Optional[List[Dict[str, Any]]] = None,
                 active_nums: Optional[Iterable[int]] = None,
                 source: str = SOURCE_PROGRAMME) -> ArrivalReading:
    """Lit l'arrivée depuis l'objet course (programme ou endpoint course) et,
    en repli, depuis les partants. Ne lève jamais : les anomalies sont dans
    ``errors`` et l'appelant n'enregistre rien tant qu'il y en a."""
    reading = ArrivalReading()
    statut, raw, cat, flag, complete = classify_statut(course_obj)
    reading.statut, reading.pmu_statut, reading.pmu_categorie = statut, raw, cat
    reading.definitive_flag, reading.complete = flag, complete

    groups: List[List[int]] = []
    if course_obj:
        groups = parse_ordre_arrivee(course_obj.get("ordreArrivee"))
        if not groups and isinstance(course_obj.get("arriveeDefinitive"), list):
            groups = parse_ordre_arrivee(course_obj.get("arriveeDefinitive"))
    if groups:
        reading.source = source
    elif participants:
        groups = groups_from_participants(participants)
        if groups:
            reading.source = SOURCE_PARTICIPANTS
            # Un classement vu seulement sur les partants, sans drapeau de la
            # course, n'est qu'une arrivée PROVISOIRE.
            if reading.statut == STATUT_EN_ATTENTE:
                reading.statut = STATUT_PROVISOIRE

    reading.ranking = ranking_from_groups(groups)
    reading.incidents, reading.non_partants = read_incidents(course_obj, participants)
    # Un non-partant ne peut pas figurer parmi les incidents de course
    reading.incidents = [i for i in reading.incidents if int(i["num"]) not in set(reading.non_partants)]

    if reading.statut in (STATUT_PROVISOIRE, STATUT_DEFINITIVE):
        reading.errors = validate_reading(reading, active_nums)
    elif reading.statut == STATUT_ANNULEE:
        reading.ranking = []
    return reading


# ──────────────────────────────────────────────────────────────────────
# Comparaison de deux lectures (suivi des corrections — point 3)
# ──────────────────────────────────────────────────────────────────────

CMP_IDENTIQUE = "IDENTIQUE"
CMP_COMPLETION = "COMPLETION"     # l'ancien classement est un préfixe strict du nouveau
CMP_CORRECTION = "CORRECTION"     # un rang déjà publié change, ou un incident change
CMP_REGRESSION = "REGRESSION"     # le nouveau contient MOINS d'information (source périmée)


def compare_rankings(old_ranking: Sequence[Dict[str, Any]], new_ranking: Sequence[Dict[str, Any]],
                     old_incidents: Sequence[Dict[str, Any]], new_incidents: Sequence[Dict[str, Any]],
                     old_non_partants: Sequence[int] = (), new_non_partants: Sequence[int] = ()) -> str:
    """Qualifie le passage de l'ancienne lecture à la nouvelle.

    Un classement PLUS COURT n'est une régression (source partielle ou
    périmée) que si les chevaux disparus ne sont PAS expliqués par la nouvelle
    lecture. Si chaque cheval retiré figure désormais dans les incidents
    (disqualification, arrêt…) ou parmi les non-partants, c'est une CORRECTION
    finale cohérente : le cheval est retiré, l'incident conservé, une version
    créée. La longueur seule ne décide jamais (retour partenaire du 10/09)."""
    old_rk = [(int(r["rang"]), int(r["num"])) for r in old_ranking]
    new_rk = [(int(r["rang"]), int(r["num"])) for r in new_ranking]
    old_inc = {int(i["num"]): str(i["type"]) for i in old_incidents}
    new_inc = {int(i["num"]): str(i["type"]) for i in new_incidents}

    # Incidents : un type qui change pour un cheval déjà connu = correction ;
    # un incident retiré = correction ; un incident ajouté = complément.
    inc_changed = any(n in new_inc and new_inc[n] != t for n, t in old_inc.items())
    inc_removed = any(n not in new_inc for n in old_inc)
    inc_added = any(n not in old_inc for n in new_inc)

    if new_rk == old_rk:
        if inc_changed:
            return CMP_CORRECTION
        if inc_removed:
            # Classement identique mais incidents disparus : perte d'information
            # sans qu'aucun cheval ne réapparaisse au classement = variante de
            # source appauvrie (cache), jamais une correction.
            return CMP_REGRESSION
        return CMP_COMPLETION if inc_added else CMP_IDENTIQUE
    if len(new_rk) > len(old_rk) and new_rk[:len(old_rk)] == old_rk:
        return CMP_CORRECTION if (inc_changed or inc_removed) else CMP_COMPLETION

    removed = {n for _, n in old_rk} - {n for _, n in new_rk}
    if removed:
        explained = {int(n) for n in new_inc} | {int(n) for n in new_non_partants}
        if removed <= explained:
            return CMP_CORRECTION  # retrait expliqué (ex. disqualification finale)
        if len(new_rk) < len(old_rk) and old_rk[:len(new_rk)] == new_rk:
            return CMP_REGRESSION  # préfixe strict, retrait inexpliqué : source partielle/périmée
    return CMP_CORRECTION


def preserve_known_incidents(old_ranking: Sequence[Dict[str, Any]], old_incidents: Sequence[Dict[str, Any]],
                             old_non_partants: Sequence[int], new_ranking: Sequence[Dict[str, Any]],
                             new_incidents: Sequence[Dict[str, Any]], new_non_partants: Sequence[int]
                             ) -> Tuple[List[Dict[str, Any]], List[int], List[int]]:
    """Information monotone : un incident ou une non-partance déjà connus ne
    disparaissent que si le cheval RÉAPPARAÎT au classement (annulation d'une
    disqualification, par exemple). Sinon ils sont conservés — une variante de
    source sans le bloc ``incidents`` (cache) ne doit jamais les effacer.
    Retourne (incidents, non_partants, numéros préservés)."""
    new_placed = {int(r["num"]) for r in new_ranking}
    inc_map = {int(i["num"]): str(i["type"]) for i in new_incidents}
    np_set = {int(n) for n in new_non_partants}
    preserved: List[int] = []
    for i in old_incidents:
        n = int(i["num"])
        if n in new_placed or n in inc_map or n in np_set:
            continue
        inc_map[n] = str(i["type"])
        preserved.append(n)
    for n in old_non_partants:
        n = int(n)
        if n in new_placed or n in inc_map or n in np_set:
            continue
        np_set.add(n)
        preserved.append(n)
    incidents = [{"num": n, "type": inc_map[n]} for n in sorted(inc_map)]
    return incidents, sorted(np_set), sorted(preserved)


def removed_unexplained(old_ranking: Sequence[Dict[str, Any]], new_ranking: Sequence[Dict[str, Any]],
                        new_incidents: Sequence[Dict[str, Any]], new_non_partants: Sequence[int] = ()) -> List[int]:
    """Chevaux classés dans l'ancienne lecture, absents de la nouvelle, sans
    incident ni non-partance qui l'explique (diagnostic pour les journaux)."""
    old_nums = {int(r["num"]) for r in old_ranking}
    new_nums = {int(r["num"]) for r in new_ranking}
    explained = {int(i["num"]) for i in new_incidents} | {int(n) for n in new_non_partants}
    return sorted(old_nums - new_nums - explained)
