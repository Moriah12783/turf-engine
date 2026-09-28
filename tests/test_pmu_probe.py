"""Sonde PMU : forme des réponses sans contenu, choix de la course."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from turf_lab import pmu_probe as probe


def test_forme_sans_valeurs():
    body = {"combinaisons": [{"numeros": [3, 7], "rapport": 12.4}], "type": "COUPLE_GAGNANT"}
    shape = probe.describe(body)
    assert shape == {"combinaisons": {"list": 1, "elem": {"dict": 2}}, "type": "str"}
    assert "12.4" not in str(shape) and "COUPLE_GAGNANT" not in str(shape)


def test_prochaine_course_pas_encore_partie():
    now = 1_000_000_000
    programme = {"programme": {"reunions": [
        {"numOfficiel": 1, "courses": [{"numOrdre": 1, "heureDepart": now - 60_000},          # partie
                                       {"numOrdre": 2, "heureDepart": now + 10 * 60_000}]},  # trop proche
        {"numOfficiel": 4, "courses": [{"numOrdre": 3, "heureDepart": now + 90 * 60_000},
                                       {"numOrdre": 5, "heureDepart": now + 40 * 60_000}]}]}}
    assert probe.pick_race(programme, now) == (now + 40 * 60_000, 4, 5)
    assert probe.pick_race({"programme": {"reunions": []}}, now) is None


def test_candidats_couvrent_les_paris_combines():
    paths = {s for _, s in probe.candidates()}
    assert "/combinaisons?specialisation=INTERNET" in paths and "/rapports/E_TRIO" in paths
    assert len(probe.candidates()) < 120                                     # sonde légère
