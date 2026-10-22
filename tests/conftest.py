"""Shared fixtures."""
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "real_flag_io: exercise the emergency-stop flag's real database I/O")


@pytest.fixture(autouse=True)
def _isolate_emergency_flag_io(request):
    """The emergency-stop flag is stored in the DB. Unit tests that mock the connection
    expect a fixed sequence of reads, so stub the flag I/O unless a test opts out."""
    if request.node.get_closest_marker("real_flag_io"):
        yield
        return
    from unittest.mock import patch
    with patch("paper.circuit_breaker._read_emergency_flag", return_value=(False, "")), \
         patch("paper.circuit_breaker._persist_emergency_flag", return_value=True):
        yield
