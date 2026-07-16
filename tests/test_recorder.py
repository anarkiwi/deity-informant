"""Acceptance tests for the symbolic window recorder (SYMBOLIC-RECORDER-SPEC).

Each fold channel gets a synthetic program; tests assert the record-time
assertion is 0-bad, replay is byte-exact, and artifacts expose SMC dependence.
"""

import deity_informant.expr as E
from deity_informant import PcodeVM, RecVM, lift, record, run_sub

from examples.hello_world import EXPECTED, ORG, PROGRAM


class _Log(PcodeVM):
    """PcodeVM that records ordered writes to ``outs`` (independent oracle)."""

    def __init__(self, mem, outs):
        super().__init__(mem)
        self.outs = set(outs)
        self.log = []

    def _wr(self, addr, val, sz):
        for i in range(sz):
            a = (addr + i) & 0xFFFF
            if a in self.outs:
                self.log.append((a, (val >> (8 * i)) & 0xFF))
        super()._wr(addr, val, sz)


def _mem(prog, org, data=None):
    m = bytearray(0x10000)
    m[org : org + len(prog)] = prog
    for a, b in (data or {}).items():
        m[a] = b
    return m


def _rec(prog, org, outputs, n=1, data=None, assertion=True):
    return record(_mem(prog, org, data), run_sub, org, outputs, n, assertion=assertion)


def _oracle(prog, org, outputs, n=1, data=None):
    vm = _Log(_mem(prog, org, data), outputs)
    cache = {}
    logs = []
    for _ in range(n):
        vm.log = []
        run_sub(vm, org, cache, lift)
        logs.append(list(vm.log))
    return logs


def _nodes(n):
    if n[0] == "op":
        return 1 + sum(_nodes(c) for c in n[2])
    if n[0] in ("mem", "cur"):
        return 1 + _nodes(n[1])
    return 1


def _refs(n, addr):
    if n[0] in ("mem", "cur"):
        a = n[1]
        if E.is_const(a) and a[1] == addr:
            return True
        return _refs(n[1], addr)
    if n[0] == "op":
        return any(_refs(c, addr) for c in n[2])
    return False


def _has_cur(n):
    if n[0] == "cur":
        return True
    if n[0] == "op":
        return any(_has_cur(c) for c in n[2])
    if n[0] == "mem":
        return _has_cur(n[1])
    return False


def _templates(rec, i):
    t = [ex for _p, _a, ex, _s in rec.slog[i]]
    t += [ex for _s, _k, ex, _o in rec.facts[i]]
    return t


def _replay_matches(rec, oracle):
    for i, want in enumerate(oracle):
        assert rec.replay(i) == want


# ---- 1. hello_world / 8. concrete fidelity -----------------------------------
def test_hello_world_replay_byte_exact():
    rec = record(_mem(PROGRAM, ORG), run_sub, ORG, range(0x0400, 0x040D), 1)
    assert bytes(v for _a, v in rec.replay(0)) == EXPECTED


def test_concrete_fidelity_vs_plain_vm():

    vm = PcodeVM(_mem(PROGRAM, ORG))
    run_sub(vm, ORG, {}, lift)
    rvm = RecVM(_mem(PROGRAM, ORG))
    rvm.reset_invocation()
    run_sub(rvm, ORG, {}, lift)
    assert bytes(vm.mem) == bytes(rvm.mem)


def test_concrete_fidelity_sid_wlog():

    prog = bytes.fromhex("A90F8D00D4EE01D460")
    vm = PcodeVM(_mem(prog, 0x1000))
    vm.wlog = []
    run_sub(vm, 0x1000, {}, lift)
    rvm = RecVM(_mem(prog, 0x1000))
    rvm.wlog = []
    rvm.reset_invocation()
    run_sub(rvm, 0x1000, {}, lift)
    assert vm.wlog == rvm.wlog and bytes(vm.mem) == bytes(rvm.mem)


# ---- 2. one program per fold channel -----------------------------------------
def test_channel_smc_opcode():
    prog = bytes.fromhex("AD20108D0C10A205A007EAEAEA8E00048C010460")
    for seed in (0xE8, 0xC8):
        data = {0x1020: seed}
        outs = {0x0400, 0x0401}
        rec = _rec(prog, 0x1000, outs, data=data)
        _replay_matches(rec, _oracle(prog, 0x1000, outs, data=data))
        ops = [f for f in rec.facts[0] if f[0] == 0x100C and f[1] == "opcode"]
        assert ops and ops[0][3] == seed


