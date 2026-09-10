"""Tests de la validation HTTPS (point 1 du correctif partenaire).

Exécutable en script (python tests/test_https_validation.py) ou via pytest.
Aucun accès réseau : l'ouverture d'URL est simulée.
"""
import gzip
import io
import json
import os
import re
import ssl
import sys
import urllib.error

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import secure_http
from turf_lab.daily_sync import PMUDataFetcher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeResponse:
    def __init__(self, payload, status=200, gz=False):
        raw = json.dumps(payload).encode("utf-8")
        self._data = gzip.compress(raw) if gz else raw
        self.status = status
        self._gz = gz

    def read(self):
        return self._data

    def info(self):
        return {"Content-Encoding": "gzip"} if self._gz else {}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_contexte_tls_strict():
    ctx = secure_http.build_ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED, ctx.verify_mode
    assert ctx.check_hostname is True
    print("  [OK] test_contexte_tls_strict")


def test_http_en_clair_refuse():
    secure_http.STATS.reset()
    called = []
    out = secure_http.get_json("http://online.turfinfo.api.pmu.fr/x", opener=lambda r, c, t: called.append(1) or FakeResponse({}))
    assert out is None and called == [] and secure_http.STATS.refused_urls == 1
    assert secure_http.get_json("ftp://x", opener=lambda r, c, t: FakeResponse({})) is None
    print("  [OK] test_http_en_clair_refuse")


def test_certificat_invalide_journalise_sans_contournement():
    secure_http.STATS.reset()

    def bad_cert(req, ctx, timeout):
        assert ctx.verify_mode == ssl.CERT_REQUIRED  # le contexte transmis est bien strict
        raise ssl.SSLCertVerificationError("certificate verify failed: self signed certificate")

    out = secure_http.get_json("https://online.turfinfo.api.pmu.fr/x", opener=bad_cert)
    assert out is None and secure_http.STATS.tls_errors == 1, secure_http.STATS.as_dict()

    def bad_via_urlerror(req, ctx, timeout):
        raise urllib.error.URLError(ssl.SSLError("tlsv1 alert"))

    assert secure_http.get_json("https://x.example", opener=bad_via_urlerror) is None
    assert secure_http.STATS.tls_errors == 2
    print("  [OK] test_certificat_invalide_journalise_sans_contournement")


def test_reponse_valide_et_gzip():
    secure_http.STATS.reset()
    seen = {}

    def ok(req, ctx, timeout):
        seen["url"] = req.full_url
        seen["ctx"] = ctx
        return FakeResponse({"programme": {"reunions": []}}, gz=True)

    out = secure_http.get_json("https://online.turfinfo.api.pmu.fr/rest/client/7/programme/09092026", opener=ok)
    assert out == {"programme": {"reunions": []}} and seen["ctx"].verify_mode == ssl.CERT_REQUIRED
    assert secure_http.STATS.as_dict() == {"tls_errors": 0, "refused_urls": 0, "http_errors": 0}
    # statut != 200 => None, compté en erreur HTTP (pas TLS)
    assert secure_http.get_json("https://x.example", opener=lambda r, c, t: FakeResponse({}, status=503)) is None
    assert secure_http.STATS.http_errors == 1
    print("  [OK] test_reponse_valide_et_gzip")


def test_fetcher_pmu_utilise_https_et_le_client_securise():
    f = PMUDataFetcher()
    assert f.BASE_URL.startswith("https://")
    assert f.url_programme("09092026") == "https://online.turfinfo.api.pmu.fr/rest/client/7/programme/09092026"
    assert f.url_course("09092026", 1, 1).endswith("/09092026/R1/C1")
    # Le fetcher délègue à secure_http.get_json (une seule implémentation)
    original = secure_http.get_json
    try:
        secure_http.get_json = lambda url, timeout=10, headers=None, opener=None: {"via": "secure", "url": url}
        assert f.fetch_programme("09092026")["via"] == "secure"
    finally:
        secure_http.get_json = original
    print("  [OK] test_fetcher_pmu_utilise_https_et_le_client_securise")


def test_aucun_cert_none_dans_le_code():
    """Garde-fou : plus aucune désactivation de la vérification TLS dans turf_lab."""
    offenders = []
    for root, _dirs, files in os.walk(os.path.join(ROOT, "turf_lab")):
        for fn in files:
            if not fn.endswith(".py"):
                continue
            path = os.path.join(root, fn)
            with open(path, "r", encoding="utf-8") as fh:
                src = fh.read()
            # Les mentions dans les commentaires/docstrings sont tolérées ; le code, non.
            code_lines = [l for l in src.splitlines() if not l.strip().startswith("#")]
            code = "\n".join(code_lines)
            if re.search(r"verify_mode\s*=\s*ssl\.CERT_NONE", code) or re.search(r"check_hostname\s*=\s*False", code):
                offenders.append(os.path.relpath(path, ROOT))
    assert offenders == [], offenders
    print("  [OK] test_aucun_cert_none_dans_le_code")


def main():
    test_contexte_tls_strict()
    test_http_en_clair_refuse()
    test_certificat_invalide_journalise_sans_contournement()
    test_reponse_valide_et_gzip()
    test_fetcher_pmu_utilise_https_et_le_client_securise()
    test_aucun_cert_none_dans_le_code()
    print("\n=== 6 TESTS VALIDATION HTTPS PASSENT ===")


if __name__ == "__main__":
    main()
