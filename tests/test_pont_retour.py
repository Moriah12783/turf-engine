"""Pont RADAR_V4, sens retour : résumé du banc déposé dans Radar (fn_pont_rapport).
Aucun réseau : le client est simulé ; le résumé est construit sur un rapport
synthétique de la forme exacte de benchmark_report.json."""
import contextlib
import io
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab.radar_bridge import (  # noqa: E402
    SUMMARY_HORIZONS, SUMMARY_SCHEMA, RadarBridgeClient, build_report_summary, push_report_summary,
)


def _metriques(brier, top1, top3, top8, roi, n):
    return {
        "engine_name": "X", "total_races": n,
        "hit_rates": {"top1_win_rate_pct": top1, "winner_in_top3_pct": top3, "winner_in_top8_pct": top8},
        "financial_performance": {"simple_gagnant": {"roi_pct": roi}},
        "statistical_calibration": {"brier_score": brier, "log_loss": 2.0},
    }


def _report():
    horizons = {}
    for i, h in enumerate(SUMMARY_HORIZONS):
        horizons[h] = {"courses": 208, "metriques": {
            "RADAR_V4": _metriques(0.0684 + i * 0.0001, 30.77, 59.62, 90.87, 12.0, 208),
            "MARKET_BASELINE": _metriques(0.0698 - i * 0.0003, 25.48, 54.81, 86.54, 4.0, 208),
            "NEW_VALUE_ENGINE": _metriques(0.0695, 27.4, 53.37, 88.94, -3.0, 208),
        }}
    return {
        "engines_evaluated": ["NEW_VALUE_ENGINE", "ETPE_ENGINE", "MARKET_BASELINE", "RADAR_V4"],
        "total_finished_races": 870,
        "evaluations": {"NEW_VALUE_ENGINE": {"total_races": 845}, "ETPE_ENGINE": {"total_races": 845},
                        "MARKET_BASELINE": {"total_races": 845}, "RADAR_V4": {"total_races": 208}},
        "courses_communes": {"engines": ["NEW_VALUE_ENGINE", "MARKET_BASELINE", "RADAR_V4"], "horizons": horizons},
        "horizon_bench_start_date": "2026-09-01",
        "historical_logs": [{"race_id": "R1C1_15092026_COMPIEGNE", "runners": [{"num": 1}] * 14}] * 3,
    }


class FakePushClient(RadarBridgeClient):
    def __init__(self, enabled=True, result=None):
        super().__init__({"url": "https://x", "key": "k", "token": "t"} if enabled else None)
        self.sent = []
        self.result = result if result is not None else {"statut": "ACCEPTE", "communes_tous": 208}

    def _rpc(self, function, params):
        self.sent.append((function, params))
        return self.result


def test_resume_compact_et_complet():
    s = build_report_summary(_report(), commit="abc123", run_id="42", generated_at_utc="2026-09-15T10:16:37Z")
    assert s["schema"] == SUMMARY_SCHEMA and s["commit"] == "abc123" and s["run_id"] == "42"
    assert s["total_finished_races"] == 870
    assert s["moteur_nve"]["market_weight"] > 0 and s["seuils_cotes"] == {"verrou": 0.5, "diffusion": 0.9}
    assert s["total_races_par_moteur"]["RADAR_V4"] == 208 and s["total_races_par_moteur"]["MARKET_BASELINE"] == 845
    assert s["premier_log_race_id"] == "R1C1_15092026_COMPIEGNE" and s["nb_logs"] == 3
    assert list(s["horizons"].keys()) == SUMMARY_HORIZONS
    t15 = s["horizons"]["T15"]
    assert t15["courses"] == 208
    assert t15["moteurs"]["RADAR_V4"]["brier"] == 0.0687
    assert t15["moteurs"]["MARKET_BASELINE"]["top1_pct"] == 25.48
    assert t15["moteurs"]["NEW_VALUE_ENGINE"]["roi_gagnant_pct"] == -3.0
    # Compact : aucune donnée par course, bien sous la limite de 64 Ko de la RPC
    assert "runners" not in json.dumps(s) and len(json.dumps(s)) < 8000
    print("  [OK] test_resume_compact_et_complet")


def test_resume_tolerant_aux_champs_absents():
    s = build_report_summary({"evaluations": {}, "historical_logs": []})
    assert s["horizons"]["TOUS"] == {"courses": None, "moteurs": {}}
    assert s["premier_log_race_id"] is None and s["nb_logs"] == 0
    assert s["genere_le_utc"].endswith("Z")
    print("  [OK] test_resume_tolerant_aux_champs_absents")


def test_envoi_via_rpc_meme_jeton():
    cli = FakePushClient()
    res = push_report_summary(_report(), commit="abc123", run_id="42", client=cli)
    assert res == {"statut": "ACCEPTE", "communes_tous": 208}
    assert len(cli.sent) == 1
    function, params = cli.sent[0]
    assert function == "fn_pont_rapport" and params["p_token"] == "t"
    assert params["p_rapport"]["commit"] == "abc123" and params["p_rapport"]["horizons"]["TOUS"]["courses"] == 208
    print("  [OK] test_envoi_via_rpc_meme_jeton")


def _capture(fn):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        res = fn()
    return res, buf.getvalue()


def test_pont_desactive_ne_fait_rien():
    cli = FakePushClient(enabled=False)
    res, out = _capture(lambda: push_report_summary(_report(), client=cli))
    assert res is None and cli.sent == []
    assert "RADAR_BRIDGE_REPORT_SKIPPED" in out
    print("  [OK] test_pont_desactive_ne_fait_rien")


def test_refus_radar_journalise_sans_exception():
    cli = FakePushClient(result={"statut": "REFUSE_DECROISSANCE", "detail": "communes TOUS 100 < dernier accepté 208"})
    res, out = _capture(lambda: push_report_summary(_report(), commit="abc123", client=cli))
    assert res["statut"] == "REFUSE_DECROISSANCE"
    assert "RADAR_BRIDGE_REPORT_PUSHED" in out
    print("  [OK] test_refus_radar_journalise_sans_exception")


class BrokenClient(RadarBridgeClient):
    def __init__(self):
        super().__init__({"url": "https://x", "key": "k", "token": "t"})

    def _rpc(self, function, params):
        raise OSError("reseau indisponible")


def test_panne_reseau_journalisee_sans_exception():
    res, out = _capture(lambda: push_report_summary(_report(), commit="abc123", client=BrokenClient()))
    assert res is None
    assert "RADAR_BRIDGE_REPORT_ERROR" in out
    print("  [OK] test_panne_reseau_journalisee_sans_exception")


def main():
    test_resume_compact_et_complet()
    test_resume_tolerant_aux_champs_absents()
    test_envoi_via_rpc_meme_jeton()
    test_pont_desactive_ne_fait_rien()
    test_refus_radar_journalise_sans_exception()
    test_panne_reseau_journalisee_sans_exception()
    print("\n=== 6 TESTS PONT RETOUR (RÉSUMÉ DU BANC → RADAR) PASSENT ===")


if __name__ == "__main__":
    main()
