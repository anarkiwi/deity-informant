"""Code cells as scalars and field arrays: deity_informant.cgen.state.classify."""

import numpy as np

from deity_informant.cgen.state import FAMILY, INDEXED, State, classify


def mask(*runs):
    """A boolean array of the bytes of ``runs`` (each an address or an iterable of them)."""
    out = np.zeros(0x10000, dtype=bool)
    for r in runs:
        out[list(r) if not isinstance(r, int) else r] = True
    return out


def why(**kinds):
    """A ``why`` array: per keyword ``code=addresses``."""
    out = np.zeros(0x10000, dtype=np.int8)
    for k, addrs in kinds.items():
        out[list(addrs)] = int(k[1:])
    return out


COPIES = [0x1000, 0x1031, 0x1062]


def test_constant_accesses_are_scalars():
    """A cell only constant addresses reach is a scalar; one another access touches is not,
    nor a byte that is no cell."""
    st = classify(mask(0x1005, 0x1006), [[0x1005], [0x1006], [0x1007]], why(w1=[0x1006]))
    assert st.scalars == [0x1005] and not st.structs and st.at == {0x1005: None}
    assert st.counts(mask(0x1005, 0x1006)) == {
        "scalars": 1,
        "fields": 0,
        "unread": 0,
        "bytes": {"indexed": 1},
    }


def test_copies_alike_are_one_struct():
    """Families alike up to a translation within one copy are fields of one struct; a
    constant access to one of their bytes is that field's element."""
    a = [c + 0x0A for c in COPIES]
    b = [c + 0x0C for c in COPIES]
    st = classify(mask(a, b), [a, b[::-1], [b[1]]], why())
    assert not st.scalars and len(st.structs) == 1
    bases, fields = st.structs[0]
    assert bases.tolist() == [0x100A, 0x103B, 0x106C] and fields == [0, 2]
    assert st.at[b[1]] == (0, 1, 2) and st.field_of(b[::-1])[2].tolist() == [2, 1, 0]
    assert st.field_of([a[0], b[1]]) is None
    assert st.mask()[a + b].all() and st.mask().sum() == 6


def test_subset_family_joins_its_field():
    """A family reaching some of a field's copies is that field; one meeting a field's bytes
    and others is not, and neither is the field it meets."""
    six = [0x2000 + 0x31 * k for k in range(6)]
    st = classify(mask(six), [six, six[:3], six[3:]], why())
    assert st.structs[0][0].tolist() == six and st.field_of(six[3:])[2].tolist() == [3, 4, 5]
    odd = [six[0], six[1], 0x2010]
    st = classify(mask(six, [0x2010]), [six, odd], why())
    assert not st.structs and (st.why[six] == FAMILY).all()


def test_families_with_touched_or_data_bytes_stay_bytes():
    """A family one of whose bytes another access touches, or that reaches a byte that is no
    cell, stays bytes."""
    a = [c + 1 for c in COPIES]
    st = classify(mask(a), [a], why(w1=[a[2]]))
    assert not st.structs and st.why[a].tolist() == [FAMILY, FAMILY, INDEXED]
    st = classify(mask(a[:2]), [a], why())
    assert not st.structs and not st.scalars


def test_stepped_families_need_consecutive_elements():
    """A family a loop steps through must reach consecutive copies (its element number steps
    by one), else it stays bytes."""
    six = [0x2000 + 0x31 * k for k in range(6)]
    st = classify(mask(six), [six, six[1:4]], why(), stepped=[six[1:4]])
    assert st.field_of(six[1:4])[2].tolist() == [1, 2, 3]
    st = classify(mask(six), [six, six[::2]], why(), stepped=[six[::2]])
    assert not st.structs


def test_state_counts_unread():
    """Cells promoted and never read count as unread."""
    st = State([0x10], [(np.array([0x20, 0x30]), [0])], unread=[0x11])
    assert st.counts(mask(0x10, 0x11, 0x20, 0x30, 0x40)) == {
        "scalars": 1,
        "fields": 2,
        "unread": 1,
        "bytes": {"unaccessed": 1},
    }
