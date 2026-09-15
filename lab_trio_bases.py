"""Labo « base des bases » : quel trio de chevaux fixer en bases d'un champ
réduit Quarté / Quinté pour maximiser les chances que les TROIS arrivent
dans les 4 (Quarté) ou les 5 (Quinté) premiers ?

Étude rétrospective sur les courses terminées à cotes réelles (aucune fuite
du futur : stats humaines apprises uniquement sur les courses antérieures).

Usage : python lab_trio_bases.py /chemin/turf_bench.db
"""
import sys, json, sqlite3
from collections import defaultdict
sys.path.insert(0, '.')
from turf_lab.database import TurfDatabase
from turf_lab.engine import NewValueEngine
from turf_lab.human_stats import HumanStatsBook, _norm, _PLACEHOLDER_NAMES
from turf_lab.features import extract_runner_features

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else 'turf_bench.db'
db = TurfDatabase(DB_PATH)
raw = sqlite3.connect(DB_PATH)
raw.row_factory = sqlite3.Row


class WalkForwardBook(HumanStatsBook):
    def __init__(self, db, cutoff_date):
        self.cutoff = cutoff_date
        super().__init__(db)

    def _build(self):
        rows = raw.execute("""SELECT x.race_id, x.arrival_order_json FROM race_results x
            JOIN races r ON r.race_id = x.race_id WHERE r.date < ?""", (self.cutoff,)).fetchall()
        results = {r['race_id']: r['arrival_order_json'] for r in rows}
        total_starts = total_top3 = 0
        for row in raw.execute("SELECT race_id, num, horse_name, driver_jockey, trainer, is_non_partant FROM runners").fetchall():
            aj = results.get(row['race_id'])
            if not aj or row['is_non_partant']:
                continue
            arrival = json.loads(aj)
            if not arrival:
                continue
            num = row['num']; won = int(num == arrival[0]); top3 = int(num in arrival[:3])
            total_starts += 1; total_top3 += top3
            d_key, t_key, h_key = _norm(row['driver_jockey']), _norm(row['trainer']), _norm(row['horse_name'])
            if d_key not in _PLACEHOLDER_NAMES:
                d = self.drivers.setdefault(d_key, {"n": 0, "wins": 0, "top3": 0}); d["n"] += 1; d["wins"] += won; d["top3"] += top3
                if h_key not in _PLACEHOLDER_NAMES:
                    c = self.couples.setdefault(d_key + "|" + h_key, {"n": 0, "wins": 0, "top3": 0}); c["n"] += 1; c["wins"] += won; c["top3"] += top3
            if t_key not in _PLACEHOLDER_NAMES:
                t = self.trainers.setdefault(t_key, {"n": 0, "wins": 0, "top3": 0}); t["n"] += 1; t["wins"] += won; t["top3"] += top3
        if total_starts:
            self.global_top3_rate = total_top3 / total_starts
        self.races_learned = len(results)


races = []
for r in raw.execute("""SELECT r.race_id, r.date, r.discipline FROM races r
        JOIN race_results x ON x.race_id = r.race_id
        WHERE COALESCE(x.statut, 'DEFINITIVE') = 'DEFINITIVE' ORDER BY r.date, r.race_id""").fetchall():
    rid = r['race_id']
    race = db.get_race(rid); runners = db.get_runners(rid)
    if not race or not runners:
        continue
    arrival = json.loads(raw.execute("SELECT arrival_order_json FROM race_results WHERE race_id=?", (rid,)).fetchone()[0])
    if not arrival or len(arrival) < 4:
        continue
    snaps = {row['num']: row['odds'] for row in raw.execute("SELECT num, odds FROM odds_snapshots WHERE race_id=? AND horizon='T15'", (rid,)).fetchall()}
    for rn in runners:
        if rn['num'] in snaps:
            rn['odds_t15'] = snaps[rn['num']]
    active = [rn for rn in runners if not rn.get('is_non_partant')]
    priced = [rn for rn in active if rn.get('odds_t15') not in (None, 15.0) or rn.get('final_odds') not in (None, 15.0)]
    if len(active) < 8 or len(priced) / len(active) < 0.5:
        continue  # champ réduit Quarté/Quinté : pelotons d'au moins 8, cotes réelles
    races.append({"race": race, "runners": runners, "arrival": arrival, "date": r['date'], "disc": r['discipline'], "field": len(active)})

print(f"Courses éligibles (≥8 partants, cotes réelles, ≥4 classés) : {len(races)}")

books = {}
def book_for(date):
    if date not in books:
        books[date] = WalkForwardBook(db, date)
    return books[date]

eng = NewValueEngine()


