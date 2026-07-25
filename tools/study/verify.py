"""EXPERIMENTAL (musical-structure study): Gate F verifier prototype.

Gate F: per frame, end-of-frame 25-register state equal AND per-voice dedup'd
value-changing ctrl/AD/SR write ("edge") sequences equal. L0 = event-list
tier: state deltas + edges; its render must pass Gate F full-length.
"""

from framelog import canonicalize

CTRL_OFFS = (4, 5, 6)


def frame_reference(slices, init_state=None):
    """Raw frame slices -> per-frame (state25, edges) Gate-F reference."""
    state = [0] * 25 if init_state is None else list(init_state)
    ref = []
    for raw in slices:
        canon, _ = canonicalize(raw)
        edges = ([], [], [])
        for r, v in canon:
            if r < 21 and (r % 7) in CTRL_OFFS:
                if state[r] != v:
                    edges[r // 7].append((r, v))
            state[r] = v
        ref.append((tuple(state), tuple(tuple(e) for e in edges)))
    return ref


def event_list(ref, init_state=None):
    """L0 tier: per frame, {reg: value} state delta + edge sequences."""
    prev = tuple([0] * 25 if init_state is None else init_state)
    out = []
    for state, edges in ref:
        delta = {r: state[r] for r in range(25) if state[r] != prev[r]}
        out.append((delta, edges))
        prev = state
    return out


def render_events(events, init_state=None):
    """Reference frame-domain player for the L0 tier -> Gate-F view."""
    state = [0] * 25 if init_state is None else list(init_state)
    ref = []
    for delta, edges in events:
        for r, v in delta.items():
            state[r] = v
        ref.append((tuple(state), edges))
    return ref


def gate_f(ref_a, ref_b):
    """(equal, first mismatching frame or -1)."""
    if len(ref_a) != len(ref_b):
        return False, min(len(ref_a), len(ref_b))
    for f, (a, b) in enumerate(zip(ref_a, ref_b)):
        if a != b:
            return False, f
    return True, -1
