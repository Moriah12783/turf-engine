"""Client HTTP sécurisé — point 1 du correctif partenaire (validation HTTPS).

Règles :
- HTTPS obligatoire : toute URL qui n'est pas en ``https://`` est refusée
  (journal ``URL_REFUSEE``), jamais rétrogradée en clair.
- Certificat et nom d'hôte TOUJOURS vérifiés (contexte SSL par défaut de
  Python, enrichi du magasin ``certifi`` s'il est installé). Il n'existe
  aucun interrupteur pour désactiver la vérification.
- Un échec TLS n'est jamais contourné : il est journalisé (``TLS_ERROR``)
  et la réponse est ``None`` — le moteur préfère ne pas ingérer plutôt
  qu'ingérer une donnée dont l'origine n'est pas prouvée.
"""

import gzip
import json
import ssl
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any, Dict, Optional

DEFAULT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "fr-FR,fr;q=0.9,en-US;q=0.8,en;q=0.7",
}


class TlsStats:
    """Compteurs de la passe en cours (remis à zéro par l'appelant)."""

    def __init__(self) -> None:
        self.tls_errors = 0
        self.refused_urls = 0
        self.http_errors = 0

    def reset(self) -> None:
        self.tls_errors = 0
        self.refused_urls = 0
        self.http_errors = 0

    def as_dict(self) -> Dict[str, int]:
        return {"tls_errors": self.tls_errors, "refused_urls": self.refused_urls, "http_errors": self.http_errors}


STATS = TlsStats()


def build_ssl_context() -> ssl.SSLContext:
    """Contexte TLS strict : CERT_REQUIRED + vérification du nom d'hôte.

    ``certifi`` (magasin de racines Mozilla) est chargé en complément du
    magasin système lorsqu'il est disponible — utile sur certains postes
    Windows dont le magasin est incomplet. Jamais de ``CERT_NONE``."""
    ctx = ssl.create_default_context()
    ctx.check_hostname = True
    ctx.verify_mode = ssl.CERT_REQUIRED
    try:  # pragma: no cover - dépend de l'environnement
        import certifi  # type: ignore
        ctx.load_verify_locations(cafile=certifi.where())
    except Exception:
        pass
    return ctx


def is_https(url: str) -> bool:
    return isinstance(url, str) and url.lower().startswith("https://")


def _log(event: str, payload: Dict[str, Any]) -> None:
    payload = dict(payload)
    payload.setdefault("now_utc", datetime.utcnow().isoformat())
    print(event + " " + json.dumps(payload, ensure_ascii=False))


def get_json(url: str, timeout: int = 10, headers: Optional[Dict[str, str]] = None,
             opener=None) -> Optional[Any]:
    """GET JSON sur une URL HTTPS avec certificat vérifié.

    ``opener`` (tests) : fonction ``(request, context, timeout) -> réponse``
    remplaçant ``urllib.request.urlopen``. Retourne ``None`` sur tout échec."""
    if not is_https(url):
        STATS.refused_urls += 1
        _log("URL_REFUSEE", {"url": str(url), "raison": "HTTPS_OBLIGATOIRE"})
        return None

    ctx = build_ssl_context()
    req = urllib.request.Request(url, headers=headers or DEFAULT_HEADERS)
    open_fn = opener or (lambda r, c, t: urllib.request.urlopen(r, context=c, timeout=t))
    try:
        with open_fn(req, ctx, timeout) as response:
            status = getattr(response, "status", 200)
            if status != 200:
                STATS.http_errors += 1
                return None
            data = response.read()
            try:
                encoding = response.info().get("Content-Encoding")
            except Exception:
                encoding = None
            if encoding == "gzip":
                data = gzip.decompress(data)
            return json.loads(data.decode("utf-8"))
    except ssl.SSLCertVerificationError as exc:
        STATS.tls_errors += 1
        _log("TLS_ERROR", {"url": url, "raison": "CERTIFICAT_INVALIDE", "detail": str(exc)[:200]})
        return None
    except ssl.SSLError as exc:
        STATS.tls_errors += 1
        _log("TLS_ERROR", {"url": url, "raison": "ERREUR_TLS", "detail": str(exc)[:200]})
        return None
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", None)
        if isinstance(reason, ssl.SSLError):
            STATS.tls_errors += 1
            _log("TLS_ERROR", {"url": url, "raison": "ERREUR_TLS", "detail": str(reason)[:200]})
        else:
            STATS.http_errors += 1
        return None
    except Exception:
        STATS.http_errors += 1
        return None
