# Examples

`hello_world.py` depends only on `deity_informant` (no external fixtures). It runs a
33-byte C64 program that prints `HELLO, WORLD!` using illegal opcodes (`LAX`, `ISC`)
and self-modifying code, and is verified end to end by `tests/test_hello_world.py`.

Run `python examples/hello_world.py`; walkthrough (including through Ghidra) in
[docs/hello-world.md](../docs/hello-world.md).

Not shipped in the wheel (package discovery is scoped to `deity_informant*`).
