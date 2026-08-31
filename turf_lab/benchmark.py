"""Benchmarking and evaluation lab for comparative turf prediction analysis with discipline breakdowns,
dual Top 8 comparisons (Moteur vs Marché), permanent historical race logs, and deep race modal inspector.
"""

import json
import math
from typing import Any, Dict, List, Optional
from turf_lab.database import TurfDatabase


class TurfBenchmarkLab:
    """Evaluation lab calculating quantitative hit rates, financial ROI,
    probabilistic calibration, comparative rankings, and permanent cumulative race logs.
    """

    def __init__(self, db: TurfDatabase):
        self.db = db

    def evaluate_engine(self, engine_name: str, race_ids: Optional[List[str]] = None, exclude_no_bet: bool = False) -> Dict[str, Any]:
        """Compute full performance metrics for a specific prediction engine on a given set of races."""
        if race_ids is None:
            race_ids = self.db.get_finished_races()

        if not race_ids:
            return {"engine_name": engine_name, "total_races": 0, "status": "NO_DATA"}

        total_races = 0
        top1_wins = 0
        base1_placed = 0
        base2_placed = 0
        at_least_one_base_placed = 0
        both_bases_placed = 0
        winner_in_top3 = 0
        winner_in_top5 = 0
        winner_in_top8 = 0
        tierce_in_top8 = 0
        quarte_in_top8 = 0
        quinte_in_top8 = 0
        outsider_placed = 0
        outsider_won = 0

        sg_stake = 0.0
        sg_return = 0.0
        sp_stake = 0.0
        sp_return = 0.0

        sg_bankroll_history = [0.0]
        sp_bankroll_history = [0.0]

        brier_sum = 0.0
        brier_count = 0
        log_loss_sum = 0.0
        log_loss_count = 0

        for r_id in race_ids:
            eval_data = self.db.get_race_evaluation_data(r_id)
            if not eval_data or not eval_data["results"]:
                continue

            arrival = eval_data["results"]["arrival_order"]
            if not arrival:
                continue

            pred = next((p for p in eval_data["predictions"] if p["engine_name"] == engine_name), None)
            if not pred or not pred["selection"]:
                continue

            if exclude_no_bet and pred.get("is_no_bet", False):
                continue

            winner = arrival[0]
            top3 = set(arrival[:3])
            top4 = set(arrival[:4])
            top5 = set(arrival[:5])

            total_races += 1
            selection = pred["selection"]
            bases = pred.get("bases", [])
            outsider = pred.get("outsider_num")
            probs = pred.get("probabilities", {})

            # Hit rates
            top_pick = selection[0]
            if top_pick == winner:
                top1_wins += 1

            if winner in selection[:3]:
                winner_in_top3 += 1
            if winner in selection[:5]:
                winner_in_top5 += 1
            if winner in selection[:8]:
                winner_in_top8 += 1

            if top3.issubset(set(selection[:8])):
                tierce_in_top8 += 1
            if top4.issubset(set(selection[:8])):
                quarte_in_top8 += 1
            if top5.issubset(set(selection[:8])):
                quinte_in_top8 += 1

            b1 = bases[0] if len(bases) > 0 else None
            b2 = bases[1] if len(bases) > 1 else None

            b1_in_top3 = b1 in top3 if b1 else False
            b2_in_top3 = b2 in top3 if b2 else False

            if b1_in_top3:
                base1_placed += 1
            if b2_in_top3:
                base2_placed += 1
            if b1_in_top3 or b2_in_top3:
                at_least_one_base_placed += 1
            if b1_in_top3 and b2_in_top3:
                both_bases_placed += 1

            if outsider:
                if outsider == winner:
                    outsider_won += 1
                if outsider in top3:
                    outsider_placed += 1

            # Financials
            rapports = eval_data.get("rapports", [])
            sg_dividend = 0.0
            sp_dividend_b1 = 0.0
            sp_dividend_b2 = 0.0

            for rap in rapports:
                b_type = rap.get("bet_type")
                comb = str(rap.get("combination"))
                div = float(rap.get("dividend", 0.0))

                if b_type == "SIMPLE_GAGNANT" and comb == str(top_pick):
                    sg_dividend = div
                elif b_type == "SIMPLE_PLACE":
                    if b1 and comb == str(b1):
                        sp_dividend_b1 = div
                    if b2 and comb == str(b2):
                        sp_dividend_b2 = div

            sg_stake += 1.0
            sg_return += sg_dividend
            sg_bankroll_history.append(sg_return - sg_stake)

            if b1:
                sp_stake += 1.0
                sp_return += sp_dividend_b1
            if b2:
                sp_stake += 1.0
                sp_return += sp_dividend_b2
            sp_bankroll_history.append(sp_return - sp_stake)

            # Probabilities calibration
            if probs:
                for r_num_str, p_val in probs.items():
                    r_num = int(r_num_str)
                    actual = 1.0 if r_num == winner else 0.0
                    brier_sum += (float(p_val) - actual) ** 2
                    brier_count += 1

                winner_prob = float(probs.get(str(winner), 0.01))
                winner_prob = max(0.001, min(0.999, winner_prob))
                log_loss_sum += -math.log(winner_prob)
                log_loss_count += 1

        if total_races == 0:
            return {"engine_name": engine_name, "total_races": 0, "status": "NO_DATA"}

        def calc_max_drawdown(history: List[float]) -> float:
            max_dd = 0.0
            peak = history[0]
            for val in history:
                if val > peak:
                    peak = val
                dd = peak - val
                if dd > max_dd:
                    max_dd = dd
            return round(max_dd, 2)

        sg_roi = round(((sg_return - sg_stake) / sg_stake) * 100.0, 2) if sg_stake > 0 else 0.0
        sp_roi = round(((sp_return - sp_stake) / sp_stake) * 100.0, 2) if sp_stake > 0 else 0.0
        brier_score = round(brier_sum / brier_count, 4) if brier_count > 0 else None
        avg_log_loss = round(log_loss_sum / log_loss_count, 4) if log_loss_count > 0 else None

        return {
            "engine_name": engine_name,
            "total_races": total_races,
            "hit_rates": {
                "top1_win_rate_pct": round((top1_wins / total_races) * 100.0, 2),
                "winner_in_top3_pct": round((winner_in_top3 / total_races) * 100.0, 2),
                "winner_in_top8_pct": round((winner_in_top8 / total_races) * 100.0, 2),
                "at_least_one_base_placed_pct": round((at_least_one_base_placed / total_races) * 100.0, 2),
                "both_bases_placed_pct": round((both_bases_placed / total_races) * 100.0, 2),
                "tierce_in_top8_pct": round((tierce_in_top8 / total_races) * 100.0, 2),
                "quarte_in_top8_pct": round((quarte_in_top8 / total_races) * 100.0, 2),
                "quinte_in_top8_pct": round((quinte_in_top8 / total_races) * 100.0, 2),
                "outsider_placed_pct": round((outsider_placed / total_races) * 100.0, 2)
            },
            "financial_performance": {
                "simple_gagnant": {
                    "total_staked_eur": round(sg_stake, 2),
                    "total_returned_eur": round(sg_return, 2),
                    "net_profit_eur": round(sg_return - sg_stake, 2),
                    "roi_pct": sg_roi,
                    "max_drawdown_eur": calc_max_drawdown(sg_bankroll_history)
                },
                "simple_place": {
                    "total_staked_eur": round(sp_stake, 2),
                    "total_returned_eur": round(sp_return, 2),
                    "net_profit_eur": round(sp_return - sp_stake, 2),
                    "roi_pct": sp_roi,
                    "max_drawdown_eur": calc_max_drawdown(sp_bankroll_history)
                }
            },
            "statistical_calibration": {
                "brier_score": brier_score,
                "log_loss": avg_log_loss
            }
        }

    def evaluate_by_discipline(self, engine_name: str = "NEW_VALUE_ENGINE") -> Dict[str, Any]:
        """Compute performance metrics grouped by discipline (TROT, PLAT, OBSTACLE)."""
        finished_races = self.db.get_finished_races()
        discipline_races = {"TROT": [], "PLAT": [], "OBSTACLE": []}

        for r_id in finished_races:
            race = self.db.get_race(r_id)
            if not race:
                continue
            disc = str(race.get("discipline", "TROT")).upper()
            if "TROT" in disc:
                discipline_races["TROT"].append(r_id)
            elif "PLAT" in disc:
                discipline_races["PLAT"].append(r_id)
            elif "OBSTACLE" in disc or "HAIE" in disc or "STEEPLE" in disc:
                discipline_races["OBSTACLE"].append(r_id)
            else:
                discipline_races["TROT"].append(r_id)

        results = {}
        for category, r_ids in discipline_races.items():
            if r_ids:
                results[category] = self.evaluate_engine(engine_name, race_ids=r_ids)
            else:
                results[category] = {"total_races": 0, "status": "NO_DATA"}

        return results

    def get_historical_race_logs(self, limit: Optional[int] = None) -> List[Dict[str, Any]]:
        """Extract all races (both finished and scheduled upcoming) with permanent cumulative history."""
        with self.db.transaction() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT race_id, status FROM races ORDER BY date DESC, meeting_number ASC, race_number ASC")
            all_races_meta = cursor.fetchall()

        if limit is not None:
            all_races_meta = all_races_meta[:limit]
            
        logs = []

        for r_row in all_races_meta:
            r_id = r_row["race_id"]
            race = self.db.get_race(r_id)
            if not race:
                continue

            runners = self.db.get_runners(r_id)
            predictions = self.db.get_predictions(r_id)
            odds_snaps = self.db.get_odds_snapshots(r_id)

            date_str = race.get("date", "2026-08-31")
            m_num = race.get("meeting_number", 1)
            c_num = race.get("race_number", 1)
            hippo = race.get("hippodrome", "PARIS")
            race_name = race.get("name", f"Prix de {hippo}")
            discipline = race.get("discipline", "TROT_ATTELE")
            distance = race.get("distance", 2700)
            rope = race.get("rope", "GAUCHE")
            autostart = bool(race.get("autostart", False))
            scheduled_start_time = race.get("scheduled_start_time", "12:00 GMT")
            course_label = f"{hippo.upper()} - R{m_num}C{c_num}"

            # 1. Top 8 Moteur (NEW_VALUE_ENGINE)
            # Priorité au pronostic le plus proche du départ réellement verrouillé
            # (T15 > T30 > T90 > T_MATIN), sans jamais mélanger les horizons.
            horizon_priority = ["T15", "T30", "T90", "T_MATIN"]

            def pick_prediction(engine: str):
                cands = [p for p in predictions if p["engine_name"] == engine]
                if not cands:
                    return None
                for h in horizon_priority:
                    for p in cands:
                        if p.get("horizon") == h:
                            return p
                return cands[0]

            p_new = pick_prediction("NEW_VALUE_ENGINE")
            sel_moteur = p_new["selection"][:8] if p_new and p_new.get("selection") else []
            sel_moteur_str = "-".join(map(str, sel_moteur)) if sel_moteur else "-"
            bases = p_new.get("bases", []) if p_new else []
            outsider_num = p_new.get("outsider_num") if p_new else None
            is_no_bet = p_new.get("is_no_bet", False) if p_new else False
            is_master = p_new.get("is_master_couple", False) if p_new else False
            confidence_stars = p_new.get("confidence_stars", 3) if p_new else 3
            confidence_label = p_new.get("confidence_label", "⭐⭐⭐ (Course Ouverte)") if p_new else "⭐⭐⭐"
            smart_tickets = p_new.get("smart_tickets", {}) if p_new else {}
            probabilities = p_new.get("probabilities", {}) if p_new else {}
            meta = p_new.get("metadata", {}) if p_new else {}
            value_indices = meta.get("value_indices", {})
            smart_signals = meta.get("smart_signals", {})

            # Regrets (9e et 10e chevaux)
            regrets = p_new["selection"][8:10] if p_new and len(p_new.get("selection", [])) >= 10 else (
                [n for n in range(1, len(runners) + 1) if n not in sel_moteur][:2]
            )

            # 2. Top 8 Marché (MARKET_BASELINE)
            p_market = pick_prediction("MARKET_BASELINE")
            sel_marche = p_market["selection"][:8] if p_market and p_market.get("selection") else []
            sel_marche_str = "-".join(map(str, sel_marche)) if sel_marche else "-"

            # 3. Fav Presse & Fav Marché
            p_press = pick_prediction("PRESS_SYNTHESIS")
            fav_presse = p_press["selection"][0] if p_press and p_press.get("selection") else "-"
            fav_marche = sel_marche[0] if sel_marche else "-"

            # 4. Official arrival (if finished)
            with self.db.transaction() as conn:
                cursor = conn.cursor()
                cursor.execute("SELECT arrival_order_json FROM race_results WHERE race_id = ?", (r_id,))
                res_row = cursor.fetchone()

            is_finished = bool(res_row)
            arrival = json.loads(res_row["arrival_order_json"]) if res_row else []
            top5_arrival = arrival[:5] if arrival else []
            arrival_str = "-".join(map(str, top5_arrival)) if arrival else "En attente"

            # 5. Dual coverage (Moteur vs Marché)
            if is_finished:
                matches_moteur = [n for n in top5_arrival if n in sel_moteur]
                matches_marche = [n for n in top5_arrival if n in sel_marche]
                c_moteur = len(matches_moteur)
                c_marche = len(matches_marche)
                couverture_label = f"Moteur {c_moteur}/5 · Marché {c_marche}/5"
            else:
                c_moteur = 0
                c_marche = 0
                couverture_label = "Course à venir"

            # 6. Bases performance note
            b_notes = []
            for b in bases:
                if is_finished and b in arrival:
                    pos = arrival.index(b) + 1
                    if pos <= 3:
                        b_notes.append(f"{b} ({pos}e 🟢)")
                    else:
                        b_notes.append(f"{b} ({pos}e)")
                else:
                    b_notes.append(str(b))

            if is_no_bet:
                decision_text = "⚠️ NO_BET (Abstention)"
                decision_badge = "badge-nobet"
            elif is_master:
                decision_text = f"⭐ COUPLE MAITRE: {' - '.join(b_notes)}"
                decision_badge = "badge-master"
            elif bases:
                decision_text = f"Bases: {' - '.join(b_notes)}"
                decision_badge = "badge-base"
            else:
                decision_text = "NO_QUALIFIED_BASE"
                decision_badge = "badge-neutral"

            common_count = len(set(sel_moteur).intersection(set(sel_marche)))

            # Detailed runners list for modal view
            detailed_runners = []
            for r in runners:
                r_num = r.get("num")
                r_num_str = str(r_num)
                r_snaps = odds_snaps.get(r_num, {})
                detailed_runners.append({
                    "num": r_num,
                    "name": r.get("horse_name", f"Cheval_{r_num}"),
                    "driver": r.get("driver_jockey", ""),
                    "shoeing": r.get("shoeing", "FERRE"),
                    "draw": r.get("draw", r_num),
                    "music": r.get("music", ""),
                    "morning_odds": r.get("morning_odds", 10.0),
                    "live_odds": r.get("odds_t15", r.get("final_odds", 10.0)),
                    "o_t90": r_snaps.get("T90"),
                    "o_t30": r_snaps.get("T30"),
                    "o_t15": r_snaps.get("T15"),
                    "prob_pct": round(float(probabilities.get(r_num_str, 0.0)) * 100.0, 1),
                    "value_index": value_indices.get(r_num_str, 1.0),
                    "smart_signal": smart_signals.get(r_num_str, "STABLE"),
                    "is_np": bool(r.get("is_non_partant", False))
                })

            detailed_runners.sort(key=lambda x: x["prob_pct"], reverse=True)

            logs.append({
                "race_id": r_id,
                "date": date_str,
                "course": course_label,
                "race_name": race_name,
                "discipline": discipline,
                "distance": distance,
                "rope": rope,
                "autostart": autostart,
                "scheduled_start_time": scheduled_start_time,
                "is_finished": is_finished,
                "status": "TERMINÉE" if is_finished else "PROGRAMMÉE",
                "confidence_stars": confidence_stars,
                "confidence_label": confidence_label,
                "is_no_bet": is_no_bet,
                "is_master": is_master,
                "sel_moteur": sel_moteur_str,
                "sel_moteur_list": sel_moteur,
                "bases": bases,
                "outsider_num": outsider_num,
                "regrets": regrets,
                "smart_tickets": smart_tickets,
                "sel_marche": sel_marche_str,
                "sel_marche_list": sel_marche,
                "common_count": common_count,
                "fav_presse": fav_presse,
                "fav_marche": fav_marche,
                "arrivee": arrival_str,
                "arrival_list": top5_arrival,
                "couv_moteur": f"{c_moteur}/5",
                "couv_moteur_count": c_moteur,
                "couv_marche": f"{c_marche}/5",
                "couv_marche_count": c_marche,
                "couverture_label": couverture_label,
                "decision": decision_text,
                "decision_badge": decision_badge,
                "runners": detailed_runners
            })

        return logs

    def generate_comparative_report(self, engines: Optional[List[str]] = None) -> Dict[str, Any]:
        """Generate full comparative report across engines, discipline breakdowns, and permanent logs."""
        if engines is None:
            engines = ["NEW_VALUE_ENGINE", "ETPE_ENGINE", "PRESS_SYNTHESIS", "MARKET_BASELINE"]

        evaluations = {}
        for eng in engines:
            evaluations[eng] = self.evaluate_engine(eng)

        discipline_breakdown = self.evaluate_by_discipline("NEW_VALUE_ENGINE")
        # Historique permanent : aucune limite — toutes les courses archivées
        # sont conservées et exposées (la pagination/les archives mensuelles
        # gèrent le volume côté site).
        historical_logs = self.get_historical_race_logs(limit=None)

        return {
            "engines_evaluated": engines,
            "total_finished_races": len(self.db.get_finished_races()),
            "evaluations": evaluations,
            "discipline_breakdown": discipline_breakdown,
            "historical_logs": historical_logs
        }
