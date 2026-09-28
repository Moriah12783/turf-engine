"""Ombre du fondamental dans NVE : constantes de la règle pré-enregistrée et
outils de lecture (intervalle apparié par réunion, puissance, recettes).

Règle arrêtée avec le dev NVE le 28/09/2026. Elle sera committée dans un
document daté avant le premier jour d'ombre ; le script de lecture reprendra
ces constantes, couvertes par un test. Aucune lecture intermédiaire : ce
module ne lit aucune donnée d'ombre, il ne fait que calculer.
"""

import math
from statistics import NormalDist
from typing import Hashable, Optional, Sequence, Tuple

import numpy as np

POIDS_MARCHE = 0.90                     # production NVE gelée pendant l'ombre (recette linéaire)
LECTURE_1, LECTURE_2 = 1000, 2300       # éditions éligibles au matin
NIVEAU_LECTURE_1 = 0.99                 # passage en production à la 1re lecture
NIVEAU_LECTURE_2 = 0.95                 # passage en production à la 2de lecture
NIVEAU_INUTILITE = 0.95                 # arrêt si la borne haute est négative, à l'une ou l'autre lecture
COUVERTURE_MIN = 0.90                   # en dessous : lecture rendue, décision suspendue
SEUIL_NON_DEGRADATION = -0.02           # borne basse IC95 de l'écart ombre − publié (« dans les 8 »)
HORIZONS_SECONDAIRES = ("T_MATIN", "T90", "T30", "T15")
# « Effet détectable à 2 300 éditions » (point 4, fixé AVANT le calcul) :
# puissance d'au moins 80 % d'obtenir une borne basse IC95 positive à 2 300
# éditions, calculée sur l'effet estimé et l'erreur-type par réunion.
PUISSANCE_MIN = 0.80
BOOTSTRAP_TIRAGES = 4000
GRAINE = 20260928
PLANCHER = 1e-6


def z_bilateral(level: float) -> float:
    return NormalDist().inv_cdf(0.5 + level / 2.0)


def _clusters(values: Sequence[float], clusters: Sequence[Hashable]) -> Tuple[np.ndarray, np.ndarray]:
    """Sommes et effectifs par grappe (réunion)."""
    index = {}
    sums, counts = [], []
    for v, c in zip(values, clusters):
        k = index.setdefault(c, len(index))
        if k == len(sums):
            sums.append(0.0)
            counts.append(0)
        sums[k] += float(v)
        counts[k] += 1
    return np.array(sums), np.array(counts, dtype=float)


def bootstrap_means(values: Sequence[float], clusters: Sequence[Hashable], n: int = BOOTSTRAP_TIRAGES,
                    seed: int = GRAINE) -> np.ndarray:
    """Moyennes bootstrap en tirant des RÉUNIONS entières avec remise (les
    courses d'une réunion partagent terrain, météo et version du modèle)."""
    sums, counts = _clusters(values, clusters)
    rng = np.random.default_rng(seed)
    out = []
    for start in range(0, n, 500):                       # par lots : mémoire bornée
        idx = rng.integers(0, len(sums), size=(min(500, n - start), len(sums)))
        out.append(sums[idx].sum(axis=1) / counts[idx].sum(axis=1))
    return np.concatenate(out)


def intervalle(values: Sequence[float], clusters: Sequence[Hashable], level: float) -> Tuple[float, float]:
    means = bootstrap_means(values, clusters)
    tail = (1.0 - level) / 2.0 * 100.0
    return float(np.percentile(means, tail)), float(np.percentile(means, 100.0 - tail))


def erreur_type(values: Sequence[float], clusters: Sequence[Hashable]) -> float:
    return float(np.std(bootstrap_means(values, clusters), ddof=1))


def puissance(effet: float, se: float, n0: int, n: int, level: float) -> float:
    """Probabilité que la borne basse de l'intervalle (niveau ``level``) soit
    positive à ``n`` éditions, si l'effet vaut ``effet`` et que l'erreur-type
    observée à ``n0`` éditions décroît en 1/√n."""
    if se <= 0 or n0 <= 0:
        return float("nan")
    se_n = se * math.sqrt(n0 / n)
    return NormalDist().cdf(effet / se_n - z_bilateral(level))


def borne_basse_projetee(effet: float, se: float, n0: int, n: int, level: float = 0.95) -> float:
    return effet - z_bilateral(level) * se * math.sqrt(n0 / n)


def marche_de_edition(publie: np.ndarray, modele: np.ndarray, poids: float) -> np.ndarray:
    """Part marché d'une édition NVE : publié = poids × marché + (1 − poids) × modèle."""
    m = np.maximum((publie - (1.0 - poids) * modele) / poids, PLANCHER)
    return m / m.sum()


def ombre_lineaire(marche: np.ndarray, fondamental: np.ndarray, poids: float = POIDS_MARCHE) -> np.ndarray:
    """Recette A : 0,90 marché + 0,10 fondamental."""
    return poids * marche + (1.0 - poids) * fondamental


def ombre_loglineaire(marche: np.ndarray, fondamental: Optional[np.ndarray], a: float, b: float = 0.0) -> np.ndarray:
    """Recette B : p ∝ marché^a × fondamental^b (poids réappris chaque lundi)."""
    s = a * np.log(np.maximum(marche, PLANCHER))
    if fondamental is not None:
        s = s + b * np.log(np.maximum(fondamental, PLANCHER))
    e = np.exp(s - s.max())
    return e / e.sum()


def top8(nums: Sequence[int], probs: np.ndarray) -> list:
    """Les 8 plus probables (à égalité, l'ordre des partants)."""
    order = sorted(range(len(nums)), key=lambda i: (-float(probs[i]), i))
    return [nums[i] for i in order[:8]]


def dans_les_8(selection: Sequence[int], cibles: Sequence[int]) -> bool:
    return set(cibles) <= set(list(selection)[:8])
