import pytest

from pakgrid import direction
from pakgrid.navigate import turn_words

TARGET = (31.5204, 74.3587)


def test_rider_two_hundred_metres_away():
    d = direction(31.5215, 74.3570, *TARGET, heading=90)
    assert round(d.distance_m) == 202 and round(d.distance_ft) == 664
    assert round(d.bearing_deg) == 127 and d.compass == "South-East" and d.compass_short == "SE"
    assert round(d.turn_deg) == 37 and d.turn == "bear right"


def test_rider_fifty_metres_away():
    d = direction(31.52085, 74.35855, *TARGET, heading=180)
    assert round(d.distance_m) == 52 and round(d.distance_ft) == 170
    assert round(d.bearing_deg) == 164 and d.compass_short == "SSE"
    assert round(d.turn_deg) == -16 and d.turn == "slightly left"


def test_without_heading():
    d = direction(31.5, 74.3, 31.6, 74.3)
    assert d.turn_deg is None and d.turn is None
    assert d.compass == "North" and d.bearing_deg == pytest.approx(0, abs=1e-9)


def test_same_point():
    d = direction(*TARGET, *TARGET, heading=45)
    assert d.distance_m == 0


@pytest.mark.parametrize("deg,words", [(0, "ahead"), (-9, "ahead"), (20, "slightly right"), (-45, "bear left"),
                                       (90, "turn right"), (-134, "turn left"), (135, "turn around"),
                                       (-180, "turn around")])
def test_turn_words(deg, words):
    assert turn_words(deg) == words


def test_turn_wraps_around_north():
    d = direction(31.5, 74.3, 31.6, 74.3001, heading=350)   # target just east of north
    assert 0 < d.turn_deg < 20 and d.turn in ("ahead", "slightly right")
