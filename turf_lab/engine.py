"""New Probabilistic & Value Prediction Engine with NO_BET filtering, Master Couplé detection, and Smart Tickets."""

import math
from typing import Optional, Any, Dict, List, Tuple
from turf_lab.features import extract_runner_features


class NewValueEngine:
    """The senior-grade predictive engine based on multi-factor calibration,
    track bias, smart money velocity, NO_BET filtering, Master Couplé alerts, and smart tickets.
    """

    # ── F1 : CALIBRATION MARCHÉ ──────────────────────────────────────
    # Poids des probabilités implicites du marché dans la probabilité
    # finale. Un moteur fiable ne combat pas le marché, il le corrige à
    # la marge : le marché (dominant) fixe l'ossature des probabilités,
    # le modèle (correcteur ~30 %) déplace les curseurs là où ses
    # capteurs réels (déferrage, Smart Money, chrono, forme) détectent
    # un écart. La value se mesure ensuite sur ces VRAIS écarts.
    MARKET_WEIGHT = 0.70
    # En dessous de cette part de partants réellement cotés, la course
    # est considérée SANS cotes PMU (réunions étrangères hors
    # mutualisation : Suède, Argentine, Chili…) : repli automatique sur
    # le modèle pur — on ne calibre jamais sur des cotes fictives.
    MIN_MARKET_COVERAGE = 0.5
    # Valeur par défaut injectée par la synchronisation quand le flux
    # PMU ne fournit aucune cote pour un partant.
    DEFAULT_ODDS = 15.0

    def __init__(self, engine_name: str = "NEW_VALUE_ENGINE", temperature: float = 12.0):
        self.engine_name = engine_name
        self.temperature = temperature

    def calculate_intrinsic_score(self, feats: Dict[str, Any], discipline: str) -> float:
        """Calculate weighted intrinsic capability score for a runner.

        Si un capteur n'a AUCUNE donnée réelle (press_score tant que la
        presse n'est pas branchée, human_score tant que le driver ou
        l'entraîneur n'a pas assez d'historique appris — F2), son poids est
        redistribué proportionnellement sur les capteurs réels au lieu
        d'injecter une constante fictive."""
        is_trot = "TROT" in discipline.upper()

        if is_trot:
            weights = {
                "form_score": 0.18,
                "speed_score": 0.22,
                "shoeing_score": 0.18,
                "track_bias_score": 0.09,
                "smart_money_score": 0.13,
                "press_score": 0.10,
                "human_score": 0.10,
            }
            penalty_dai = feats["dai_rate"] * 25.0
        else:
            weights = {
                "form_score": 0.22,
                "speed_score": 0.24,
                "equip_score": 0.09,
                "track_bias_score": 0.13,
                "smart_money_score": 0.12,
                "press_score": 0.10,
                "human_score": 0.10,
            }
            penalty_dai = 0.0

        # Capteurs muets (aucune donnée réelle) : poids redistribué.
        dropped = 0.0
        for silent_key in ("press_score", "human_score"):
            if feats.get(silent_key, 0.0) <= 0.0 and silent_key in weights:
                dropped += weights.pop(silent_key)
        if dropped > 0.0 and weights:
            scale = 1.0 / (1.0 - dropped)
            weights = {k: w * scale for k, w in weights.items()}

        raw = sum(w * feats.get(k, 50.0) for k, w in weights.items()) - penalty_dai

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

    # Codes PMU (``paris[].codePari`` du flux) requis par chaque produit proposé.
    PRODUCT_BET_CODES = {
        "COUPLE_PLACE": ("COUPLE_PLACE", "E_COUPLE_PLACE"),
        "DEUX_SUR_QUATRE": ("DEUX_SUR_QUATRE", "E_DEUX_SUR_QUATRE"),
        "COUPLE_GAGNANT": ("COUPLE_GAGNANT", "E_COUPLE_GAGNANT"),
        "TRIO": ("TRIO", "E_TRIO"),
        "QUINTE_PLUS": ("QUINTE_PLUS", "E_QUINTE_PLUS"),
    }
    # Mises unitaires de repli (EUR) si le flux ne fournit pas ``miseBase``.
    DEFAULT_UNIT_STAKE = {"COUPLE_PLACE": 1.50, "DEUX_SUR_QUATRE": 3.00, "COUPLE_GAGNANT": 1.50,
                          "TRIO": 1.50, "QUINTE_PLUS": 2.00}

    @classmethod
    def bet_availability(cls, race: Optional[Dict[str, Any]]) -> Optional[Dict[str, float]]:
        """Paris ouverts sur la course d'après le flux PMU : {code: mise_unitaire_eur}.
        None si l'information n'est pas connue (archive, simulation)."""
        if not race:
            return None
        bets = race.get("bets")
        if bets is None:
            return None
        out: Dict[str, float] = {}
        for b in bets or []:
            if isinstance(b, dict):
                code = str(b.get("code") or b.get("codePari") or b.get("typePari") or "").upper()
                stake = b.get("mise_base_eur")
                if stake is None and b.get("miseBase") is not None:
                    stake = float(b["miseBase"]) / 100.0
            else:
                code, stake = str(b).upper(), None
            if code:
                out[code] = float(stake) if stake is not None else out.get(code, 0.0)
        return out

    @classmethod
    def _product_eligibility(cls, product: str, available: Optional[Dict[str, float]]):
        """(eligible, mise_unitaire, motif). eligible = None si non vérifiable."""
        if available is None:
            return None, cls.DEFAULT_UNIT_STAKE[product], "DISPONIBILITE_NON_VERIFIEE"
        for code in cls.PRODUCT_BET_CODES[product]:
            if code in available:
                stake = available[code] or cls.DEFAULT_UNIT_STAKE[product]
                return True, stake, None
        return False, cls.DEFAULT_UNIT_STAKE[product], "PARI_NON_OUVERT"

    def generate_smart_tickets(self, bases: List[int], selection: List[int], outsider_num: Optional[int],
                               is_master_couple: bool, is_no_bet: bool,
                               race: Optional[Dict[str, Any]] = None,
                               active_nums: Optional[List[int]] = None) -> Dict[str, Any]:
        """Tickets prêts à jouer — représentation STRUCTURÉE unique (Lot 1, A03/A05/A06/A07).

        - NO_BET : aucun ticket (``tickets = []``), jamais un ticket « à 0 € ».
        - Chaque ticket porte ses chevaux DISTINCTS et actifs, ses bases/associés,
          le nombre de combinaisons calculé par énumération (``math.comb``), la
          mise unitaire et le coût total, et son éligibilité selon les paris
          réellement ouverts sur la course (``race["bets"]`` issu du flux PMU).
        - Le texte affiché est dérivé de cette structure, jamais reconstruit.
        Les clés historiques (``ticket_securite`` …) sont conservées pour
        compatibilité, dérivées des mêmes tickets."""
        available = self.bet_availability(race)
        active = set(int(n) for n in active_nums) if active_nums is not None else None

        def _ok(n: Optional[int]) -> bool:
            return n is not None and (active is None or int(n) in active)

        bases = [int(b) for b in bases if _ok(b)][:2]
        base_set = set(bases)
        associates = [int(n) for n in selection if _ok(n) and int(n) not in base_set][:4]
        tickets: List[Dict[str, Any]] = []

        def _ticket(product: str, libelle: str, chevaux: List[int], base_list: List[int], assoc: List[int],
                    combinaisons: int, texte: str, extra_motif: Optional[str] = None) -> Dict[str, Any]:
            eligible, stake, motif = self._product_eligibility(product, available)
            if extra_motif:
                eligible, motif = False, extra_motif
            return {
                "produit": product, "libelle": libelle,
                "chevaux": chevaux, "bases": base_list, "associes": assoc,
                "combinaisons": combinaisons, "mise_unitaire_eur": round(stake, 2),
                "cout_total_eur": round(combinaisons * stake, 2) if eligible is not False else None,
                "eligible": eligible, "motif_ineligibilite": motif,
                "texte": texte,
            }

        if is_no_bet:
            return {"contract": 2, "no_bet": True, "tickets": [],
                    "ticket_securite": None, "ticket_trio": None, "quinte_champ_reduit": None}

        if len(bases) == 2:
            if is_master_couple:
                sec_product, sec_label = "COUPLE_GAGNANT", "Couplé Maître du jour (Gagnant & Placé)"
            else:
                sec_product, sec_label = "COUPLE_PLACE", "Couplé Placé (ou 2sur4)"
            tickets.append(_ticket(sec_product, sec_label, bases, bases, [], 1,
                                   f"{bases[0]} - {bases[1]}"))
            # Trio : trois chevaux DISTINCTS. L'outsider n'occupe jamais deux places.
            third = None
            if outsider_num is not None and _ok(outsider_num) and int(outsider_num) not in base_set:
                third = int(outsider_num)
            elif associates:
                third = associates[0]
            if third is not None:
                trio = bases + [third]
                tickets.append(_ticket("TRIO", "Trio (Couplé Gagnant)", trio, bases, [third], 1,
                                       f"{trio[0]} - {trio[1]} - {trio[2]}"))
            else:
                tickets.append(_ticket("TRIO", "Trio (Couplé Gagnant)", [], bases, [], 0, "—",
                                       extra_motif="MOINS_DE_3_CHEVAUX_DISTINCTS"))
            # Quinté+ champ réduit : 2 bases fixes + 3 places parmi les associés.
            places = 5 - len(bases)
            combos = math.comb(len(associates), places) if len(associates) >= places else 0
            extra = None
            if combos == 0:
                extra = "ASSOCIES_INSUFFISANTS"
            elif active is not None and len(active) < 8:
                extra = "MOINS_DE_8_PARTANTS"
            tickets.append(_ticket("QUINTE_PLUS", "Quinté+ champ réduit", bases + associates, bases, associates, combos,
                                   f"{bases[0]} - {bases[1]} - X - X - X / {', '.join(map(str, associates))}",
                                   extra_motif=extra))

        # Compatibilité : anciennes clés dérivées des tickets structurés
        by_product = {t["produit"]: t for t in tickets}
        sec = by_product.get("COUPLE_PLACE") or by_product.get("COUPLE_GAGNANT")
        trio = by_product.get("TRIO")
        quinte = by_product.get("QUINTE_PLUS")
        return {
            "contract": 2,
            "no_bet": False,
            "tickets": tickets,
            "ticket_securite": ({"pari": sec["libelle"], "chevaux": sec["chevaux"], "formule": sec["texte"],
                                 "mise_base_eur": sec["mise_unitaire_eur"]} if sec else None),
            "ticket_trio": ({"pari": trio["libelle"], "chevaux": trio["chevaux"], "formule": trio["texte"],
                             "mise_base_eur": trio["mise_unitaire_eur"]} if trio and trio["chevaux"] else None),
            "quinte_champ_reduit": ({"pari": quinte["libelle"], "bases_fixes": quinte["bases"], "associes": quinte["associes"],
                                     "formule": quinte["texte"], "combinaisons": quinte["combinaisons"],
                                     "budget_conseille_eur": quinte["cout_total_eur"]} if quinte else None),
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
                "smart_tickets": {"contract": 2, "no_bet": True, "tickets": [],
                                  "ticket_securite": None, "ticket_trio": None, "quinte_champ_reduit": None},
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
            r["model_prob"] = round(prob, 4)
            r["estimated_prob"] = round(prob, 4)
            r["value_index"] = round(prob * r["odds"], 2)

        # 2bis. F1 — CALIBRATION MARCHÉ
        # Probabilité finale = MARKET_WEIGHT × prob. implicite du marché
        # (cotes dé-margées) + (1 − MARKET_WEIGHT) × prob. du modèle.
        # Value index = probabilité finale × cote : un indice > 1 signale
        # désormais un écart RÉEL détecté par les capteurs, et non plus un
        # simple désaccord de classement avec le marché.
        priced = [
            r for r in scored_runners
            if r["odds"] and r["odds"] > 1.01 and abs(r["odds"] - self.DEFAULT_ODDS) > 1e-9
        ]
        market_coverage = len(priced) / len(scored_runners)
        market_available = market_coverage >= self.MIN_MARKET_COVERAGE

        if market_available:
            implied = {r["num"]: 1.0 / max(1.05, float(r["odds"])) for r in scored_runners}
            total_implied = sum(implied.values()) or 1.0
            for r in scored_runners:
                p_market = implied[r["num"]] / total_implied
                p_final = self.MARKET_WEIGHT * p_market + (1.0 - self.MARKET_WEIGHT) * r["model_prob"]
                r["estimated_prob"] = round(p_final, 4)
                r["value_index"] = round(p_final * r["odds"], 2)
        # Sinon (course sans cotes PMU) : les probabilités restent celles
        # du modèle pur, déjà en place — aucune calibration fictive.

        # 3. Classement par probabilité calibrée
        # Le top 8 est constitué des 8 chevaux LES PLUS PROBABLES (indice de
        # value en départage). Banc rétrospectif sur 382 courses à cotes
        # réelles : remplacer les anciens « paris de value » des places 6-8
        # par les 6e-8e plus probables fait passer le Tiercé dans les 8 de
        # 55,5 % à 69,9 %, le Quarté de 45,3 % à 58,4 % et le Quinté de
        # 32,7 % à 47,6 % — bases, ROI et outsider strictement inchangés.
        # La value conserve sa place : choix du tocard (étape 5) et indice
        # affiché par partant. La sélection stockée compte 10 chevaux : les
        # 8 joués, puis les 9e et 10e (« regrets ») pour la transparence.
        ranked_by_prob = sorted(
            scored_runners, key=lambda x: (x["estimated_prob"], x["value_index"]), reverse=True
        )
        top_prob = ranked_by_prob[:5]
        final_selected = ranked_by_prob[:10]

        selection_nums = [r["num"] for r in final_selected]

        # 4. Bases
        base_candidates = sorted(top_prob, key=lambda x: (x["estimated_prob"], x["regularity"]), reverse=True)
        bases = [b["num"] for b in base_candidates[:2]]

        # 5. Outsider / Tocard (Odds >= 10.0 with highest value index)
        outsiders = [r for r in scored_runners if r["odds"] >= 10.0]
        if outsiders:
            best_outsider = max(outsiders, key=lambda x: x["value_index"])
            outsider_num = best_outsider["num"]
        else:
            outsider_num = selection_nums[:8][-1] if selection_nums else None

        # 6. Confidence index, NO_BET filter & Master Couplé detection
        p1 = ranked_by_prob[0]["estimated_prob"] if len(ranked_by_prob) > 0 else 0.2
        p2 = ranked_by_prob[1]["estimated_prob"] if len(ranked_by_prob) > 1 else 0.1
        p3 = ranked_by_prob[2]["estimated_prob"] if len(ranked_by_prob) > 2 else 0.08
        reg1 = base_candidates[0]["regularity"] if len(base_candidates) > 0 else 0.2
        reg2 = base_candidates[1]["regularity"] if len(base_candidates) > 1 else 0.2

        stars, confidence_label, is_no_bet = self.calculate_race_confidence(p1, len(valid_runners), p1 - p2)
        is_master_couple, master_couple_label = self.detect_master_couple(p1, p2, p3, reg1, reg2)

        smart_tickets = self.generate_smart_tickets(bases, selection_nums, outsider_num, is_master_couple, is_no_bet,
                                                   race=race, active_nums=[r["num"] for r in valid_runners])

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
                "runners_count": len(valid_runners),
                # F1 — traçabilité de la calibration : appliquée ou non,
                # poids du marché et couverture réelle en cotes, plus les
                # probabilités du modèle pur pour la transparence.
                "market_calibration": {
                    "applied": market_available,
                    "market_weight": self.MARKET_WEIGHT if market_available else 0.0,
                    "coverage_pct": round(market_coverage * 100.0, 1)
                },
                # F2 — part des partants pour lesquels le capteur humain
                # appris a émis un signal (monte avec la taille des archives).
                "human_coverage_pct": round(
                    100.0 * sum(1 for f in features_list if f.get("human_score", 0.0) > 0.0)
                    / max(1, len(features_list)), 1
                ),
                "model_probs": {str(r["num"]): r["model_prob"] for r in scored_runners}
            }
        }
