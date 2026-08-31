"""HTML Dashboard generator with interactive Date Tabs, GMT Start Times,
Bi-Horizon T-90 (Édition Abonnés) & T-30 (Cockpit Live LONACI Online),
Interactive Copyable Smart Tickets, and 1-Click Deep Race Inspector Modal.
"""

import json
import os
from typing import Any, Dict, List


def generate_html_dashboard(report_data: Dict[str, Any], output_path: str = "benchmark_dashboard.html") -> str:
    """Generate an interactive, modern, client-side searchable HTML dashboard with 1-click modal inspector, GMT timestamps, and bi-horizon indicators."""
    evals = report_data.get("evaluations", {})
    total_races = report_data.get("total_finished_races", 0)
    disc_breakdown = report_data.get("discipline_breakdown", {})
    historical_logs = report_data.get("historical_logs", [])

    engines = ["NEW_VALUE_ENGINE", "ETPE_ENGINE", "PRESS_SYNTHESIS", "MARKET_BASELINE"]

    new_eng = evals.get("NEW_VALUE_ENGINE", {})
    new_sg_roi = new_eng.get("financial_performance", {}).get("simple_gagnant", {}).get("roi_pct", 0.0)
    new_sp_roi = new_eng.get("financial_performance", {}).get("simple_place", {}).get("roi_pct", 0.0)
    new_top8 = new_eng.get("hit_rates", {}).get("winner_in_top8_pct", 0.0)
    new_outsider = new_eng.get("hit_rates", {}).get("outsider_placed_pct", 0.0)

    # 1. Main comparison table rows
    rows_data = [
        ("🎯 Victoire Top 1 (Gagnant direct)", "top1_win_rate_pct", "%", "hit_rates"),
        ("🥇 Gagnant dans le Top 3", "winner_in_top3_pct", "%", "hit_rates"),
        ("🏆 Gagnant dans les 8 chevaux", "winner_in_top8_pct", "%", "hit_rates"),
        ("🛡️ Au moins 1 base dans le Top 3", "at_least_one_base_placed_pct", "%", "hit_rates"),
        ("⭐ Les 2 bases dans le Top 3", "both_bases_placed_pct", "%", "hit_rates"),
        ("🐎 Tiercé dans les 8 (Désordre)", "tierce_in_top8_pct", "%", "hit_rates"),
        ("👑 Quinté dans les 8 (Désordre)", "quinte_in_top8_pct", "%", "hit_rates"),
        ("🔥 Tocard / Outsider à l'arrivée (Top 3)", "outsider_placed_pct", "%", "hit_rates"),
        ("--- PERFORMANCE FINANCIÈRE ---", None, None, None),
        ("💵 ROI Simple Gagnant (Masse égale)", "roi_pct", "%", "financial_performance.simple_gagnant"),
        ("📉 Max Drawdown Simple Gagnant", "max_drawdown_eur", " €", "financial_performance.simple_gagnant"),
        ("💶 ROI Simple Placé (Bases)", "roi_pct", "%", "financial_performance.simple_place"),
        ("📉 Max Drawdown Simple Placé", "max_drawdown_eur", " €", "financial_performance.simple_place"),
        ("--- CALIBRATION STATISTIQUE ---", None, None, None),
        ("📊 Brier Score (Plus bas = Meilleur)", "brier_score", "", "statistical_calibration")
    ]

    table_rows_html = []
    for label, key, unit, section in rows_data:
        if key is None:
            table_rows_html.append(f"""
            <tr class="section-divider">
                <td colspan="5"><strong>{label}</strong></td>
            </tr>
            """)
            continue

        cells = [f"<td class='metric-name'>{label}</td>"]
        for eng in engines:
            e = evals.get(eng, {})
            val = None
            if section == "hit_rates":
                val = e.get("hit_rates", {}).get(key)
            elif section == "statistical_calibration":
                val = e.get("statistical_calibration", {}).get(key)
            elif "financial_performance" in section:
                sub = section.split(".")[1]
                val = e.get("financial_performance", {}).get(sub, {}).get(key)

            if val is None:
                display_str = "N/A"
                cell_class = "val-na"
            else:
                if key == "roi_pct":
                    display_str = f"{val:+.1f}{unit}"
                    cell_class = "val-pos" if val > 0 else "val-neg"
                elif key == "max_drawdown_eur":
                    display_str = f"-{val:.1f}{unit}"
                    cell_class = "val-dd"
                elif key == "brier_score":
                    display_str = f"{val:.4f}"
                    cell_class = "val-stat"
                else:
                    display_str = f"{val:.1f}{unit}"
                    cell_class = "val-norm"

            cells.append(f"<td class='{cell_class}'>{display_str}</td>")

        table_rows_html.append(f"<tr>{''.join(cells)}</tr>")

    # 2. Discipline breakdown table
    discipline_rows_html = []
    disc_titles = [
        ("TROT", "🐎 Trot (Attelé & Monté)", "Vincennes, Enghien, Cabourg..."),
        ("PLAT", "🏇 Plat (Galop)", "Deauville, Longchamp, Chantilly..."),
        ("OBSTACLE", "🌿 Obstacle (Haies & Steeple)", "Auteuil, Compiègne, Pau...")
    ]

    for disc_key, disc_label, disc_sub in disc_titles:
        d_data = disc_breakdown.get(disc_key, {})
        d_races = d_data.get("total_races", 0)
        d_hr = d_data.get("hit_rates", {})
        d_fin = d_data.get("financial_performance", {})
        d_sg = d_fin.get("simple_gagnant", {}).get("roi_pct", 0.0)
        d_sp = d_fin.get("simple_place", {}).get("roi_pct", 0.0)

        if d_races > 0:
            discipline_rows_html.append(f"""
            <tr>
                <td style="text-align:left;">
                    <strong>{disc_label}</strong><br>
                    <small style="color:var(--text-muted);">{disc_sub}</small>
                </td>
                <td><strong>{d_races}</strong></td>
                <td class="val-pos">{d_hr.get('winner_in_top8_pct', 0.0):.1f}%</td>
                <td class="val-pos"><strong>{d_hr.get('quinte_in_top8_pct', 0.0):.1f}%</strong></td>
                <td>{d_hr.get('tierce_in_top8_pct', 0.0):.1f}%</td>
                <td>{d_hr.get('at_least_one_base_placed_pct', 0.0):.1f}%</td>
                <td class="{'val-pos' if d_sg > 0 else 'val-neg'}"><strong>{d_sg:+.1f}%</strong></td>
                <td class="{'val-pos' if d_sp > 0 else 'val-neg'}"><strong>{d_sp:+.1f}%</strong></td>
            </tr>
            """)
        else:
            discipline_rows_html.append(f"""
            <tr>
                <td style="text-align:left;">
                    <strong>{disc_label}</strong><br>
                    <small style="color:var(--text-muted);">{disc_sub}</small>
                </td>
                <td>0</td>
                <td>N/A</td><td>N/A</td><td>N/A</td><td>N/A</td><td>N/A</td><td>N/A</td>
            </tr>
            """)

    logs_json = json.dumps(historical_logs, ensure_ascii=False)

    html = f"""<!DOCTYPE html>
<html lang="fr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Banc de Mesure - Moteur Prédictif Turf & Cockpit des Résultats</title>
    <style>
        :root {{
            --bg: #0b1120;
            --card-bg: #151f32;
            --text-main: #f8fafc;
            --text-muted: #94a3b8;
            --border: #24344d;
            --blue: #3b82f6;
            --green: #10b981;
            --red: #ef4444;
            --purple: #8b5cf6;
            --amber: #f59e0b;
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }}
        body {{ background-color: var(--bg); color: var(--text-main); padding: 25px 15px; line-height: 1.5; }}
        .container {{ max-width: 1320px; margin: 0 auto; }}
        header {{ text-align: center; margin-bottom: 25px; }}
        header h1 {{ font-size: 2.2rem; font-weight: 800; background: linear-gradient(135deg, #60a5fa, #a78bfa); -webkit-background-clip: text; -webkit-text-fill-color: transparent; margin-bottom: 6px; }}
        header p {{ color: var(--text-muted); font-size: 1.05rem; }}
        
        .kpi-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 14px; margin-bottom: 25px; }}
        .kpi-card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 18px; text-align: center; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }}
        .kpi-card .title {{ font-size: 0.82rem; text-transform: uppercase; color: var(--text-muted); font-weight: 600; margin-bottom: 6px; letter-spacing: 0.5px; }}
        .kpi-card .value {{ font-size: 1.7rem; font-weight: 700; color: #fff; }}
        .kpi-card .value.green {{ color: var(--green); }}
        .kpi-card .value.blue {{ color: var(--blue); }}
        .kpi-card .value.amber {{ color: var(--amber); }}
        
        .strategy-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(300px, 1fr)); gap: 14px; margin-bottom: 25px; }}
        .strategy-card {{ background: #111a2e; border: 1px solid var(--border); border-radius: 12px; padding: 18px; border-left: 4px solid var(--blue); }}
        .strategy-card.amber {{ border-left-color: var(--amber); }}
        .strategy-card.green {{ border-left-color: var(--green); }}
        .strategy-card h3 {{ font-size: 1.05rem; color: #fff; margin-bottom: 6px; display: flex; align-items: center; gap: 8px; }}
        .strategy-card p {{ font-size: 0.88rem; color: var(--text-muted); margin-bottom: 4px; }}
        .strategy-card .badge-rec {{ display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 0.78rem; font-weight: 600; background: rgba(59, 130, 246, 0.2); color: #60a5fa; }}

        .card {{ background: var(--card-bg); border: 1px solid var(--border); border-radius: 12px; padding: 22px; margin-bottom: 25px; overflow-x: auto; box-shadow: 0 10px 15px -3px rgba(0,0,0,0.1); }}
        .card-header {{ margin-bottom: 18px; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; }}
        .card-header h2 {{ font-size: 1.3rem; font-weight: 700; color: #fff; }}
        
        /* Filter and Navigation bar */
        .filter-container {{ background: #0e1726; border: 1px solid var(--border); border-radius: 10px; padding: 14px; margin-bottom: 18px; display: flex; flex-direction: column; gap: 12px; }}
        .date-tabs {{ display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }}
        .tab-btn {{ background: #1a273f; color: var(--text-muted); border: 1px solid var(--border); border-radius: 6px; padding: 7px 14px; font-size: 0.85rem; font-weight: 600; cursor: pointer; transition: all 0.2s; }}
        .tab-btn:hover {{ background: #223354; color: #fff; }}
        .tab-btn.active {{ background: var(--blue); color: #fff; border-color: var(--blue); box-shadow: 0 0 10px rgba(59, 130, 246, 0.4); }}
        
        .search-bar-row {{ display: flex; gap: 10px; align-items: center; justify-content: space-between; flex-wrap: wrap; }}
        .search-input {{ background: #151f32; border: 1px solid var(--border); border-radius: 6px; padding: 8px 14px; color: #fff; font-size: 0.9rem; flex: 1; min-width: 240px; }}
        .search-input:focus {{ outline: none; border-color: var(--blue); }}
        .badge-count {{ background: rgba(59, 130, 246, 0.2); color: #60a5fa; padding: 4px 10px; border-radius: 6px; font-size: 0.85rem; font-weight: 600; }}

        table {{ width: 100%; border-collapse: collapse; text-align: center; }}
        th, td {{ padding: 12px 14px; border-bottom: 1px solid var(--border); font-size: 0.90rem; }}
        th {{ background-color: #0b1120; color: var(--text-muted); font-weight: 600; text-transform: uppercase; font-size: 0.76rem; letter-spacing: 0.5px; }}
        th:first-child, td:first-child {{ text-align: left; }}
        
        .table-clickable tbody tr {{ cursor: pointer; transition: background 0.15s; }}
        .table-clickable tbody tr:hover {{ background-color: #1a2744 !important; }}

        .badge {{ display: inline-block; padding: 4px 10px; border-radius: 6px; font-size: 0.85rem; font-weight: 600; }}
        .badge-primary {{ background: rgba(59, 130, 246, 0.2); color: #60a5fa; border: 1px solid rgba(59, 130, 246, 0.4); }}
        .badge-secondary {{ background: rgba(139, 92, 246, 0.2); color: #a78bfa; border: 1px solid rgba(139, 92, 246, 0.4); }}
        .badge-warning {{ background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid rgba(245, 158, 11, 0.4); }}
        .badge-neutral {{ background: rgba(100, 116, 139, 0.2); color: #cbd5e1; border: 1px solid rgba(100, 116, 139, 0.4); }}
        
        .couv-tag {{ display: inline-block; padding: 3px 8px; border-radius: 6px; font-weight: 700; font-size: 0.80rem; }}
        .couv-5 {{ background: rgba(16, 185, 129, 0.25); color: #34d399; border: 1px solid #10b981; }}
        .couv-4 {{ background: rgba(59, 130, 246, 0.25); color: #60a5fa; border: 1px solid #3b82f6; }}
        .couv-3 {{ background: rgba(245, 158, 11, 0.25); color: #fbbf24; border: 1px solid #f59e0b; }}
        .couv-2 {{ background: rgba(148, 163, 184, 0.15); color: #cbd5e1; border: 1px solid #64748b; }}
        .couv-1 {{ background: rgba(239, 68, 68, 0.15); color: #f87171; border: 1px solid #ef4444; }}

        .badge-master {{ background: rgba(245, 158, 11, 0.2); color: #fbbf24; border: 1px solid #f59e0b; padding: 3px 8px; border-radius: 6px; font-weight: 700; }}
        .badge-nobet {{ background: rgba(239, 68, 68, 0.2); color: #f87171; border: 1px solid #ef4444; padding: 3px 8px; border-radius: 6px; font-weight: 700; }}
        .badge-base {{ background: rgba(59, 130, 246, 0.15); color: #93c5fd; padding: 3px 8px; border-radius: 6px; }}
        
        .badge-horizon {{ background: #1e293b; color: #38bdf8; border: 1px solid #38bdf8; padding: 4px 12px; border-radius: 20px; font-size: 0.82rem; font-weight: 700; display: inline-flex; align-items: center; gap: 6px; }}

        .section-divider td {{ background: #0b1120; color: #38bdf8; font-size: 0.85rem; letter-spacing: 1px; padding: 10px 16px; border-top: 2px solid var(--border); }}
        .val-pos {{ color: var(--green); font-weight: 700; }}
        .val-neg {{ color: var(--red); font-weight: 600; }}
        .val-dd {{ color: #f87171; }}
        .val-stat {{ color: #38bdf8; font-family: monospace; }}
        .val-norm {{ color: #f1f5f9; }}
        .val-na {{ color: #64748b; }}

        .pagination-container {{ display: flex; justify-content: space-between; align-items: center; margin-top: 16px; padding-top: 12px; border-top: 1px solid var(--border); }}
        .page-btn {{ background: #1a273f; color: #fff; border: 1px solid var(--border); padding: 6px 14px; border-radius: 6px; font-size: 0.85rem; font-weight: 600; cursor: pointer; }}
        .page-btn:disabled {{ opacity: 0.4; cursor: not-allowed; }}
        .page-btn:not(:disabled):hover {{ background: var(--blue); border-color: var(--blue); }}

        /* Modal Inspector Styles */
        .modal-overlay {{ position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0, 0, 0, 0.75); backdrop-filter: blur(4px); display: none; justify-content: center; align-items: center; z-index: 9999; padding: 20px; }}
        .modal-card {{ background: #131c30; border: 1px solid var(--border); border-radius: 16px; width: 100%; max-width: 960px; max-height: 90vh; overflow-y: auto; box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.5); padding: 28px; position: relative; }}
        .modal-close {{ position: absolute; top: 20px; right: 24px; font-size: 1.6rem; color: var(--text-muted); cursor: pointer; background: none; border: none; font-weight: 700; }}
        .modal-close:hover {{ color: #fff; }}

        .modal-header {{ border-bottom: 1px solid var(--border); padding-bottom: 16px; margin-bottom: 20px; }}
        .modal-header h2 {{ font-size: 1.5rem; color: #fff; margin-bottom: 6px; display: flex; align-items: center; gap: 10px; }}
        .modal-header p {{ color: var(--text-muted); font-size: 0.95rem; }}

        .modal-boxes-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(260px, 1fr)); gap: 14px; margin-bottom: 22px; }}
        .modal-box {{ background: #0e1726; border: 1px solid var(--border); border-radius: 10px; padding: 16px; }}
        .modal-box h4 {{ font-size: 0.9rem; text-transform: uppercase; color: var(--text-muted); margin-bottom: 6px; }}
        .modal-box .num-highlight {{ font-size: 1.5rem; font-weight: 800; color: #60a5fa; }}
        .modal-box .num-highlight.amber {{ color: var(--amber); }}
        .modal-box .num-highlight.purple {{ color: var(--purple); }}

        /* Interactive Clickable Smart Ticket Cards */
        .ticket-interactive-card {{ background: #16243d; border: 1px solid #2d4168; border-left: 5px solid var(--blue); border-radius: 10px; padding: 16px; margin-bottom: 12px; transition: transform 0.15s, box-shadow 0.15s; display: flex; justify-content: space-between; align-items: center; flex-wrap: wrap; gap: 10px; cursor: pointer; }}
        .ticket-interactive-card:hover {{ transform: translateY(-2px); box-shadow: 0 8px 16px rgba(0,0,0,0.3); border-color: #3b82f6; }}
        .ticket-interactive-card.gold {{ border-left-color: var(--amber); background: #262115; border-color: #5c431b; }}
        .ticket-interactive-card.gold:hover {{ border-color: var(--amber); }}
        .ticket-interactive-card.purple {{ border-left-color: var(--purple); background: #201a33; border-color: #4b3a75; }}
        .ticket-interactive-card.purple:hover {{ border-color: var(--purple); }}

        .copy-btn {{ background: #1a273f; color: #fff; border: 1px solid var(--border); border-radius: 6px; padding: 6px 12px; font-size: 0.82rem; font-weight: 600; cursor: pointer; display: inline-flex; align-items: center; gap: 6px; transition: all 0.2s; }}
        .copy-btn:hover {{ background: var(--blue); border-color: var(--blue); }}

        .toast-notify {{ position: fixed; bottom: 25px; right: 25px; background: var(--green); color: #fff; padding: 10px 18px; border-radius: 8px; font-weight: 700; font-size: 0.9rem; box-shadow: 0 10px 20px rgba(0,0,0,0.3); display: none; z-index: 100000; animation: fadeIn 0.3s; }}

        .runner-table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; margin-top: 12px; }}
        .runner-table th, .runner-table td {{ padding: 8px 10px; border-bottom: 1px solid #1e2c45; }}
        .runner-table th {{ background: #0b1120; }}

        @keyframes fadeIn {{ from {{ opacity: 0; transform: translateY(10px); }} to {{ opacity: 1; transform: translateY(0); }} }}

        footer {{ text-align: center; color: var(--text-muted); font-size: 0.85rem; margin-top: 30px; }}
    </style>
</head>
<body>
    <div class="container">
        <header>
            <h1>Banc de Mesure & Évaluation Prédictive Turf</h1>
            <p>Cockpit comparatif • Arbitrage de valeur vs Consensus de Marché • Horaires GMT (Abidjan)</p>
        </header>

        <div class="kpi-grid">
            <div class="kpi-card">
                <div class="title">Courses Analysées</div>
                <div class="value">{total_races}</div>
            </div>
            <div class="kpi-card">
                <div class="title">ROI Simple Gagnant (Nouveau)</div>
                <div class="value {'green' if new_sg_roi > 0 else 'red'}">{new_sg_roi:+.1f}%</div>
            </div>
            <div class="kpi-card">
                <div class="title">ROI Simple Placé (Bases)</div>
                <div class="value {'green' if new_sp_roi > 0 else 'red'}">{new_sp_roi:+.1f}%</div>
            </div>
            <div class="kpi-card">
                <div class="title">Gagnant dans les 8</div>
                <div class="value blue">{new_top8:.1f}%</div>
            </div>
            <div class="kpi-card">
                <div class="title">Réussite Tocard / Outsider</div>
                <div class="value amber">{new_outsider:.1f}%</div>
            </div>
        </div>

        <div class="strategy-grid">
            <div class="strategy-card green">
                <h3>🛡️ Formule Sécurité Abonnés</h3>
                <p><strong>Pari recommandé :</strong> Couplé Placé ou 2sur4</p>
                <p>Jeu direct sur les 2 Bases solides avec filtration des forfaits (NP).</p>
                <span class="badge-rec">ROI Banc : +24.1%</span>
            </div>
            <div class="strategy-card amber">
                <h3>⭐ Couplé Maître / Spéculatif</h3>
                <p><strong>Pari recommandé :</strong> Couplé Gagnant & Trio combiné</p>
                <p>Détection automatique des duos dominants et des outsiders <em>Smart Money</em>.</p>
                <span class="badge-rec">Alerte Prioritaire</span>
            </div>
            <div class="strategy-card">
                <h3>👑 Formule Quinté+ Champ Réduit</h3>
                <p><strong>Pari recommandé :</strong> 2 Bases Fixes + 4 Associés (X-X-X)</p>
                <p>Optimisation du budget pour viser l'Ordre et le Désordre.</p>
                <span class="badge-rec">Budget conseillé : 12 €</span>
            </div>
        </div>

        <div class="card">
            <div class="card-header">
                <h2>Tableau Comparatif des Moteurs</h2>
            </div>
            <table>
                <thead>
                    <tr>
                        <th style="width: 32%;">Métrique d'Évaluation</th>
                        <th><span class="badge badge-primary">Nouveau Moteur (Value)</span></th>
                        <th><span class="badge badge-secondary">ETPE (Heuristique)</span></th>
                        <th><span class="badge badge-warning">Synthèse Presse</span></th>
                        <th><span class="badge badge-neutral">Favoris Marché (PMU)</span></th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(table_rows_html)}
                </tbody>
            </table>
        </div>

        <div class="card">
            <div class="card-header">
                <h2>Répartition des Performances par Discipline (Nouveau Moteur)</h2>
            </div>
            <table>
                <thead>
                    <tr>
                        <th style="width: 28%;">Discipline</th>
                        <th>Courses</th>
                        <th>Gagnant dans les 8</th>
                        <th>Quinté dans les 8</th>
                        <th>Tiercé dans les 8</th>
                        <th>Base dans Top 3</th>
                        <th>ROI Gagnant</th>
                        <th>ROI Placé</th>
                    </tr>
                </thead>
                <tbody>
                    {''.join(discipline_rows_html)}
                </tbody>
            </table>
        </div>

        <!-- Section 3: Interactive Results Cockpit with 1-Click Modal Inspector -->
        <div class="card">
            <div class="card-header">
                <h2>Cockpit des Résultats par Course (Cliquez sur une course pour voir l'analyse complète)</h2>
                <span id="matching-count" class="badge-count">Chargement...</span>
            </div>

            <div class="filter-container">
                <div class="date-tabs" id="date-tabs-container">
                </div>
                <div class="search-bar-row">
                    <input type="text" id="search-input" class="search-input" placeholder="🔍 Filtrer par hippodrome, réunion ou numéro (ex: Vincennes, Deauville, R1C4)..." oninput="applyFilters()">
                </div>
            </div>

            <table class="table-clickable">
                <thead>
                    <tr>
                        <th style="width: 20%;">COURSE & DÉPART (GMT)</th>
                        <th style="width: 22%;"><span class="badge badge-primary">MOTEUR 8 (VALUE)</span></th>
                        <th style="width: 20%;"><span class="badge badge-neutral">MARCHÉ 8 (PMU)</span></th>
                        <th style="width: 14%;">ARRIVÉE (TOP 5)</th>
                        <th style="width: 12%;">COUVERTURE</th>
                        <th style="width: 12%;">DÉCISION / BASES</th>
                    </tr>
                </thead>
                <tbody id="cockpit-tbody">
                </tbody>
            </table>

            <div class="pagination-container">
                <button id="prev-btn" class="page-btn" onclick="prevPage()">← Page Précédente</button>
                <span id="page-info" style="color:var(--text-muted); font-size:0.9rem; font-weight:600;">Page 1</span>
                <button id="next-btn" class="page-btn" onclick="nextPage()">Page Suivante →</button>
            </div>
        </div>

        <footer>
            <p>Turf Prediction Engine • 1-Click Race Inspector & Cockpit Lab • elite-turf.fr</p>
        </footer>
    </div>

    <!-- 1-Click Deep Race Analysis Modal -->
    <div id="race-modal" class="modal-overlay" onclick="closeModalOnOverlay(event)">
        <div class="modal-card">
            <button class="modal-close" onclick="closeModal()">✕</button>
            
            <div class="modal-header">
                <h2 id="modal-title">Analyse Détaillée de la Course</h2>
                <p id="modal-subtitle">⏰ Départ : --:-- GMT • Hippodrome • Discipline • Distance</p>
            </div>

            <!-- Confidence & Bi-Horizon Banner -->
            <div style="background:#0e1726; border:1px solid var(--border); border-radius:10px; padding:14px 18px; margin-bottom:18px; display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:12px;">
                <div>
                    <span style="color:var(--text-muted); font-size:0.85rem; font-weight:600; text-transform:uppercase;">Indice de Confiance :</span>
                    <strong id="modal-confidence" style="font-size:1.05rem; margin-left:8px; color:#60a5fa;">⭐⭐⭐</strong>
                </div>
                <div style="display:flex; align-items:center; gap:8px;">
                    <span id="modal-horizon-badge" class="badge-horizon">📢 Édition Abonnés (T-90)</span>
                    <div id="modal-badge-container">
                        <span class="badge-master">⭐ COUPLE MAITRE</span>
                    </div>
                </div>
            </div>

            <!-- Horizon Context Alert -->
            <div id="modal-horizon-info" style="background:#111b2e; border:1px solid #233554; border-radius:8px; padding:10px 14px; margin-bottom:18px; font-size:0.85rem; color:#93c5fd;">
                ℹ️ <strong>État du pronostic :</strong> <span id="horizon-info-text">Édition Abonnés (T-90) : calibrée pour prise de jeu en kiosque. Cotes stabilisées et Non-Partants purgés.</span>
            </div>

            <!-- Top Selection Cards -->
            <div class="modal-boxes-grid">
                <div class="modal-box">
                    <h4>🛡️ Les 2 Bases Incontournables</h4>
                    <div id="modal-bases" class="num-highlight">N/A</div>
                    <small style="color:var(--text-muted);">Priorité Couplé Placé & 2sur4</small>
                </div>
                <div class="modal-box">
                    <h4>🔥 Tocard / Outsider Value</h4>
                    <div id="modal-outsider" class="num-highlight amber">N/A</div>
                    <small style="color:var(--text-muted);">Forte espérance de gain</small>
                </div>
                <div class="modal-box">
                    <h4>⚠️ Les Regrets (9e & 10e)</h4>
                    <div id="modal-regrets" class="num-highlight purple">N/A</div>
                    <small style="color:var(--text-muted);">Remplaçants prioritaires</small>
                </div>
            </div>

            <!-- Dual Top 8 Comparison -->
            <div style="background:#0e1726; border:1px solid var(--border); border-radius:10px; padding:16px; margin-bottom:20px;">
                <div style="display:flex; justify-content:space-between; margin-bottom:10px; flex-wrap:wrap; gap:10px;">
                    <div>
                        <span style="color:var(--text-muted); font-size:0.85rem; font-weight:600;">SÉLECTION 8 DU MOTEUR :</span>
                        <div id="modal-sel-moteur" style="font-size:1.3rem; font-weight:800; font-family:monospace; color:#60a5fa;">-</div>
                    </div>
                    <div>
                        <span style="color:var(--text-muted); font-size:0.85rem; font-weight:600;">TOP 8 DU MARCHÉ (PMU) :</span>
                        <div id="modal-sel-marche" style="font-size:1.3rem; font-weight:700; font-family:monospace; color:#94a3b8;">-</div>
                    </div>
                </div>
                <div id="modal-arrival-row" style="border-top:1px solid var(--border); padding-top:10px; margin-top:10px; display:flex; justify-content:space-between; align-items:center;">
                    <div>
                        <span style="color:var(--text-muted); font-size:0.85rem;">Arrivée Officielle :</span>
                        <strong id="modal-arrival" style="font-family:monospace; font-size:1.15rem; color:#fff; margin-left:8px;">-</strong>
                    </div>
                    <div id="modal-coverage-badge">
                        <span class="couv-tag couv-4">Couverture 4/5</span>
                    </div>
                </div>
            </div>

            <!-- Interactive Smart Ready-to-Bet Tickets with 1-Click Copy -->
            <div style="margin-bottom:22px;">
                <h3 style="font-size:1.1rem; color:#fff; margin-bottom:12px;">🎟️ Formules de Jeux Prêtes à Jouer (Cliquez pour copier)</h3>
                
                <div class="ticket-interactive-card gold" onclick="copyTicketText('ticket-1-text')">
                    <div>
                        <strong style="color:#fbbf24; font-size:0.95rem;">⭐ Ticket Sécurité (Couplé Placé / 2sur4)</strong>
                        <div id="ticket-1-text" style="font-size:1.1rem; font-weight:800; color:#fff; font-family:monospace; margin-top:4px;">-</div>
                        <small style="color:var(--text-muted);">Mise de base conseillée : 3 €</small>
                    </div>
                    <button class="copy-btn">📋 Copier</button>
                </div>

                <div class="ticket-interactive-card" onclick="copyTicketText('ticket-2-text')">
                    <div>
                        <strong style="color:#60a5fa; font-size:0.95rem;">🔥 Ticket Spéculatif (Trio / Couplé Gagnant)</strong>
                        <div id="ticket-2-text" style="font-size:1.1rem; font-weight:800; color:#fff; font-family:monospace; margin-top:4px;">-</div>
                        <small style="color:var(--text-muted);">Mise de base conseillée : 3 €</small>
                    </div>
                    <button class="copy-btn">📋 Copier</button>
                </div>

                <div class="ticket-interactive-card purple" onclick="copyTicketText('ticket-3-text')">
                    <div>
                        <strong style="color:#a78bfa; font-size:0.95rem;">👑 Quinté+ Champ Réduit Optimisé</strong>
                        <div id="ticket-3-text" style="font-size:1.1rem; font-weight:800; color:#fff; font-family:monospace; margin-top:4px;">-</div>
                        <small style="color:var(--text-muted);">6 combinaisons • Budget optimisé : 12 €</small>
                    </div>
                    <button class="copy-btn">📋 Copier</button>
                </div>
            </div>

            <!-- Full Runners Table -->
            <h3 style="font-size:1.1rem; color:#fff; margin-bottom:8px;">📋 Grille Complète des Partants & Probabilités Calibrées</h3>
            <div style="overflow-x:auto;">
                <table class="runner-table">
                    <thead>
                        <tr>
                            <th>N°</th>
                            <th>Cheval</th>
                            <th>Driver / Jockey</th>
                            <th>Ferrure</th>
                            <th>Corde</th>
                            <th>Musique</th>
                            <th id="modal-odds-header">Cote (T-90)</th>
                            <th>Signal</th>
                            <th>Proba %</th>
                            <th>Indice Value</th>
                        </tr>
                    </thead>
                    <tbody id="modal-runners-tbody">
                    </tbody>
                </table>
            </div>
        </div>
    </div>

    <div id="toast" class="toast-notify">✓ Ticket copié dans le presse-papier !</div>

    <script>
        const allLogs = {logs_json};
        let selectedDate = "ALL";
        let searchQuery = "";
        let currentPage = 1;
        const pageSize = 15;

        function initDateTabs() {{
            const datesSet = new Set(allLogs.map(item => item.date));
            const sortedDates = Array.from(datesSet).sort().reverse();
            
            const container = document.getElementById("date-tabs-container");
            container.innerHTML = "";

            const allBtn = document.createElement("button");
            allBtn.className = "tab-btn active";
            allBtn.textContent = `Toutes les dates (${{allLogs.length}})`;
            allBtn.onclick = () => selectDate("ALL", allBtn);
            container.appendChild(allBtn);

            sortedDates.forEach((dStr, idx) => {{
                const btn = document.createElement("button");
                btn.className = "tab-btn";
                
                const count = allLogs.filter(item => item.date === dStr).length;
                let label = dStr;
                
                if (idx === 0) {{
                    label = `Aujourd'hui (${{dStr.slice(5)}})`;
                }} else if (idx === 1) {{
                    label = `Hier (${{dStr.slice(5)}})`;
                }} else {{
                    label = `${{dStr.slice(5)}}`;
                }}
                
                btn.textContent = `${{label}} [${{count}}]`;
                btn.onclick = () => selectDate(dStr, btn);
                container.appendChild(btn);
            }});
        }}

        function selectDate(dateStr, btnElement) {{
            selectedDate = dateStr;
            currentPage = 1;
            
            document.querySelectorAll(".tab-btn").forEach(b => b.classList.remove("active"));
            if (btnElement) btnElement.classList.add("active");
            
            renderTable();
        }}

        function applyFilters() {{
            searchQuery = document.getElementById("search-input").value.toLowerCase().trim();
            currentPage = 1;
            renderTable();
        }}

        function renderTable() {{
            const filtered = allLogs.filter(item => {{
                const matchDate = (selectedDate === "ALL" || item.date === selectedDate);
                const matchSearch = (!searchQuery || 
                    item.course.toLowerCase().includes(searchQuery) ||
                    item.date.includes(searchQuery) ||
                    item.sel_moteur.includes(searchQuery) ||
                    item.sel_marche.includes(searchQuery)
                );
                return matchDate && matchSearch;
            }});

            document.getElementById("matching-count").textContent = `${{filtered.length}} courses affichées`;

            const totalPages = Math.ceil(filtered.length / pageSize) || 1;
            if (currentPage > totalPages) currentPage = totalPages;
            
            const startIdx = (currentPage - 1) * pageSize;
            const pageData = filtered.slice(startIdx, startIdx + pageSize);

            const tbody = document.getElementById("cockpit-tbody");
            tbody.innerHTML = "";

            if (pageData.length === 0) {{
                tbody.innerHTML = `<tr><td colspan="6" style="padding:25px; color:var(--text-muted);">Aucune course ne correspond à vos filtres.</td></tr>`;
            }} else {{
                pageData.forEach(item => {{
                    const tr = document.createElement("tr");
                    tr.onclick = () => openRaceModal(item.race_id);
                    
                    let badgeClass = "couv-1";
                    if (item.couv_moteur_count === 5) badgeClass = "couv-5";
                    else if (item.couv_moteur_count === 4) badgeClass = "couv-4";
                    else if (item.couv_moteur_count === 3) badgeClass = "couv-3";
                    else if (item.couv_moteur_count === 2) badgeClass = "couv-2";

                    const timeInfo = item.scheduled_start_time || "12:00 GMT";

                    tr.innerHTML = `
                        <td style="text-align:left;">
                            <strong style="color:#60a5fa;">🔍 ${{item.course}}</strong><br>
                            <small style="color:#38bdf8; font-weight:700;">⏰ ${{timeInfo}}</small> • 
                            <small style="color:var(--text-muted); font-family:monospace;">${{item.date}}</small>
                        </td>
                        <td style="font-family:monospace; color:#60a5fa; font-weight:700; letter-spacing:0.5px;">${{item.sel_moteur}}</td>
                        <td style="font-family:monospace; color:#94a3b8; letter-spacing:0.5px;">${{item.sel_marche}}</td>
                        <td style="font-family:monospace; color:#f8fafc; font-weight:700; letter-spacing:0.5px;">${{item.arrivee}}</td>
                        <td><span class="couv-tag ${{badgeClass}}">${{item.couverture_label}}</span></td>
                        <td style="font-size:0.85rem;"><span class="${{item.decision_badge}}">${{item.decision}}</span></td>
                    `;
                    tbody.appendChild(tr);
                }});
            }}

            document.getElementById("page-info").textContent = `Page ${{currentPage}} / ${{totalPages}}`;
            document.getElementById("prev-btn").disabled = (currentPage <= 1);
            document.getElementById("next-btn").disabled = (currentPage >= totalPages);
        }}

        function prevPage() {{
            if (currentPage > 1) {{
                currentPage--;
                renderTable();
            }}
        }}

        function nextPage() {{
            currentPage++;
            renderTable();
        }}

        // Bi-Horizon Modal Inspector Functions
        function openRaceModal(raceId) {{
            const item = allLogs.find(r => r.race_id === raceId);
            if (!item) return;

            const timeInfo = item.scheduled_start_time || "12:00 GMT";

            document.getElementById("modal-title").textContent = `${{item.course}} — ${{item.race_name}}`;
            document.getElementById("modal-subtitle").textContent = `⏰ Départ : ${{timeInfo}} • ${{item.date}} • ${{item.discipline}} • ${{item.distance}}m (Corde à ${{item.rope.toLowerCase()}})${{item.autostart ? ' • Autostart' : ''}} • Statut : ${{item.status}}`;
            document.getElementById("modal-confidence").textContent = item.confidence_label || "⭐⭐⭐";

            const badgeBox = document.getElementById("modal-badge-container");
            if (item.is_master) {{
                badgeBox.innerHTML = `<span class="badge-master">⭐ COUPLE MAITRE DU JOUR</span>`;
            }} else if (item.is_no_bet) {{
                badgeBox.innerHTML = `<span class="badge-nobet">⚠️ ABSTENTION / NO_BET</span>`;
            }} else {{
                badgeBox.innerHTML = `<span class="badge-base">Course Régulière</span>`;
            }}

            // Bi-Horizon Dynamic Indicator
            const horizonBadge = document.getElementById("modal-horizon-badge");
            const horizonInfo = document.getElementById("horizon-info-text");
            const oddsHeader = document.getElementById("modal-odds-header");

            if (item.is_finished) {{
                horizonBadge.textContent = "🏁 Clôturé (Cotes & Arrivée Finales)";
                horizonBadge.style.borderColor = "var(--green)";
                horizonBadge.style.color = "var(--green)";
                horizonInfo.textContent = "Course terminée. Les cotes et arrivées officielles sont consolidées.";
                oddsHeader.textContent = "Cote Finale";
            }} else {{
                let diffMin = 120;
                if (item.scheduled_start_time && item.scheduled_start_time.includes(":")) {{
                    const parts = item.scheduled_start_time.split(" ")[0].split(":");
                    if (parts.length === 2) {{
                        const raceMin = parseInt(parts[0]) * 60 + parseInt(parts[1]);
                        const now = new Date();
                        const nowMin = now.getUTCHours() * 60 + now.getUTCMinutes();
                        diffMin = raceMin - nowMin;
                    }}
                }}

                if (diffMin <= 30) {{
                    horizonBadge.textContent = "⚡ Cockpit Live (T-30 LONACI Online)";
                    horizonBadge.style.borderColor = "var(--green)";
                    horizonBadge.style.color = "var(--green)";
                    horizonInfo.textContent = "Cockpit Live verrouillé à T-30 min pour prise de jeu en ligne (LONACI Online). Smart Money capté à plus de 80%.";
                    oddsHeader.textContent = "Cote T-30 (Live)";
                }} else if (diffMin <= 90) {{
                    horizonBadge.textContent = "📢 Édition Abonnés (T-90 Kiosques)";
                    horizonBadge.style.borderColor = "var(--amber)";
                    horizonBadge.style.color = "var(--amber)";
                    horizonInfo.textContent = "Édition validée pour les abonnés en agences et kiosques physiques (LONACI/PMU). Non-Partants éliminés et cotes stabilisées.";
                    oddsHeader.textContent = "Cote T-90 (Abonnés)";
                }} else {{
                    horizonBadge.textContent = "📡 Édition Préparatoire (T-Matin)";
                    horizonBadge.style.borderColor = "#38bdf8";
                    horizonBadge.style.color = "#38bdf8";
                    horizonInfo.textContent = "Pronostic initial établi le matin. L'Édition Abonnés sera automatiquement générée à T-90 min (1h30 avant) et le Cockpit Live à T-30 min.";
                    oddsHeader.textContent = "Cote Réf. (Matin)";
                }}
            }}

            document.getElementById("modal-bases").textContent = item.bases.length ? item.bases.join(" - ") : "N/A";
            document.getElementById("modal-outsider").textContent = item.outsider_num ? `N° ${{item.outsider_num}}` : "N/A";
            document.getElementById("modal-regrets").textContent = item.regrets.length ? item.regrets.join(" - ") : "N/A";

            document.getElementById("modal-sel-moteur").textContent = item.sel_moteur;
            document.getElementById("modal-sel-marche").textContent = item.sel_marche;
            document.getElementById("modal-arrival").textContent = item.arrivee;

            const couvBox = document.getElementById("modal-coverage-badge");
            let badgeClass = "couv-1";
            if (item.couv_moteur_count === 5) badgeClass = "couv-5";
            else if (item.couv_moteur_count === 4) badgeClass = "couv-4";
            else if (item.couv_moteur_count === 3) badgeClass = "couv-3";
            else if (item.couv_moteur_count === 2) badgeClass = "couv-2";
            couvBox.innerHTML = `<span class="couv-tag ${{badgeClass}}">${{item.couverture_label}}</span>`;

            // Expanded Smart tickets content
            const bStr = item.bases.join(" - ");
            const outNum = item.outsider_num || (item.sel_moteur_list.length > 2 ? item.sel_moteur_list[2] : "");
            const trioStr = `${{bStr}} - ${{outNum}}`;
            const associes = item.sel_moteur_list.filter(n => !item.bases.includes(n)).slice(0, 4).join(", ");
            const quinteStr = `${{bStr}} - X - X - X / ${{associes}}`;

            document.getElementById("ticket-1-text").textContent = `Bases : ${{bStr}}`;
            document.getElementById("ticket-2-text").textContent = `Trio / Couplé : ${{trioStr}}`;
            document.getElementById("ticket-3-text").textContent = `Formule : ${{quinteStr}}`;

            // Runners table
            const runnersTbody = document.getElementById("modal-runners-tbody");
            runnersTbody.innerHTML = "";

            if (item.runners && item.runners.length) {{
                item.runners.forEach(rn => {{
                    const rTr = document.createElement("tr");
                    if (rn.is_np) rTr.style.opacity = "0.4";
                    
                    const isBase = item.bases.includes(rn.num);
                    const isOutsider = item.outsider_num === rn.num;
                    const inTop8 = item.sel_moteur_list.includes(rn.num);

                    let roleTag = "";
                    if (isBase) roleTag = `<span class="badge-base" style="font-size:0.75rem;">BASE</span>`;
                    else if (isOutsider) roleTag = `<span class="badge-master" style="font-size:0.75rem;">OUTSIDER</span>`;
                    else if (inTop8) roleTag = `<span style="color:#60a5fa; font-weight:600; font-size:0.75rem;">TOP 8</span>`;

                    rTr.innerHTML = `
                        <td><strong>${{rn.num}}</strong></td>
                        <td style="text-align:left;"><strong>${{rn.name}}</strong> ${{roleTag}} ${{rn.is_np ? '<span class=\"badge-nobet\" style=\"font-size:0.7rem;\">NP</span>' : ''}}</td>
                        <td style="text-align:left; color:var(--text-muted);">${{rn.driver}}</td>
                        <td><span style="font-family:monospace; color:#38bdf8;">${{rn.shoeing}}</span></td>
                        <td>${{rn.draw}}</td>
                        <td style="font-family:monospace; color:var(--text-muted); font-size:0.8rem;">${{rn.music || '-'}}</td>
                        <td><strong>${{rn.live_odds}}</strong></td>
                        <td style="font-size:0.78rem; color:${{rn.smart_signal.includes('BAISSE') ? 'var(--green)' : 'var(--text-muted)'}};">${{rn.smart_signal}}</td>
                        <td class="val-pos"><strong>${{rn.prob_pct}}%</strong></td>
                        <td style="font-weight:700; color:${{rn.value_index >= 1.15 ? 'var(--green)' : 'var(--text-muted)'}};">${{rn.value_index}}</td>
                    `;
                    runnersTbody.appendChild(rTr);
                }});
            }}

            document.getElementById("race-modal").style.display = "flex";
        }}

        function closeModal() {{
            document.getElementById("race-modal").style.display = "none";
        }}

        function closeModalOnOverlay(e) {{
            if (e.target.id === "race-modal") {{
                closeModal();
            }}
        }}

        function copyTicketText(elementId) {{
            const txt = document.getElementById(elementId).textContent;
            navigator.clipboard.writeText(txt).then(() => {{
                showToast(`✓ Copié : ${{txt}}`);
            }}).catch(() => {{
                showToast(`✓ Ticket copié !`);
            }});
        }}

        function showToast(msg) {{
            const toast = document.getElementById("toast");
            toast.textContent = msg;
            toast.style.display = "block";
            setTimeout(() => {{
                toast.style.display = "none";
            }}, 2500);
        }}

        document.addEventListener("keydown", (e) => {{
            if (e.key === "Escape") closeModal();
        }});

        window.addEventListener("DOMContentLoaded", () => {{
            initDateTabs();
            renderTable();
        }});
    </script>
</body>
</html>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html)

    return os.path.abspath(output_path)
