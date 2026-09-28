"""Site léger — index.html ne porte que le jour courant, l'historique vit
dans site/archive/AAAA-MM-JJ.json (chargé à la demande par le navigateur).

Garde-fous : taille de la page publiée, archives journalières sans perte,
lignes embarquées = projection exacte des pronostics (aucune valeur
recalculée), balises SEO/Open Graph et pied de page légal.

Exécutable en script (python tests/test_site_leger.py) ou via pytest.
"""
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab.html_report import export_site_archives, generate_html_dashboard

RACINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
# Cible fixée ici, pas importée : un réglage du générateur ne peut pas
# assouplir le test en silence.
TAILLE_CIBLE_OCTETS = 300_000


def _log(date, i):
    """Course de synthèse au format de benchmark.get_historical_race_logs,
    avec un volume réaliste (16 partants, 4 éditions, tickets)."""
    rid = f"R{i // 8 + 1}C{i % 8 + 1}_{date.replace('-', '')}_HIPPO"
    sel = list(range(1, 9))
    return {
        "race_id": rid, "market_odds_available": True, "priced_ratio": 1.0,
        "edition_provisoire": False, "provisoire_reason": None, "publishable": True,
        "publication_reason": "OK", "display_horizon": "T15",
        "editions_moteur": {h: {"lock": "12:00", "sel": "1-2-3-4-5-6-7-8", "odds_real": True, "priced_ratio": 1.0}
                            for h in ("T_MATIN", "T90", "T30", "T15")},
        "editions_marche": {h: {"lock": "12:00", "sel": "2-1-3-4-6-5-8-7", "nominal": False}
                            for h in ("T_MATIN", "T90", "T30", "T15")},
        "date": date, "course": f"R{i // 8 + 1}C{i % 8 + 1} - HIPPODROME DE TEST", "race_name": "Prix de la Synthèse",
        "discipline": "TROT_ATTELE", "distance": 2700, "rope": "GAUCHE", "autostart": False,
        "scheduled_start_time": "14:00 GMT (16:00 Paris)", "is_finished": True, "status": "ARRIVÉE DÉFINITIVE",
        "is_cancelled": False, "is_started": True, "start_time_utc": f"{date}T14:00:00Z",
        "contract_recorded": True, "confidence_stars": 3, "confidence_label": "⭐⭐⭐ Confiance moyenne",
        "is_no_bet": False, "is_master": False, "np_nums": [], "sel_moteur": "1-2-3-4-5-6-7-8",
        "sel_moteur_list": sel, "bases": [1, 2], "outsider_num": 7, "regrets": [9, 10],
        "smart_tickets": {"no_bet": False, "tickets": [
            {"produit": p, "libelle": p, "texte": "1 - 2 - 3 - 4 - 5", "chevaux": [1, 2, 3, 4, 5], "eligible": True,
             "combinaisons": 10, "mise_unitaire_eur": 1.5, "cout_total_eur": 15.0}
            for p in ("COUPLE_PLACE", "COUPLE_GAGNANT", "TRIO", "QUINTE_PLUS")]},
        "sel_marche": "2-1-3-4-6-5-8-7", "sel_marche_list": [2, 1, 3, 4, 6, 5, 8, 7], "common_count": 8,
        "fav_presse": 1, "fav_marche": 2, "arrivee": "3-1-2-8-5", "arrival_list": [3, 1, 2, 8, 5],
        "couv_moteur": [3, 1, 2, 8, 5], "couv_moteur_count": 5, "couv_marche": [3, 1, 2, 8, 5],
        "couv_marche_count": 5, "couverture_label": "Couverture 5/5", "decision": "JOUER", "decision_badge": "badge-base",
        "runners": [{"num": n, "name": f"CHEVAL NUMERO {n}", "driver": "D. JOCKEY-TEST", "shoeing": "D4", "draw": n,
                     "music": "1a2a3aDa4a5a", "morning_odds": 5.0 + n, "o_t90": 5.1 + n, "o_t30": 5.2 + n, "o_t15": 5.3 + n,
                     "live_odds": 5.4 + n, "smart_signal": "STABLE", "prob_pct": 6.2, "value_index": 1.05,
                     "is_np": False, "odds_real": True} for n in range(1, 17)],
    }


def _rapport(nb_jours, courses_par_jour, dernier_jour="2026-09-27"):
    fin = datetime.strptime(dernier_jour, "%Y-%m-%d")
    logs = []
    for d in range(nb_jours):  # ordre du banc : date décroissante
        date = (fin - timedelta(days=d)).strftime("%Y-%m-%d")
        logs.extend(_log(date, i) for i in range(courses_par_jour))
    return {"evaluations": {}, "total_finished_races": len(logs), "historical_logs": logs}


def _publier(report):
    """Chaîne de main.py : archives journalières puis index.html."""
    site = tempfile.mkdtemp()
    manifest = export_site_archives(report, site)
    report_html = dict(report, archive_manifest=manifest)
    out = os.path.join(site, "index.html")
    generate_html_dashboard(report_html, output_path=out)
    return site, manifest, out


def _lignes_embarquees(html):
    m = re.search(r"let allLogs = (\[.*?\]);\n", html, re.S)
    assert m, "lignes du jour introuvables dans la page"
    return json.loads(m.group(1).replace("<\\/", "</"))


# ── Taille ────────────────────────────────────────────────────────────

def test_index_html_publie_sous_taille_cible():
    chemin = os.path.join(RACINE, "site", "index.html")
    assert os.path.exists(chemin), "site/index.html absent"
    taille = os.path.getsize(chemin)
    assert taille < TAILLE_CIBLE_OCTETS, f"site/index.html pèse {taille} octets (cible < {TAILLE_CIBLE_OCTETS})"
    print("  [OK] test_index_html_publie_sous_taille_cible")


