"""The per-test guards of tests/conftest.py and pyproject.toml (docs/testing.md)."""

import os
import resource

import pytest

import conftest


def test_heap_limit_shares_the_ram_among_the_workers():
    ram = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    assert conftest.heap_limit({}) == min(conftest.HEAP_MAX, ram)
    many = conftest.heap_limit({"PYTEST_XDIST_WORKER_COUNT": str(ram // (1 << 20))})
    assert many <= 1 << 20
    assert conftest.heap_limit({"DI_TEST_HEAP": "12345"}) == 12345
    assert conftest.heap_limit({"DI_TEST_HEAP": "0"}) is None


@pytest.mark.skipif("DI_TEST_HEAP" in os.environ, reason="the limit is overridden")
def test_this_process_runs_under_the_limit():
    soft, _ = resource.getrlimit(resource.RLIMIT_DATA)
    assert soft != resource.RLIM_INFINITY and soft <= conftest.HEAP_MAX
    with pytest.raises(MemoryError):
        bytearray(conftest.HEAP_MAX)


def test_tests_run_under_a_timeout(request):
    assert request.config.getini("timeout") == "30"
    assert request.config.getini("timeout_method") == "thread"
