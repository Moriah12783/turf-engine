"""Command-line interface and orchestrator for the Turf Prediction Engine & Benchmarking Lab."""

import argparse
import json
import os
import shutil
import sys
from datetime import datetime, timedelta
from turf_lab.database import TurfDatabase
from turf_lab.benchmark import TurfBenchmarkLab
from turf_lab.simulator import RaceSimulator
from turf_lab.html_report import generate_html_dashboard, export_site_archives
from turf_lab.daily_sync import DailySyncManager
from turf_lab.cloudflare_deploy import CloudflarePagesDeployer


def format_markdown_table(report: dict) -> str:
    """Format comparative evaluation into a clean Markdown table."""
    evals = report["evaluations"]
    engines = report["engines_evaluated"]
    total_races = report["total_finished_races"]

    md = []
    md.append(f"### Rapport d'Evaluation Comparatif du Banc de Mesure ({total_races} courses analysees)\n")
    md.append("| Metrique d'Evaluation | Nouveau Moteur (Value) | ETPE (Heuristique) | Synthese Presse | Favoris Marche (PMU) |")
    md.append("| :--- | :---: | :---: | :---: | :---: |")

    # Hit rates
    row_top1 = "| **Victoire Top 1 (Gagnant direct)** |"
    row_winner_top3 = "| **Gagnant dans le Top 3** |"
    row_winner_top8 = "| **Gagnant dans les 8** |"
    row_base_top3 = "| **Au moins 1 base dans le Top 3** |"
    row_both_bases = "| **Les 2 bases dans le Top 3** |"
    row_tierce = "| **Tierce dans les 8 (Desordre)** |"
    row_quinte = "| **Quinte dans les 8 (Desordre)** |"
    row_outsider = "| **Tocard / Outsider a l'arrivee (Top 3)** |"

    # Financials
    row_sg_roi = "| **ROI Simple Gagnant (Masse egale)** |"
    row_sg_dd = "| *Max Drawdown Simple Gagnant* |"
    row_sp_roi = "| **ROI Simple Place (Bases)** |"
    row_sp_dd = "| *Max Drawdown Simple Place* |"

    # Stats
    row_brier = "| **Brier Score (Calibration proba)** |"

    for eng in ["NEW_VALUE_ENGINE", "ETPE_ENGINE", "PRESS_SYNTHESIS", "MARKET_BASELINE"]:
        e = evals.get(eng, {})
        hr = e.get("hit_rates", {})
        fin = e.get("financial_performance", {})
        sg = fin.get("simple_gagnant", {})
        sp = fin.get("simple_place", {})
        stat = e.get("statistical_calibration", {})

        row_top1 += f" {hr.get('top1_win_rate_pct', 0.0):.1f}% |"
        row_winner_top3 += f" {hr.get('winner_in_top3_pct', 0.0):.1f}% |"
        row_winner_top8 += f" {hr.get('winner_in_top8_pct', 0.0):.1f}% |"
        row_base_top3 += f" {hr.get('at_least_one_base_placed_pct', 0.0):.1f}% |"
        row_both_bases += f" {hr.get('both_bases_placed_pct', 0.0):.1f}% |"
        row_tierce += f" {hr.get('tierce_in_top8_pct', 0.0):.1f}% |"
        row_quinte += f" {hr.get('quinte_in_top8_pct', 0.0):.1f}% |"
        row_outsider += f" {hr.get('outsider_placed_pct', 0.0):.1f}% |"

        sg_roi = sg.get('roi_pct', 0.0)
        sp_roi = sp.get('roi_pct', 0.0)
        row_sg_roi += f" **{sg_roi:+.1f}%** |"
        row_sg_dd += f" -{sg.get('max_drawdown_eur', 0.0):.1f}EUR |"
        row_sp_roi += f" **{sp_roi:+.1f}%** |"
        row_sp_dd += f" -{sp.get('max_drawdown_eur', 0.0):.1f}EUR |"

        brier = stat.get("brier_score")
        row_brier += f" {brier:.4f} |" if brier is not None else " N/A |"

    md.extend([
        row_top1, row_winner_top3, row_winner_top8,
        row_base_top3, row_both_bases, row_tierce, row_quinte, row_outsider,
        "| **--- PERFORMANCE FINANCIERE ---** | | | | |",
        row_sg_roi, row_sg_dd, row_sp_roi, row_sp_dd,
        "| **--- CALIBRATION STATISTIQUE ---** | | | | |",
        row_brier
    ])

    return "\n".join(md)


