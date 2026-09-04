"""Banc d'essai rétrospectif : quelles configurations du moteur auraient
produit les meilleurs scores sur les courses terminées (données réelles :
cotes capturées, arrivées officielles, dividendes officiels).

Usage : python backtest_reglages.py /chemin/turf_bench.db
"""
import sys, json, sqlite3
from collections import defaultdict
sys.path.insert(0, '.')
from turf_lab.database import TurfDatabase
from turf_lab.engine import NewValueEngine
from turf_lab.human_stats import HumanStatsBook, _norm, _PLACEHOLDER_NAMES

DB_PATH = sys.argv[1] if len(sys.argv) > 1 else 'turf_bench.db'
db = TurfDatabase(DB_PATH)
raw = sqlite3.connect(DB_PATH)
raw.row_factory = sqlite3.Row


class WalkForwardBook(HumanStatsBook):
    """Carnet construit UNIQUEMENT sur les courses terminées AVANT une date."""
    def __init__(self, db, cutoff_date):
        self.cutoff = cutoff_date
        super().__init__(db)

    def _build(self):
        rows = raw.execute("""SELECT x.race_id, x.arrival_order_json FROM race_results x
            JOIN races r ON r.race_id = x.race_id WHERE r.date < ?""", (self.cutoff,)).fetchall()
        results = {r['race_id']: r['arrival_order_json'] for r in rows}
        runner_rows = raw.execute("SELECT race_id, num, horse_name, driver_jockey, trainer, is_non_partant FROM runners").fetchall()
        total_starts = total_top3 = 0
        for row in runner_rows:
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


# ── Chargement des courses terminées avec cotes réelles ──────────────────
races = []
for r in raw.execute("""SELECT r.race_id, r.date, r.discipline FROM races r
        JOIN race_results x ON x.race_id = r.race_id ORDER BY r.date, r.race_id""").fetchall():
    rid = r['race_id']
    race = db.get_race(rid); runners = db.get_runners(rid)
    if not race or not runners:
        continue
    arrival = json.loads(raw.execute("SELECT arrival_order_json FROM race_results WHERE race_id=?", (rid,)).fetchone()[0])
    if not arrival:
        continue
    snaps = {row['num']: row['odds'] for row in raw.execute("SELECT num, odds FROM odds_snapshots WHERE race_id=? AND horizon='T15'", (rid,)).fetchall()}
    raps = defaultdict(dict)
    for row in raw.execute("SELECT bet_type, combination, dividend FROM rapports WHERE race_id=?", (rid,)).fetchall():
        raps[row['bet_type']][str(row['combination'])] = float(row['dividend'])
    # cotes de référence = T15 réel si dispo, sinon dernière cote live capturée
    for rn in runners:
        if rn['num'] in snaps:
            rn['odds_t15'] = snaps[rn['num']]
    active = [rn for rn in runners if not rn.get('is_non_partant')]
    priced = [rn for rn in active if rn.get('odds_t15') not in (None, 15.0) or rn.get('final_odds') not in (None, 15.0)]
    if not active or len(priced) / len(active) < 0.5:
        continue  # course sans cotes PMU : hors banc
    races.append({"race": race, "runners": runners, "arrival": arrival, "raps": raps, "date": r['date'], "disc": r['discipline']})

print(f"Courses terminées éligibles (cotes réelles) : {len(races)} | dont depuis le réglage (01/09) : {sum(1 for x in races if x['date'] >= '2026-09-01')}")

books = {}
def book_for(date):
    if date not in books:
        books[date] = WalkForwardBook(db, date)
    return books[date]


def evaluate(cfg, cohort):
    eng = NewValueEngine(temperature=cfg.get('temp', 12.0))
    eng.MARKET_WEIGHT = cfg.get('mw', 0.70)
    m = defaultdict(float); n = 0; sg_stake = sg_ret = sp_stake = sp_ret = 0.0; brier = 0.0; brier_n = 0
    stars_stats = defaultdict(lambda: [0, 0, 0])
    for x in cohort:
        runners = [dict(rn) for rn in x['runners']]
        if cfg.get('human', True):
            book_for(x['date']).enrich_runners(runners)
        pred = eng.predict(x['race'], runners)
        probs = pred['probabilities']
        if cfg.get('sel') == 'prob8':
            sel = [int(k) for k, _ in sorted(probs.items(), key=lambda kv: -kv[1])[:8]]
        else:
            sel = pred['selection'][:8]
        bases = pred['bases']; arr = x['arrival']; w = arr[0]
        n += 1
        m['g8'] += w in sel; m['t8'] += set(arr[:3]) <= set(sel); m['q8'] += set(arr[:4]) <= set(sel); m['q5'] += set(arr[:5]) <= set(sel)
        m['base3'] += any(b in arr[:3] for b in bases); m['top1'] += (sel[0] == w)
        st = stars_stats[pred['confidence_stars']]; st[0] += 1; st[1] += w in sel; st[2] += any(b in arr[:3] for b in bases)
        sg = x['raps'].get('SIMPLE_GAGNANT'); sp = x['raps'].get('SIMPLE_PLACE')
        if sg:
            sg_stake += 1; sg_ret += sg.get(str(sel[0]), 0.0)
        if sp:
            for b in bases:
                sp_stake += 1; sp_ret += sp.get(str(b), 0.0)
        for k, p in probs.items():
            brier += (float(p) - (1.0 if int(k) == w else 0.0)) ** 2; brier_n += 1
    out = {k: 100.0 * v / n for k, v in m.items()}
    out['n'] = n; out['roi_sg'] = 100.0 * (sg_ret - sg_stake) / sg_stake if sg_stake else 0.0
    out['roi_sp'] = 100.0 * (sp_ret - sp_stake) / sp_stake if sp_stake else 0.0
    out['brier'] = brier / brier_n if brier_n else 0.0
    out['stars'] = {k: v for k, v in sorted(stars_stats.items())}
    return out