def test_index_genere_sous_taille_cible_meme_un_gros_jour():
    # 80 courses le jour courant (record observé : 72 le 20/09/2026) et
    # 40 jours d'historique : la page ne doit pas grossir avec l'historique.
    site, _manifest, out = _publier(_rapport(nb_jours=40, courses_par_jour=80))
    taille = os.path.getsize(out)
    assert taille < TAILLE_CIBLE_OCTETS, f"index.html généré : {taille} octets"
    html = open(out, encoding="utf-8").read()
    assert "_20260926_" not in html, "une course de la veille est embarquée dans la page"
    print("  [OK] test_index_genere_sous_taille_cible_meme_un_gros_jour")


# ── Intégrité des données ─────────────────────────────────────────────

def test_archives_journalieres_sans_perte():
    report = _rapport(nb_jours=5, courses_par_jour=12)
    site, manifest, _out = _publier(report)
    relu = []
    for date in sorted(manifest, reverse=True):
        with open(os.path.join(site, "archive", f"{date}.json"), encoding="utf-8") as f:
            jour = json.load(f)
        assert len(jour) == manifest[date]
        assert all(l["date"] == date for l in jour)
        relu.extend(jour)
    # Mêmes courses, mêmes valeurs, même ordre : rien n'est recalculé.
    assert relu == report["historical_logs"]
    print("  [OK] test_archives_journalieres_sans_perte")


def test_manifeste_garde_les_journees_deja_archivees():
    # Une journée présente sur disque mais absente du rapport courant reste
    # listée : l'historique publié ne rétrécit jamais.
    site = tempfile.mkdtemp()
    export_site_archives(_rapport(nb_jours=3, courses_par_jour=4, dernier_jour="2026-09-20"), site)
    manifest = export_site_archives(_rapport(nb_jours=2, courses_par_jour=4, dernier_jour="2026-09-27"), site)
    assert set(manifest) == {"2026-09-18", "2026-09-19", "2026-09-20", "2026-09-26", "2026-09-27"}
    assert manifest["2026-09-18"] == 4
    print("  [OK] test_manifeste_garde_les_journees_deja_archivees")


def test_lignes_du_jour_projection_exacte_des_pronostics():
    report = _rapport(nb_jours=3, courses_par_jour=10)
    report["historical_logs"][0]["edition_provisoire"] = True       # cas « provisoire » conservé
    report["historical_logs"][1].pop("display_horizon")              # clé absente => reste absente
    _site, _manifest, out = _publier(report)
    lignes = _lignes_embarquees(open(out, encoding="utf-8").read())
    du_jour = [l for l in report["historical_logs"] if l["date"] == "2026-09-27"]
    assert [l["race_id"] for l in lignes] == [l["race_id"] for l in du_jour]
    for ligne, complet in zip(lignes, du_jour):
        assert "runners" not in ligne and "smart_tickets" not in ligne and "editions_moteur" not in ligne
        for cle, valeur in ligne.items():
            assert cle in complet and complet[cle] == valeur, f"{ligne['race_id']}.{cle} altéré"
        for cle in ("sel_moteur", "sel_marche", "arrivee", "decision", "couverture_label", "display_horizon"):
            assert (cle in ligne) == (cle in complet), f"{ligne['race_id']}.{cle} manquant"
    print("  [OK] test_lignes_du_jour_projection_exacte_des_pronostics")


def test_json_embarque_ne_ferme_pas_le_script():
    report = _rapport(nb_jours=1, courses_par_jour=2)
    report["historical_logs"][0]["course"] = "R1C1 - </script><b>X"
    _site, _manifest, out = _publier(report)
    html = open(out, encoding="utf-8").read()
    assert "</script><b>X" not in html
    assert _lignes_embarquees(html)[0]["course"] == "R1C1 - </script><b>X"
    print("  [OK] test_json_embarque_ne_ferme_pas_le_script")


# ── SEO, partage et mentions obligatoires ─────────────────────────────

def test_balises_seo_open_graph_et_pied_de_page_legal():
    _site, _manifest, out = _publier(_rapport(nb_jours=1, courses_par_jour=2))
    html = open(out, encoding="utf-8").read()
    assert "<title>Pronostics PMU du jour vérifiés — Elite Turf</title>" in html
    desc = re.search(r'<meta name="description" content="([^"]+)">', html)
    assert desc and 70 <= len(desc.group(1)) <= 170, "meta description absente ou hors 70-170 caractères"
    for prop in ("og:title", "og:description", "og:type", "og:url", "og:image", "og:locale", "og:site_name"):
        assert f'<meta property="{prop}"' in html, prop
    for name in ("twitter:card", "twitter:title", "twitter:description", "twitter:image"):
        assert f'<meta name="{name}"' in html, name
    assert '<link rel="canonical" href="https://prono.elite-turf.fr/">' in html
    pied = html[html.index("<footer"):html.index("</footer>")]
    assert "18+" in pied
    assert "Jouer comporte des risques : endettement, isolement, dépendance." in pied
    assert "09 74 75 13 13" in pied
    assert "Mentions légales" in pied and "Hébergeur" in pied
    print("  [OK] test_balises_seo_open_graph_et_pied_de_page_legal")


def main():
    test_index_html_publie_sous_taille_cible()
    test_index_genere_sous_taille_cible_meme_un_gros_jour()
    test_archives_journalieres_sans_perte()
    test_manifeste_garde_les_journees_deja_archivees()
    test_lignes_du_jour_projection_exacte_des_pronostics()
    test_json_embarque_ne_ferme_pas_le_script()
    test_balises_seo_open_graph_et_pied_de_page_legal()
    print("\n=== 7 TESTS SITE LÉGER PASSENT ===")


if __name__ == "__main__":
    main()