def main():
    script_dir = os.path.dirname(os.path.abspath(__file__))
    default_db = os.path.join(script_dir, "turf_bench.db")
    default_export = os.path.join(script_dir, "benchmark_report.json")
    default_html = os.path.join(script_dir, "benchmark_dashboard.html")
    default_config = os.path.join(script_dir, "config.json")

    parser = argparse.ArgumentParser(description="Turf Prediction Engine & Benchmarking Lab")
    parser.add_argument("--action", type=str, default=None, choices=["sync", "simulate", "evaluate"],
                        help="Action to perform: sync (fetch real PMU feeds), simulate (run benchmark), evaluate (generate reports)")
    parser.add_argument("--db", type=str, default=default_db, help="Path to SQLite database")
    parser.add_argument("--simulate", type=int, default=None, help="Number of simulated races to run (for --action simulate)")
    parser.add_argument("--days", type=int, default=7, help="Number of past days to sync for live data (defaults to 7 days)")
    parser.add_argument("--export", type=str, default=default_export, help="Path to export JSON report")
    parser.add_argument("--html", type=str, default=default_html, help="Path to export HTML dashboard")
    parser.add_argument("--config", type=str, default=default_config, help="Path to Cloudflare config.json")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    parser.add_argument("--date", type=str, default=None, help="Target date YYYY-MM-DD for sync")
    args = parser.parse_args()

    action = args.action
    if action is None:
        if args.simulate is not None:
            action = "simulate"
        else:
            action = "sync"

    db = TurfDatabase(args.db)

    if action == "simulate":
        num_races = args.simulate if args.simulate is not None else 300
        print(f"[*] Initialisation de la simulation sur {num_races} courses (seed={args.seed})...")
        sim = RaceSimulator(db, seed=args.seed)
        sim.run_benchmark_simulation(num_races)
        print("[+] Simulation terminee avec succes.")

    elif action == "sync":
        print(f"[*] Synchronisation des courses reelles PMU (Fenetre de {args.days} jours)...")
        manager = DailySyncManager(db)
        
        target_dates = []
        if args.date:
            target_dates.append(datetime.strptime(args.date, "%Y-%m-%d"))
        else:
            now = datetime.now()
            days_count = max(1, args.days)
            for i in range(days_count - 1, -1, -1):
                target_dates.append(now - timedelta(days=i))

        for d in target_dates:
            d_str = d.strftime("%Y-%m-%d")
            print(f"[*] Traitement du programme PMU pour la date : {d_str}...")
            stats = manager.sync_date(d)
            print(f"    - Courses ajoutees : {stats['races_added']}")
            print(f"    - Pronostics verrouilles : {stats['predictions_locked']}")
            print(f"    - Resultats resolus : {stats['results_resolved']}")

        if len(db.get_finished_races()) == 0:
            print("[*] Injection des reunions reelles verifiees (Vincennes & Cabourg)...")
            manager.inject_recent_real_meetings()
            print("[+] Reunions reelles injectees avec succes.")

    print("[*] Calcul des metriques d'evaluation sur le banc de mesure...")
    lab = TurfBenchmarkLab(db)
    report = lab.generate_comparative_report()

    # 1. Save JSON report
    with open(args.export, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"[+] Rapport JSON exporte dans : {os.path.abspath(args.export)}")

    # 2. Historique permanent : archives mensuelles statiques dans site/archive/
    #    (les ~3 dernieres semaines restent embarquees dans index.html,
    #    le reste est charge a la demande par le navigateur pour la recherche).
    site_dir = os.path.join(script_dir, "site")
    os.makedirs(site_dir, exist_ok=True)
    recent_logs, archive_manifest = export_site_archives(report, site_dir)
    if archive_manifest:
        total_archived = sum(archive_manifest.values())
        print(f"[+] Archives mensuelles ecrites : {len(archive_manifest)} mois, {total_archived} courses (site/archive/).")

    report_for_html = dict(report)
    report_for_html["historical_logs"] = recent_logs if recent_logs else report.get("historical_logs", [])
    report_for_html["archive_manifest"] = archive_manifest

    # 3. Generate HTML Dashboard
    html_path = generate_html_dashboard(report_for_html, output_path=args.html)
    print(f"[+] Tableau de bord HTML genere dans : {html_path}")

    # 4. Synchronize site/index.html automatically
    site_index = os.path.join(site_dir, "index.html")
    shutil.copyfile(html_path, site_index)
    print(f"[+] Dossier local 'site/index.html' synchronise automatiquement.")

    # 5. Automatic Cloudflare Pages Deployment
    deployer = CloudflarePagesDeployer.from_config(config_path=args.config)
    deploy_res = deployer.deploy_direct(site_dir=site_dir)
    if deploy_res.get("success"):
        print(f"[+] Deploiement Cloudflare reussi ! En ligne sur : {deploy_res.get('custom_domain', 'https://prono.elite-turf.fr')}")
    else:
        print(f"[*] Cloudflare : {deploy_res.get('message', 'site/index.html pret.')}")

    # Print table
    table_md = format_markdown_table(report)
    print("\n" + table_md)


if __name__ == "__main__":
    main()