def test_channel_smc_operand_abs():
    prog = bytes.fromhex("AD20108D07108D0004EE0710AD071060")
    data = {0x1020: 0x00}
    rec = _rec(prog, 0x1000, {0x0400}, data=data)
    _replay_matches(rec, _oracle(prog, 0x1000, {0x0400}, data=data))
    place = [f for f in rec.facts[0] if f[1] == "place"]
    assert any(_refs(f[2], 0x1008) for f in place)


def test_channel_smc_operand_indy_plus1():
    prog = bytes.fromhex("AD30108D0910A000B1408D000460")
    base = {0x40: 0x00, 0x41: 0x30, 0x50: 0x00, 0x51: 0x31, 0x3000: 0xAB, 0x3100: 0xCD}
    for seed in (0x40, 0x50):
        data = {**base, 0x1030: seed}
        rec = _rec(prog, 0x1000, {0x0400}, data=data)
        _replay_matches(rec, _oracle(prog, 0x1000, {0x0400}, data=data))
        st = rec.slog[0][-1][2]
        assert _refs(st, 0x1009)


def test_channel_smc_branch_target():
    prog = bytes.fromhex("AD20108D0910A200D0FEE8E8E88E000460")
    for seed in (0x02, 0x03):
        data = {0x1020: seed}
        rec = _rec(prog, 0x1000, {0x0400}, data=data)
        _replay_matches(rec, _oracle(prog, 0x1000, {0x0400}, data=data))
        tgt = [f for f in rec.facts[0] if f[0] == 0x1008 and f[1] == "target"]
        assert tgt


def _with_stubs(base):
    d = dict(base)
    for a, b in (
        (0x1200, bytes.fromhex("A9118D000460")),
        (0x1210, bytes.fromhex("A9228D000460")),
    ):
        for k, byte in enumerate(b):
            d[a + k] = byte
    return d


def test_channel_jmpind_vector_rewrite():
    prog = bytes.fromhex("AD30108D4010A9128D41106C4010")
    for seed, marker in ((0x00, 0x11), (0x10, 0x22)):
        d = _with_stubs({0x1030: seed})
        rec = _rec(prog, 0x1000, {0x0400}, data=d)
        _replay_matches(rec, _oracle(prog, 0x1000, {0x0400}, data=d))
        assert rec.replay(0) == [(0x0400, marker)]
        vec = [f for f in rec.facts[0] if f[0] == 0x100B and f[1] == "target"]
        assert vec and vec[0][3] in (0x1200, 0x1210)


def test_channel_push_push_rts_dispatch():
    prog = bytes.fromhex("AD301048AD31104860")
    stubs = {}
    for a, b in ((0x1300, "A9118D000460"), (0x1310, "A9228D000460")):
        for k, byte in enumerate(bytes.fromhex(b)):
            stubs[a + k] = byte
    for (hi, lo), (target, marker) in (
        ((0x12, 0xFF), (0x1300, 0x11)),
        ((0x13, 0x0F), (0x1310, 0x22)),
    ):
        d = {**stubs, 0x1030: hi, 0x1031: lo}
        rec = _rec(prog, 0x1000, {0x0400}, data=d)
        _replay_matches(rec, _oracle(prog, 0x1000, {0x0400}, data=d))
        assert rec.replay(0) == [(0x0400, marker)]
        tgt = [f for f in rec.facts[0] if f[0] == 0x1008 and f[1] == "target"]
        assert tgt and tgt[0][3] == target


def test_channel_load_placement_alias():
    prog = bytes.fromhex("A9AA8D5010AE3010BD20108D000460")
    tbl = {0x1020 + k: 0x10 + k for k in range(0x40)}
    for seed, want in ((0x30, 0xAA), (0x00, 0x10)):
        d = {**tbl, 0x1030: seed}
        rec = _rec(prog, 0x1000, {0x0400}, data=d)
        _replay_matches(rec, _oracle(prog, 0x1000, {0x0400}, data=d))
        assert rec.replay(0) == [(0x0400, want)]
        if seed == 0x30:
            place = [f for f in rec.facts[0] if f[1] == "place"]
            assert any(f[3] == 0x1050 for f in place)


def test_channel_store_placement_alias():
    prog = bytes.fromhex("AE3010A9999D2010AD50108D000460")
    for seed, want in ((0x30, 0x99), (0x00, 0x00)):
        d = {0x1030: seed}
        rec = _rec(prog, 0x1000, {0x0400}, data=d)
        _replay_matches(rec, _oracle(prog, 0x1000, {0x0400}, data=d))
        assert rec.replay(0) == [(0x0400, want)]
        place = [f for f in rec.facts[0] if f[1] == "place"]
        assert any(f[3] == (0x1020 + seed) for f in place)


