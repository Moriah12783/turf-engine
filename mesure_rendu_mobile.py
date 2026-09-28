"""Mesure du rendu de site/index.html sur mobile en 4G lente (Playwright).

Profil « Slow 4G » appliqué par Lighthouse en mode DevTools : latence
562,5 ms par requête, 1,47 Mb/s descendant, 675 kb/s montant, CPU ralenti
×4, écran de smartphone. La latence par requête (150 ms × 3,75) est
l'ajustement de Lighthouse qui tient lieu des allers-retours d'une
connexion HTTPS neuve (DNS, TCP, TLS), non simulés par Chromium. Le site
est servi en local, compressé en gzip comme par Cloudflare Pages
(Cloudflare sert souvent en Brotli, plus compact : mesure prudente).

« Rendu utile » = premier affichage du cockpit du jour avec ses courses.

Usage :
    python mesure_rendu_mobile.py [dossier_site ...]
    (Chromium de Playwright, ou chemin explicite via PW_CHROMIUM)
"""

import functools
import gzip
import http.server
import json
import os
import sys
import threading
from typing import Any, Dict, Optional

SLOW_4G = {
    "offline": False,
    "latency": 150 * 3.75,                      # ms ajoutées à chaque requête
    "downloadThroughput": int(1638.4 * 0.9 * 1024 / 8),   # octets/s
    "uploadThroughput": int(750 * 0.9 * 1024 / 8),
}
CPU_RALENTI = 4
ECRAN_MOBILE = {"viewport": {"width": 412, "height": 823}, "device_scale_factor": 1.75,
                "is_mobile": True, "has_touch": True}

_COMPRESSIBLES = (".html", ".json", ".svg", ".css", ".js")

# Horodate (depuis le début de la navigation) le premier affichage du
# cockpit avec au moins une course, et le LCP.
_SONDE = """
window.__mesure = { renduUtile: null, lcp: null };
new PerformanceObserver(l => { const e = l.getEntries(); window.__mesure.lcp = e[e.length - 1].startTime; })
    .observe({ type: "largest-contentful-paint", buffered: true });
document.addEventListener("DOMContentLoaded", () => {
    const tbody = document.getElementById("cockpit-tbody");
    if (!tbody) return;
    const pret = () => tbody.querySelector("tr > td:nth-child(6)");   // une ligne de course (6 colonnes)
    const noter = () => requestAnimationFrame(() => { window.__mesure.renduUtile = performance.now(); });
    if (pret()) { noter(); return; }
    const obs = new MutationObserver(() => { if (pret()) { obs.disconnect(); noter(); } });
    obs.observe(tbody, { childList: true });
});
"""


class _GzipHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        path = self.translate_path(self.path)
        if os.path.isdir(path):
            path = os.path.join(path, "index.html")
        if not (path.endswith(_COMPRESSIBLES) and os.path.isfile(path)
                and "gzip" in self.headers.get("Accept-Encoding", "")):
            return super().do_GET()
        with open(path, "rb") as f:
            body = gzip.compress(f.read(), compresslevel=6)
        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(path))
        self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def servir(site_dir: str):
    """Sert `site_dir` sur un port libre ; retourne (url, arrêt)."""
    handler = functools.partial(_GzipHandler, directory=site_dir)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return f"http://127.0.0.1:{httpd.server_address[1]}/", httpd.shutdown


def lancer_chromium(playwright, chromium: Optional[str] = None):
    chemin = chromium or os.environ.get("PW_CHROMIUM") or None
    return playwright.chromium.launch(executable_path=chemin) if chemin else playwright.chromium.launch()


def _un_chargement(navigateur, url: str) -> Dict[str, Any]:
    """Un chargement à cache vide (nouveau contexte) en 4G lente."""
    contexte = navigateur.new_context(**ECRAN_MOBILE)
    try:
        page = contexte.new_page()
        cdp = contexte.new_cdp_session(page)
        cdp.send("Network.enable")
        cdp.send("Network.emulateNetworkConditions", SLOW_4G)
        cdp.send("Emulation.setCPUThrottlingRate", {"rate": CPU_RALENTI})
        page.add_init_script(_SONDE)
        page.goto(url, wait_until="load", timeout=120_000)
        page.wait_for_function("window.__mesure && window.__mesure.renduUtile !== null", timeout=120_000)
        page.wait_for_timeout(500)   # laisse le LCP se stabiliser
        return page.evaluate("""() => {
            const nav = performance.getEntriesByType("navigation")[0];
            const fcp = performance.getEntriesByName("first-contentful-paint")[0];
            return {
                html_recu_ms: nav.responseEnd,
                fcp_ms: fcp ? fcp.startTime : null,
                lcp_ms: window.__mesure.lcp,
                rendu_utile_ms: window.__mesure.renduUtile,
                html_transfere_octets: nav.transferSize,
                html_octets: nav.decodedBodySize,
            };
        }""")
    finally:
        contexte.close()


def mesurer(site_dir: str, essais: int = 5, chromium: Optional[str] = None) -> Dict[str, Any]:
    """Médiane de `essais` chargements à cache vide ; temps en millisecondes.

    Le navigateur est préchauffé une fois : son démarrage à froid (processus,
    polices du système) n'est pas celui d'un téléphone qui ouvre une page."""
    from playwright.sync_api import sync_playwright

    url, arreter = servir(site_dir)
    try:
        with sync_playwright() as p:
            navigateur = lancer_chromium(p, chromium)
            try:
                chauffe = navigateur.new_page()
                chauffe.goto(url, wait_until="load", timeout=120_000)
                chauffe.close()
                runs = [_un_chargement(navigateur, url) for _ in range(essais)]
            finally:
                navigateur.close()
    finally:
        arreter()
    mediane = {}
    for cle in runs[0]:
        valeurs = sorted(r[cle] for r in runs if r[cle] is not None)
        mediane[cle] = round(valeurs[len(valeurs) // 2]) if valeurs else None
    mediane["rendu_utile_essais_ms"] = [round(r["rendu_utile_ms"]) for r in runs]
    return mediane


def main(argv):
    dossiers = argv[1:] or [os.path.join(os.path.dirname(os.path.abspath(__file__)), "site")]
    for d in dossiers:
        print(d)
        print(json.dumps(mesurer(d), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main(sys.argv)