def show(label, r):
    print(f"  {label:34s} n={r['n']:3d} | G8 {r['g8']:5.1f}% | T8 {r['t8']:5.1f}% | Q8 {r['q8']:5.1f}% | Q5-8 {r['q5']:5.1f}% | base3 {r['base3']:5.1f}% | top1 {r['top1']:5.1f}% | ROI SG {r['roi_sg']:+6.1f}% SP {r['roi_sp']:+6.1f}% | Brier {r['brier']:.4f}")


for cohort_name, cohort in [("TOUTES (25/08 → 04/09)", races), ("DEPUIS RÉGLAGE (01/09 → 04/09)", [x for x in races if x['date'] >= '2026-09-01'])]:
    print(f"\n=== COHORTE {cohort_name} ===")
    print("-- Poids du marché (F1) --")
    for mw in [0.0, 0.5, 0.6, 0.7, 0.8, 0.9, 1.0]:
        show(f"MW={mw:.1f} (prod sel)", evaluate({'mw': mw}, cohort))
    print("-- Construction de la sélection --")
    show("MW=0.7 top8 par proba (sans value)", evaluate({'mw': 0.7, 'sel': 'prob8'}, cohort))
    show("MW=0.8 top8 par proba (sans value)", evaluate({'mw': 0.8, 'sel': 'prob8'}, cohort))
    print("-- Température softmax (netteté du modèle) --")
    for t in [8.0, 16.0, 24.0]:
        show(f"MW=0.7 temp={t:.0f}", evaluate({'mw': 0.7, 'temp': t}, cohort))
    print("-- Capteur humain F2 --")
    show("MW=0.7 SANS facteur humain", evaluate({'mw': 0.7, 'human': False}, cohort))
    prod = evaluate({'mw': 0.7}, cohort)
    print("-- Fiabilité de l'indice de confiance (prod) : étoiles -> (courses, gagnant dans les 8 %, base placée %) --")
    for s, (cnt, g8, b3) in prod['stars'].items():
        print(f"     {s}★ : {cnt:3d} courses | G8 {100*g8/cnt:5.1f}% | base3 {100*b3/cnt:5.1f}%")


def evaluate_gated(cfg, cohort, min_stars):
    """ROI en ne jouant QUE les courses dont l'indice de confiance >= min_stars."""
    eng = NewValueEngine(temperature=12.0); eng.MARKET_WEIGHT = cfg.get('mw', 0.70)
    n = played = 0; sg_stake = sg_ret = sp_stake = sp_ret = 0.0; g8 = 0
    for x in cohort:
        runners = [dict(rn) for rn in x['runners']]; book_for(x['date']).enrich_runners(runners)
        pred = eng.predict(x['race'], runners); n += 1
        if pred['confidence_stars'] < min_stars:
            continue
        played += 1
        probs = pred['probabilities']
        sel = [int(k) for k, _ in sorted(probs.items(), key=lambda kv: -kv[1])[:8]]
        arr = x['arrival']; w = arr[0]; g8 += w in sel
        sg = x['raps'].get('SIMPLE_GAGNANT'); sp = x['raps'].get('SIMPLE_PLACE')
        if sg: sg_stake += 1; sg_ret += sg.get(str(sel[0]), 0.0)
        if sp:
            for b in pred['bases']: sp_stake += 1; sp_ret += sp.get(str(b), 0.0)
    return dict(n=n, played=played, g8=100*g8/played if played else 0, roi_sg=100*(sg_ret-sg_stake)/sg_stake if sg_stake else 0, roi_sp=100*(sp_ret-sp_stake)/sp_stake if sp_stake else 0)

print("\n=== JEU FILTRÉ PAR INDICE DE CONFIANCE (sélection top8 proba, MW=0.7) ===")
for cohort_name, cohort in [("TOUTES", races), ("DEPUIS RÉGLAGE", [x for x in races if x['date'] >= '2026-09-01'])]:
    for ms in [1, 3, 4, 5]:
        r = evaluate_gated({'mw': 0.7}, cohort, ms)
        print(f"  {cohort_name:15s} >= {ms}★ : {r['played']:3d}/{r['n']:3d} courses jouées | G8 {r['g8']:5.1f}% | ROI SG {r['roi_sg']:+6.1f}% | ROI SP (bases) {r['roi_sp']:+6.1f}%")