def test_channel_rti_modified_status():
    prog = bytes.fromhex("A91048A92048AD30104840")
    ret = bytes.fromhex("08688D000460")
    for seed in (0x81, 0x42):
        d = {0x1030: seed}
        d.update({0x1020 + k: b for k, b in enumerate(ret)})
        rec = _rec(prog, 0x1000, {0x0400}, data=d)
        _replay_matches(rec, _oracle(prog, 0x1000, {0x0400}, data=d))


def test_channel_brk_handler():
    # BRK pushes 3 (incl status) and vectors through $FFFE; RTI unwinds it.
    prog = bytes.fromhex("00EA8D010460")
    handler = bytes.fromhex("A9778D000440")
    data = {0xFFFE: 0x00, 0xFFFF: 0x11}
    data.update({0x1100 + k: b for k, b in enumerate(handler)})
    outs = {0x0400, 0x0401}
    rec = _rec(prog, 0x1000, outs, data=data)
    _replay_matches(rec, _oracle(prog, 0x1000, outs, data=data))
    assert rec.replay(0) == [(0x0400, 0x77), (0x0401, 0x77)]


# ---- 3. width regression -----------------------------------------------------
def test_width_wrap_absy():
    prog = bytes.fromhex("AC3010C8B900208D000460")
    tbl = {0x2000 + k: (0x80 + k) & 0xFF for k in range(0x100)}
    for seed in (0xFF, 0xFE, 0x00):
        d = {**tbl, 0x1030: seed}
        rec = _rec(prog, 0x1000, {0x0400}, data=d)
        _replay_matches(rec, _oracle(prog, 0x1000, {0x0400}, data=d))


# ---- 4. template stability ---------------------------------------------------
def test_template_stability_pointer_walk():
    prog = bytes.fromhex("A000B1108D0004E61060")
    data = {0x10: 0x00, 0x11: 0x20}
    data.update({0x2000 + k: (0xA0 + k) & 0xFF for k in range(0x10)})
    n = 8
    rec = _rec(prog, 0x1000, {0x0400}, n=n, data=data)
    _replay_matches(rec, _oracle(prog, 0x1000, {0x0400}, n=n, data=data))
    per0 = {repr(t) for t in _templates(rec, 0)}
    allt = {repr(t) for i in range(n) for t in _templates(rec, i)}
    assert allt == per0


# ---- 5. rewrite between load and use -----------------------------------------
def test_stale_cur_falls_back():
    prog = bytes.fromhex("A9AA8D2010AE2010A9BB8D20108E000460")
    rec = _rec(prog, 0x1000, {0x0400})
    assert rec.replay(0) == [(0x0400, 0xAA)]
    st = rec.slog[0][-1][2]
    assert not _has_cur(st) and st == ("const", 0xAA, 1)


def test_fresh_cur_present_without_rewrite():
    prog = bytes.fromhex("A9AA8D2010AE20108E000460")
    rec = _rec(prog, 0x1000, {0x0400})
    assert rec.replay(0) == [(0x0400, 0xAA)]
    assert _has_cur(rec.slog[0][-1][2])


# ---- 6. stack-tower canonicalisation -----------------------------------------
def _nest_mem(depth):
    m = bytearray(0x10000)
    for k in range(depth - 1):
        base = 0x1000 + 0x10 * k
        tgt = 0x1000 + 0x10 * (k + 1)
        m[base : base + 4] = bytes([0x20, tgt & 0xFF, (tgt >> 8) & 0xFF, 0x60])
    deep = 0x1000 + 0x10 * (depth - 1)
    m[deep : deep + 6] = bytes.fromhex("A95A8D000460")
    return m


def test_stack_tower_bounded():
    def maxnodes(depth):
        rec = record(_nest_mem(depth), run_sub, 0x1000, {0x0400}, 1)
        best = 0
        for t in _templates(rec, 0):
            best = max(best, _nodes(t))
        for _a, (fe, _s) in rec.F[0].items():
            best = max(best, _nodes(fe))
        return best

    assert maxnodes(4) == maxnodes(12)


# ---- 7. determinism ----------------------------------------------------------
def test_determinism():
    a = _rec(PROGRAM, ORG, range(0x0400, 0x040D))
    b = _rec(PROGRAM, ORG, range(0x0400, 0x040D))
    assert [repr(x) for x in a.slog[0]] == [repr(x) for x in b.slog[0]]
    assert [repr(x) for x in a.facts[0]] == [repr(x) for x in b.facts[0]]


def test_assertion_optout_runs():
    rec = _rec(PROGRAM, ORG, range(0x0400, 0x040D), assertion=False)
    assert bytes(v for _a, v in rec.replay(0)) == EXPECTED
