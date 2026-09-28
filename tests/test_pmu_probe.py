"""Sonde PMU : forme des réponses sans contenu, choix de la course."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import pmu_probe as probe


def test_forme_sans_valeurs():
    body = {"combinaisons": [{"numeros": [3, 7], "rapport": 12.4}], "type": "COUPLE_GAGNANT"}
    shape = probe.describe(body)
    assert shape == {"combinaisons": {"list": 1, "elem": {"champs": ["numeros", "rapport"]}}, "type": "str"}
    assert "12.4" not in str(shape) and "COUPLE_GAGNANT" not in str(shape)


def test_course_qui_propose_le_plus_de_paris():
    now = 1_000_000_000
    quinte = [{"typePari": t} for t in ("SIMPLE_GAGNANT", "COUPLE_GAGNANT", "TRIO", "MULTI", "QUINTE_PLUS")]
    programme = {"programme": {"reunions": [
        {"numOfficiel": 1, "courses": [{"numOrdre": 1, "heureDepart": now - 60_000, "paris": quinte},   # partie
                                       {"numOrdre": 2, "heureDepart": now + 10 * 60_000}]},             # trop proche
        {"numOfficiel": 4, "courses": [{"numOrdre": 3, "heureDepart": now + 90 * 60_000, "paris": quinte},
                                       {"numOrdre": 5, "heureDepart": now + 40 * 60_000,
                                        "paris": quinte[:2]}]}]}}
    assert probe.pick_race(programme, now) == (now + 90 * 60_000, 4, 3)
    assert probe.offered_bets({"paris": quinte}) == sorted(t["typePari"] for t in quinte)
    assert probe.pick_race({"programme": {"reunions": []}}, now) is None


def test_candidats_couvrent_les_paris_combines():
    paths = {s for _, s in probe.candidates()}
    assert "/combinaisons?specialisation=INTERNET" in paths and "/rapports/E_TRIO" in paths
    assert len(probe.candidates()) < 120                                     # sonde légère


def test_suivi_releve_seulement_les_horizons_a_venir(monkeypatch):
    calls, now = [], [10_000.0]
    monkeypatch.setattr(probe.time, "time", lambda: now[0])
    monkeypatch.setattr(probe.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))
    monkeypatch.setattr(probe, "availability", lambda *a: calls.append(round(now[0])) or {})
    start_ms = (10_000 + 20 * 60) * 1000                                    # départ dans 20 min
    assert probe.watch("28092026", 1, 1, [], start_ms) == 0
    # T-60 et T-30 déjà passés : relevés à T-15, T-5, T-2.
    assert calls == [10_000 + 5 * 60, 10_000 + 15 * 60, 10_000 + 18 * 60]
