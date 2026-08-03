import os
import socket
import subprocess
import sys
import tempfile
import time

os.environ["OMP_NUM_THREADS"] = "1"
os.environ["OPENBLAS_NUM_THREADS"] = "1"
database_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
database_file.close()
os.environ["DATABASE_URL"] = f"sqlite:///{database_file.name}"

import httpx  # noqa: E402
import pytest  # noqa: E402


@pytest.fixture
def client():
    api_database = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    api_database.close()
    process_env = os.environ.copy()
    process_env["DATABASE_URL"] = f"sqlite:///{api_database.name}"
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "mtglogger.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        stdout=subprocess.DEVNULL,
        env=process_env,
    )
    base_url = f"http://127.0.0.1:{port}"
    try:
        for _ in range(100):
            try:
                with httpx.Client(base_url=base_url, timeout=0.2) as probe:
                    if probe.get("/api/health").status_code == 200:
                        break
            except httpx.HTTPError:
                time.sleep(0.05)
        else:
            raise RuntimeError("Test API did not start")
        with httpx.Client(base_url=base_url, timeout=5) as test_client:
            yield test_client
    finally:
        process.terminate()
        process.wait(timeout=5)
        os.unlink(api_database.name)