def trio_candidates(x):
    """Retourne pour une course les différents trios candidats + contexte."""
    runners = [dict(rn) for rn in x['runners']]
    book_for(x['date']).enrich_runners(runners)
    pred = eng.predict(x['race'], runners)
    probs = {int(k): v for k, v in pred['probabilities'].items()}
    model = {int(k): v for k, v in pred['metadata'].get('model_probs', {}).items()}
    odds = {rn['num']: float(rn.get('odds_t15') or rn.get('final_odds') or rn.get('morning_odds') or 15.0) for rn in runners if not rn.get('is_non_partant')}
    by_prob = [n for n, _ in sorted(probs.items(), key=lambda kv: -kv[1])]
    by_market = [n for n, _ in sorted(odds.items(), key=lambda kv: kv[1])]
    by_model = [n for n, _ in sorted(model.items(), key=lambda kv: -kv[1])]
    # consensus : chevaux dans le top 5 du marché ET le top 5 du modèle pur, classés par proba calibrée
    consensus = [n for n in by_prob if n in by_market[:5] and n in by_model[:5]]
    # Sélecteurs orientés PLACEMENT : la base d'un champ réduit doit finir
    # dans les 5, pas gagner — on pondère la proba calibrée par la régularité
    # (part de top 3 dans la musique) et par le taux top-3 appris du driver.
    active = [rn for rn in runners if not rn.get('is_non_partant')]
    feats = {rn['num']: extract_runner_features(rn, x['race']) for rn in active}
    g = None
    def place_score(n, w_reg, w_hum):
        f = feats.get(n, {}); reg = float(f.get('regularity', 0.2))
        hs = next((rn.get('human_stats') or {} for rn in active if rn['num'] == n), {})
        base = hs.get('global_top3_rate', 0.28) or 0.28
        hum = hs.get('driver_top3_shrunk')
        hum_factor = 1.0 + w_hum * ((float(hum) - base) / base) if hum is not None else 1.0
        return probs.get(n, 0.0) * (1.0 + w_reg * (reg - 0.3)) * hum_factor
    by_place = lambda wr, wh: [n for n in sorted(probs, key=lambda k: -place_score(k, wr, wh))]
    d1 = by_place(1.0, 0.0); d2 = by_place(2.0, 0.0); e1 = by_place(1.0, 1.0)
    trios = {
        "A. Top 3 proba calibrée": by_prob[:3],
        "B. Top 3 marché (favoris)": by_market[:3],
        "C. Consensus marché∩modèle (complété proba)": (consensus + [n for n in by_prob if n not in consensus])[:3],
        "D1. Placement : proba × régularité (léger)": d1[:3],
        "D2. Placement : proba × régularité (fort)": d2[:3],
        "E1. Placement : proba × régularité × driver top3": e1[:3],
        "F. 2 bases proba + 3e = plus régulier des rangs 3-6": by_prob[:2] + [max(by_prob[2:6], key=lambda k: feats.get(k, {}).get('regularity', 0))],
    }
    p_sorted = sorted(probs.values(), reverse=True)
    return trios, pred['confidence_stars'], p_sorted[2] if len(p_sorted) > 2 else 0.0


def score(trio, arr):
    top4, top5, top3 = set(arr[:4]), set(arr[:5]), set(arr[:3])
    s = set(trio)
    return {
        "3 dans les 3 (Bonus 3 / Tiercé désordre)": s <= top3,
        "3 dans les 4 (Quarté désordre atteignable)": s <= top4,
        "3 dans les 5 (Quinté désordre atteignable)": s <= top5,
        "≥2 dans les 4": len(s & top4) >= 2,
        "≥2 dans les 5": len(s & top5) >= 2,
    }


def run(label, cohort, filt=lambda stars, p3, x: True):
    agg = defaultdict(lambda: defaultdict(int)); n = 0
    for x in cohort:
        trios, stars, p3 = trio_candidates(x)
        if not filt(stars, p3, x):
            continue
        n += 1
        for name, trio in trios.items():
            for k, v in score(trio, x['arrival']).items():
                agg[name][k] += v
    print(f"\n--- {label} : {n} courses ---")
    for name, d in agg.items():
        print(f"  {name}")
        print("     " + " | ".join(f"{k.split(' (')[0]} {100*v/n:5.1f}%" for k, v in d.items()))


run("TOUTES LES COURSES", races)
run("DEPUIS RÉGLAGE 01/09", [x for x in races if x['date'] >= '2026-09-01'])
run("Courses 4★ et 5★ uniquement", races, lambda s, p3, x: s >= 4)
run("Courses 5★ uniquement", races, lambda s, p3, x: s >= 5)
run("3e proba ≥ 12 % (trio net)", races, lambda s, p3, x: p3 >= 0.12)
run("Trot uniquement", [x for x in races if 'TROT' in str(x['disc']).upper()])
run("Plat uniquement", [x for x in races if 'PLAT' in str(x['disc']).upper()])
run("Pelotons 8-12 partants", [x for x in races if x['field'] <= 12])
run("Pelotons 13+ partants", [x for x in races if x['field'] >= 13])
