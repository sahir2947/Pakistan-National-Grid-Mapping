import pytest

from pakgrid import ids
from pakgrid.errors import InvalidIdError


def test_lahore_example():
    cid = ids.cell_id(3, 140, 471)
    assert cid == 3_289_559
    sid = ids.sub_cell_id(cid, 0)
    assert sid == 105_265_888
    assert ids.split_cell_id(cid) == (3, 140, 471)
    assert ids.split_sub_cell_id(sid) == (cid, 0)
    assert ids.parent(sid) == cid
    assert ids.cell_code(cid) == "11-0140-0471"
    assert ids.sub_cell_code(sid) == "11-0140-0471-00"


def test_same_row_and_column_in_each_quadrant_are_distinct():
    got = [ids.cell_id(q, 140, 471) for q in range(4)]
    assert got == [143_831, 1_192_407, 2_240_983, 3_289_559]
    assert [ids.cell_code(c)[:2] for c in got] == ["00", "01", "10", "11"]


@pytest.mark.parametrize("q,row,col,sub", [(0, 0, 0, 0), (3, 1023, 1023, 24), (2, 500, 17, 13)])
def test_round_trip(q, row, col, sub):
    sid = ids.sub_cell_id(ids.cell_id(q, row, col), sub)
    assert sid < 2 ** 27
    cid, s = ids.split_sub_cell_id(sid)
    assert ids.split_cell_id(cid) == (q, row, col) and s == sub
    assert ids.parse_code(ids.sub_cell_code(sid)) == ("sub", sid)
    assert ids.parse_code(ids.cell_code(cid)) == ("cell", cid)


def test_sub_index():
    assert ids.sub_index(0, 1) == 1
    assert ids.sub_index(4, 4) == 24
    assert ids.split_sub_index(13) == (2, 3)


@pytest.mark.parametrize("bad", ["", "11-140-518", "12-0140-0518", "11-0140-0518-1", "11-0140-0518-25",
                                 "11-0140-0518-01-02", "xx-0140-0518", None, 5])
def test_bad_codes(bad):
    with pytest.raises(InvalidIdError):
        ids.parse_code(bad)


def test_bad_parts():
    with pytest.raises(InvalidIdError):
        ids.cell_id(4, 0, 0)
    with pytest.raises(InvalidIdError):
        ids.cell_id(0, 1024, 0)
    with pytest.raises(InvalidIdError):
        ids.sub_cell_id(0, 25)
    with pytest.raises(InvalidIdError):
        ids.split_sub_cell_id((5 << 5) | 30)
    with pytest.raises(InvalidIdError):
        ids.split_cell_id(-1)
    with pytest.raises(InvalidIdError):
        ids.split_cell_id(True)
