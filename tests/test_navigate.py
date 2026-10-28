import pytest

from pakgrid import NavigationSession, direction, format_distance, geodesy
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


# -- format_distance ---------------------------------------------------------

@pytest.mark.parametrize("metres, units, text", [
    (0, "m", "0 m"), (12.4, "m", "10 m"), (12.6, "m", "15 m"), (97.6, "m", "100 m"), (100, "m", "100 m"),
    (434, "m", "430 m"), (999, "m", "1000 m"), (1000, "m", "1.0 km"), (12345, "m", "12.3 km"),
    (10, "ft", "35 ft"), (100, "ft", "330 ft"), (1609.344, "ft", "1.0 mi"), (5000, "ft", "3.1 mi"),
])
def test_format_distance(metres, units, text):
    assert format_distance(metres, units) == text


def test_format_distance_rejects_unknown_units():
    with pytest.raises(ValueError):
        format_distance(10, "yd")


# -- NavigationSession ---------------------------------------------------------

def _offset(east, north, origin=TARGET):
    return geodesy.apply_offset(*origin, east, north)


def test_waiting_until_first_position():
    s = NavigationSession(*TARGET).state
    assert s.status == "waiting" and s.lat is None and s.distance_m is None and s.arrow_mode is None


def test_fix_gives_distance_and_north_up_arrow():
    nav = NavigationSession(*TARGET)
    s = nav.add_fix(*_offset(-300, -400), accuracy=5, time=0)
    assert s.status == "navigating" and round(s.distance_m) == 500
    assert s.compass_short == "NE" and s.heading_deg is None
    assert s.arrow_mode == "north-up" and s.arrow_deg == s.bearing_deg and s.accuracy_m == 5


def test_status_thresholds():
    nav = NavigationSession(*TARGET)
    assert nav.set_position(*_offset(0, -60)).status == "navigating"
    assert nav.set_position(*_offset(0, -40)).status == "almost"
    assert nav.set_position(*_offset(0, -10)).status == "arrived"


def test_jitter_is_averaged_but_real_movement_is_not():
    nav = NavigationSession(*TARGET)
    nav.add_fix(*_offset(0, -500), accuracy=10, time=0)
    s = nav.add_fix(*_offset(4, -500), accuracy=10, time=1)    # within accuracy: averaged
    assert geodesy.local_offset(*TARGET, s.lat, s.lon)[0] == pytest.approx(2, abs=0.01)
    s = nav.add_fix(*_offset(0, -400), accuracy=10, time=2)    # 100 m on: start afresh
    assert geodesy.local_offset(*TARGET, s.lat, s.lon) == pytest.approx((0, -400), abs=0.01)


def test_more_accurate_fixes_count_more():
    nav = NavigationSession(*TARGET)
    nav.add_fix(*_offset(0, -500), accuracy=4, time=0)
    s = nav.add_fix(*_offset(3, -500), accuracy=8, time=1)     # weights 1/16 and 1/64
    assert geodesy.local_offset(*TARGET, s.lat, s.lon)[0] == pytest.approx(0.6, abs=0.01)


def test_old_fixes_drop_out():
    nav = NavigationSession(*TARGET, smoothing_window_s=10)
    nav.add_fix(*_offset(0, -500), accuracy=10, time=0)
    s = nav.add_fix(*_offset(4, -500), accuracy=10, time=11)
    assert geodesy.local_offset(*TARGET, s.lat, s.lon)[0] == pytest.approx(4, abs=0.01)


def test_heading_prefers_gps_course_when_moving():
    nav = NavigationSession(*TARGET)
    nav.add_compass(10, time=0)
    s = nav.add_fix(*_offset(0, -500), speed=6, heading=90, time=0.5)
    assert (s.heading_deg, s.heading_source) == (90, "gps")
    assert s.arrow_mode == "relative" and s.turn_deg == pytest.approx(-90) and s.turn == "turn left"
    s = nav.add_compass(20, time=1)               # ignored while the GPS course is in charge
    assert s.heading_source == "gps"


def test_heading_uses_fresh_compass_when_slow():
    nav = NavigationSession(*TARGET)
    nav.add_compass(370, time=0)
    s = nav.add_fix(*_offset(0, -500), speed=0.5, heading=90, time=1)
    assert (s.heading_deg, s.heading_source) == (10, "compass")
    assert nav.add_compass(5, time=2).heading_deg == 5
    s = nav.add_fix(*_offset(0, -500), speed=0, time=5.5)        # compass now 3.5 s old
    assert s.heading_deg is None and s.arrow_mode == "north-up"


def test_set_position_heading_from_movement():
    nav = NavigationSession(*TARGET)
    s = nav.set_position(*_offset(0, -500))
    assert s.heading_deg is None
    s = nav.set_position(*_offset(1, -500))       # under 2 m: no heading yet
    assert s.heading_deg is None
    s = nav.set_position(*_offset(0, -490))
    assert s.heading_source == "movement" and s.heading_deg == pytest.approx(0, abs=0.01) and s.turn == "ahead"
    s = nav.set_position(*_offset(0.5, -490))     # tiny move keeps the last heading
    assert s.heading_source == "movement" and s.heading_deg == pytest.approx(0, abs=0.01)
    s = nav.set_position(*_offset(0, -480), heading=200)
    assert (s.heading_deg, s.heading_source) == (200, "manual")


def test_compass_ignored_for_set_position_and_reset_forgets():
    nav = NavigationSession(*TARGET)
    nav.set_position(*_offset(0, -500), heading=45)
    assert nav.add_compass(300, time=0).heading_source == "manual"
    assert nav.reset().status == "waiting"


def test_set_target_keeps_rider():
    nav = NavigationSession(*TARGET)
    nav.set_position(*_offset(0, -500))
    s = nav.set_target(*_offset(0, -1000))
    assert round(s.distance_m) == 500 and s.compass == "South"
