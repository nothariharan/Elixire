"""pytest fixtures for elixir api integration tests."""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

ELIXIR_ROOT = Path(__file__).resolve().parent

HEALTH_TIMEOUT = 30.0


def _free_port() -> str:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return str(sock.getsockname()[1])


def pytest_configure(config):
    config.addinivalue_line("markers", "slow: tests that call real LLM APIs")
    config.addinivalue_line("markers", "integration: end-to-end pipeline tests")
    config.addinivalue_line("markers", "band: requires live Band agents (run_all.py)")


def _wait_for_health(base: str, timeout: float = HEALTH_TIMEOUT) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = requests.get(f"{base}/health", timeout=2)
            if r.status_code == 200:
                return
        except requests.RequestException:
            pass
        time.sleep(0.5)
    raise RuntimeError(f"server at {base} did not become healthy")


def _start_server(port: str, extra_env: dict | None = None) -> subprocess.Popen:
    env = {**os.environ, **(extra_env or {})}
    return subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--port", port],
        cwd=str(ELIXIR_ROOT),
        env=env,
    )


def _stop_server(proc: subprocess.Popen) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait(timeout=5)


@pytest.fixture(scope="module")
def legacy_api():
    """uvicorn with in-process langgraph — no band room calls."""
    port = _free_port()
    proc = _start_server(port, {"ELIXIR_LEGACY_GRAPH": "1"})
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base)
        yield base
    finally:
        _stop_server(proc)


@pytest.fixture(scope="module")
def legacy_aml_api():
    """legacy graph with invalid AML key to force featherless fallback."""
    port = _free_port()
    proc = _start_server(
        port,
        {
            "ELIXIR_LEGACY_GRAPH": "1",
            "AML_API_KEY": "invalid-key-for-testing",
            "AML_MODEL_VERIFICATION": "test-model",
        },
    )
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base)
        yield base
    finally:
        _stop_server(proc)


@pytest.fixture(scope="module")
def band_api():
    """uvicorn in default band mode when agent configs exist."""
    port = _free_port()
    proc = _start_server(port, {"ELIXIR_LEGACY_GRAPH": "0"})
    base = f"http://127.0.0.1:{port}"
    try:
        _wait_for_health(base)
        health = requests.get(f"{base}/health", timeout=5).json()
        yield base, health
    finally:
        _stop_server(proc)
