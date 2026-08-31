"""New Probabilistic & Value Prediction Engine with NO_BET filtering, Master Couplé detection, and Smart Tickets."""

import math
from typing import Any, Dict, List, Tuple
from turf_lab.features import extract_runner_features


class NewValueEngine:
    """The senior-grade predictive engine based on multi-factor calibration,
    track bias, smart money velocity, NO_BET filtering, Master Couplé alerts, and smart tickets.
    """

    def __init__(self, engine_name: str = "NEW_VALUE_ENGINE", temperature: float = 12.0):
        self.engine_name = engine_name
        self.temperature = temperature

    def calculate_intrinsic_score(self, feats: Dict[str, Any], discipline: str) -> float:
        """Calculate weighted intrinsic capability score for a runner."""
        is_trot = "TROT" in discipline.upper()

        if is_trot:
            w_form = 0.20
            w_speed = 0.24
            w_shoeing = 0.18
            w_bias = 0.10
            w_smart_money = 0.15
            w_press = 0.13
            penalty_dai = feats["dai_rate"] * 25.0

            raw = (
                w_form * feats["form_score"] +
                w_speed * feats["speed_score"] +
                w_shoeing * feats["shoeing_score"] +
                w_bias * feats["track_bias_score"] +
                w_smart_money * feats["smart_money_score"] +
                w_press * feats["press_score"] -
                penalty_dai
            )
        else:
            w_form = 0.24
            w_speed = 0.26
            w_equip = 0.10
            w_bias = 0.15
            w_smart_money = 0.13
            w_press = 0.12

            raw = (
                w_form * feats["form_score"] +
                w_speed * feats["speed_score"] +
                w_equip * feats["equip_score"] +
                w_bias * feats["track_bias_score"] +
                w_smart_money * feats["smart_money_score"] +
                w_press * feats["press_score"]
            )

        return round(max(5.0, raw), 2)

    def calculate_race_confidence(self, top_prob: float, field_size: int, top2_diff: float) -> Tuple[int, str, bool]:
        """Proposition 1 & 4: Race Confidence Index (1 to 5 Stars) and NO_BET filtering."""
        if top_prob >= 0.32 and top2_diff >= 0.12 and field_size <= 14:
            return 5, "⭐⭐⭐⭐⭐ (Course Très Fiable - Bases Ultra-Solides)", False
        elif top_prob >= 0.25 and top2_diff >= 0.08:
            return 4, "⭐⭐⭐⭐ (Course Favorable - Bon niveau de confiance)", False
        elif top_prob >= 0.17:
            return 3, "⭐⭐⭐ (Course Ouverte - Équilibre Favoris / Outsiders)", False
        elif top_prob >= 0.13:
            return 2, "⭐⭐ (Course Spéculative - Privilégier les champs réduits)", False
        else:
            # 1 Star: NO_BET recommended to protect subscriber capital
            return 1, "⭐ (Course Loterie / Gros Handicap - ⚠️ ABSTENTION CONSEILLÉE)", True

    def detect_master_couple(self, p1: float, p2: float, p3: float, reg1: float, reg2: float) -> Tuple[bool, str]:
        """Proposition 2: Master Couplé Detection (Couplé Maître)."""
        combined_prob = p1 + p2
        gap_with_third = p2 - p3

        # If top 2 horses represent over 35% win probability and have a solid gap with 3rd
        if combined_prob >= 0.35 and gap_with_third >= 0.03 and reg1 >= 0.30 and reg2 >= 0.30:
            return True, "⭐ COUPLE MAITRE DÉTECTÉ (Bases dominantes sur le peloton)"
        return False, "Couplé Standard"

    def generate_smart_tickets(self, bases: List[int], selection: List[int], outsider_num: int, is_master_couple: bool, is_no_bet: bool) -> Dict[str, Any]:
        """Proposition 3: Smart Ready-to-Bet Ticket Generator."""
        associates = [n for n in selection if n not in bases][:4]
        
        if is_no_bet:
            ticket_securite = {
                "pari": "⚠️ Course Loterie (NO_BET)",
                "chevaux": [],
                "formule": "Abstention conseillée pour préserver le capital.",
                "mise_base_eur": 0.00
            }
        elif is_master_couple:
            ticket_securite = {
                "pari": "⭐ COUPLÉ MAÎTRE DU JOUR (Gagnant & Placé)",
                "chevaux": bases,
                "formule": f"Jeu Prioritaire sur le duo ({bases[0]} - {bases[1]})",
                "mise_base_eur": 6.00
            }
        else:
            ticket_securite = {
                "pari": "Couplé Placé ou 2sur4",
                "chevaux": bases,
                "formule": f"Jeu sur les 2 bases ({bases[0]} - {bases[1]})",
                "mise_base_eur": 3.00
            }

        ticket_trio = {
            "pari": "Couplé Gagnant / Trio",
            "chevaux": bases + ([outsider_num] if outsider_num not in bases else associates[:1]),
            "formule": f"Combinaison {bases[0]} - {bases[1]} - {outsider_num}",
            "mise_base_eur": 3.00
        }

        quinte_champ_reduit = {
            "pari": "Quinté+ Champ Réduit",
            "bases_fixes": bases,
            "associes": associates,
            "formule": f"{bases[0]} - {bases[1]} - X - X - X / {', '.join(map(str, associates))}",
            "combinaisons": len(associates) * (len(associates) - 1) // 2,
            "budget_conseille_eur": 12.00
        }

        return {
            "ticket_securite": ticket_securite,
            "ticket_trio": ticket_trio,
            "quinte_champ_reduit": quinte_champ_reduit
        }

    def predict(self, race: Dict[str, Any], runners: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Generate full probabilistic prediction, NO_BET filter, Master Couplé alert, and smart tickets."""
        valid_runners = [r for r in runners if not r.get("is_non_partant", False)]

        if not valid_runners:
            return {
                "engine_name": self.engine_name,
                "selection": [],
                "bases": [],
                "outsider_num": None,
                "confidence_stars": 1,
                "confidence_label": "N/A",
                "is_no_bet": True,
                "is_master_couple": False,
                "smart_tickets": {},
                "probabilities": {},
                "metadata": {}
            }

        discipline = race.get("discipline", "TROT_ATTELE")
        features_list = [extract_runner_features(r, race) for r in valid_runners]

        # 1. Compute raw scores
        scored_runners = []
        for f in features_list:
            score = self.calculate_intrinsic_score(f, discipline)
            scored_runners.append({
                "num": f["num"],
                "horse_name": f["horse_name"],
                "score": score,
                "odds": f["odds"],
                "smart_signal": f["smart_signal"],
                "regularity": f["regularity"],
                "features": f
            })

        # 2. Compute Softmax Probabilities
        max_score = max(r["score"] for r in scored_runners)
        exp_scores = [math.exp((r["score"] - max_score) / self.temperature) for r in scored_runners]
        sum_exp = sum(exp_scores) or 1.0

        for r, exp_s in zip(scored_runners, exp_scores):
            prob = exp_s / sum_exp
            r["estimated_prob"] = round(prob, 4)
            r["value_index"] = round(prob * r["odds"], 2)

        # 3. Probabilistic & Value Ranking
        ranked_by_prob = sorted(scored_runners, key=lambda x: x["estimated_prob"], reverse=True)
        top_prob = ranked_by_prob[:5]
        
        remaining = [r for r in ranked_by_prob[5:]]
        value_picks = sorted(remaining, key=lambda x: (x["value_index"], x["estimated_prob"]), reverse=True)
        
        final_selected = top_prob + value_picks[:3]
        if len(final_selected) < 8 and len(remaining) > 3:
            final_selected += [r for r in remaining if r not in final_selected][:8 - len(final_selected)]

        selection_nums = [r["num"] for r in final_selected[:8]]

        # 4. Bases
        base_candidates = sorted(top_prob, key=lambda x: (x["estimated_prob"], x["regularity"]), reverse=True)
        bases = [b["num"] for b in base_candidates[:2]]

        # 5. Outsider / Tocard (Odds >= 10.0 with highest value index)
        outsiders = [r for r in scored_runners if r["odds"] >= 10.0]
        if outsiders:
            best_outsider = max(outsiders, key=lambda x: x["value_index"])
            outsider_num = best_outsider["num"]
        else:
            outsider_num = selection_nums[-1] if selection_nums else None

        # 6. Confidence index, NO_BET filter & Master Couplé detection
        p1 = ranked_by_prob[0]["estimated_prob"] if len(ranked_by_prob) > 0 else 0.2
        p2 = ranked_by_prob[1]["estimated_prob"] if len(ranked_by_prob) > 1 else 0.1
        p3 = ranked_by_prob[2]["estimated_prob"] if len(ranked_by_prob) > 2 else 0.08
        reg1 = base_candidates[0]["regularity"] if len(base_candidates) > 0 else 0.2
        reg2 = base_candidates[1]["regularity"] if len(base_candidates) > 1 else 0.2

        stars, confidence_label, is_no_bet = self.calculate_race_confidence(p1, len(valid_runners), p1 - p2)
        is_master_couple, master_couple_label = self.detect_master_couple(p1, p2, p3, reg1, reg2)

        smart_tickets = self.generate_smart_tickets(bases, selection_nums, outsider_num, is_master_couple, is_no_bet)

        probabilities_dict = {str(r["num"]): r["estimated_prob"] for r in scored_runners}
        value_dict = {str(r["num"]): r["value_index"] for r in scored_runners}
        signal_dict = {str(r["num"]): r["smart_signal"] for r in scored_runners}

        return {
            "engine_name": self.engine_name,
            "selection": selection_nums,
            "bases": bases,
            "outsider_num": outsider_num,
            "confidence_stars": stars,
            "confidence_label": confidence_label,
            "is_no_bet": is_no_bet,
            "is_master_couple": is_master_couple,
            "master_couple_label": master_couple_label,
            "smart_tickets": smart_tickets,
            "probabilities": probabilities_dict,
            "metadata": {
                "value_indices": value_dict,
                "smart_signals": signal_dict,
                "top_score": max_score,
                "discipline": discipline,
                "runners_count": len(valid_runners)
            }
        }
