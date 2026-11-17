# Testing

## Guards

Every test runs under two guards, so a lift that runs away fails in seconds with its stacks
rather than holding a worker and the host's memory:

- **Time**: `pytest-timeout` (`[tool.pytest.ini_options]` in `pyproject.toml`) fails a test
  after 30 s with `timeout_method = "thread"`, which dumps every thread's stack. A test that
  legitimately needs longer carries `@pytest.mark.timeout(n)`; `--timeout 0` disables the guard
  for a run. The thread method ends the process, so a native lift stops too; under xdist a test
  that timed out shows as a crashed worker naming it, and run alone it prints the stacks.
  Lifts parallelise over the host's cores, so a test's time scales with them: an override is
  measured on a runner of CI's size (`taskset -c 0-3 pytest -n 4`).
- **Memory**: `tests/conftest.py` sets each test process's `RLIMIT_DATA` to its share of the
  host's RAM among the xdist workers, at most 4 GiB. `RLIMIT_DATA` counts the writable memory a
  process maps but not what it only reserves (thread arenas, JIT pools), so it bounds the heap
  where `RLIMIT_AS` would trip on reservations. A Python allocation over the limit raises
  `MemoryError`; a native one aborts the worker, which xdist reports and replaces.
  `DI_TEST_HEAP=<bytes>` overrides it (`0`: no limit).

`python tools/testimages.py` builds and pulls the Docker images the tests use (CI runs it before
the tests), so no test spends its timeout provisioning one.

## Inner loop

Build the native extension with the `dev-release` profile (`Cargo.toml`): optimized like
`release` but without LTO, in many codegen units and incrementally, so a rebuild after an edit
compiles only what changed, across cores. `release` (fat LTO, one codegen unit) is for the
published wheels.

```bash
maturin develop --profile dev-release
```

Run the one test being worked on, under a wall-clock bound:

```bash
timeout 30 pytest tests/test_cgen.py -k name -x -q
```

A test that is slow is profiled, not waited on:

```bash
sudo -n env "PATH=$PATH" py-spy dump --pid N    # N: the pytest (or xdist worker) process
```

`py-spy dump --native` adds the Rust frames of `deity_informant._native`.

The full suite (`pytest tests/ -n auto`) is for before a push.

## Generated-output identity

`tools/cgenref.py` checks a change preserves the generated C: for a wheel it translates
gravity-inversion's tunes and fuzz players, and with `--tests` every `cgen.translate` the cgen
tests make (named by a digest of its arguments), and diffs the `.c`, `.h` and `info` against a
reference made from another wheel:

```bash
python tools/cgenref.py check main.whl out/ref --gi GI --tests
python tools/cgenref.py check new.whl out/new --gi GI --tests --ref out/ref
```
