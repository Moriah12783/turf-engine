"""Garde anti-régression de publier_vers_github.bat.

Incident du 23/09/2026 : la publication depuis le PC a republié une copie
PÉRIMÉE de .github/workflows/daily_sync.yml et de .gitignore (antérieure à la
migration R2), annulant sans bruit une modification faite côté GitHub.

Cause : le script publie tout fichier du PC qui diffère de GitHub, y compris
un fichier que personne n'a touché sur le PC mais qui a évolué sur GitHub.

Règle appliquée ici, juste après ``git add -A`` : un fichier modifié dont le
contenu est IDENTIQUE à une version ANTÉRIEURE du même fichier sur GitHub est
une copie périmée, pas une modification. Il est restauré depuis GitHub (sur
le PC aussi : le dossier se met à jour tout seul) au lieu d'être publié. Un
contenu jamais vu sur GitHub est une vraie modification : il est publié.

Retour volontaire à une ancienne version : ``set PUBLIER_FORCER_ANCIEN=1``
avant de lancer le script (la garde est alors désactivée pour cette fois).

Usage (appelé par publier_vers_github.bat) :
    python garde_fichiers_perimes.py [REF]      # REF par défaut : FETCH_HEAD
"""

import os
import subprocess
import sys
from typing import List, Set, Tuple

NULL_BLOB = "0" * 40


def git(*args: str, cwd: str = ".") -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout


def staged_modifications(cwd: str = ".") -> List[Tuple[str, str, str]]:
    """(chemin, blob GitHub actuel, blob local indexé) des fichiers modifiés."""
    out = git("diff", "--cached", "--raw", "--no-abbrev", "--no-renames", "--diff-filter=M", "-z", cwd=cwd)
    fields = out.split("\0")
    result = []
    for meta, path in zip(fields[0::2], fields[1::2]):
        if not meta.startswith(":"):
            continue
        parts = meta[1:].split()
        result.append((path, parts[2], parts[3]))
    return result


def historical_blobs(path: str, ref: str, cwd: str = ".") -> Set[str]:
    """Toutes les versions (blobs) qu'a connues ``path`` dans l'historique de ``ref``."""
    out = git("log", "--format=", "--raw", "--no-abbrev", "--no-renames", ref, "--", path, cwd=cwd)
    blobs = set()
    for line in out.splitlines():
        if line.startswith(":"):
            parts = line[1:].split()
            blobs.update(b for b in (parts[2], parts[3]) if b != NULL_BLOB)
    return blobs


def find_stale(ref: str = "FETCH_HEAD", cwd: str = ".") -> List[str]:
    stale = []
    for path, current_blob, local_blob in staged_modifications(cwd):
        if local_blob != current_blob and local_blob in historical_blobs(path, ref, cwd):
            stale.append(path)
    return stale


def main(argv: List[str]) -> int:
    ref = argv[1] if len(argv) > 1 else "FETCH_HEAD"
    if os.environ.get("PUBLIER_FORCER_ANCIEN") == "1":
        print("[!] PUBLIER_FORCER_ANCIEN=1 : garde anti-régression DÉSACTIVÉE pour cette publication.")
        return 0
    stale = find_stale(ref)
    if not stale:
        print("[OK] Aucun fichier périmé : seules de vraies modifications seront publiées.")
        return 0
    print("[!] Fichiers PÉRIMÉS sur ce PC (identiques à une ancienne version GitHub) :")
    for path in stale:
        print("    - " + path)
        git("checkout", ref, "--", path)
    print("[*] Ils sont remplacés par la version GitHub à jour (sur le PC aussi), pas publiés.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
