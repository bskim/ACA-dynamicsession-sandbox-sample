"""Stdlib-only loopback RAM witness. Never persists RAM state or daemon output."""
from __future__ import annotations

import argparse
import hashlib
import http.client
from http.server import BaseHTTPRequestHandler, HTTPServer
import json
import math
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
import time

SENTINEL_CONTENT = b"ACA sandbox lifecycle disk witness v1\n"
BUFFER_SIZE = 65536


def disk_evidence(directory: Path) -> dict:
    path = directory / "sentinel.txt"
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return {"disk_exists": False, "disk_sha256": None}
    return {"disk_exists": True, "disk_sha256": hashlib.sha256(data).hexdigest()}


def request(port: int, action: str, timeout: float) -> dict | None:
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout)
    try:
        connection.request("POST", "/" + action, body=b"")
        response = connection.getresponse()
        if response.status != 200:
            raise RuntimeError("Unexpected worker response")
        payload = json.loads(response.read(8192))
        if payload.get("protocol") != "aca-state-worker-v1":
            raise RuntimeError("Loopback port belongs to another service")
        return payload
    except (ConnectionRefusedError, TimeoutError, ConnectionResetError, http.client.RemoteDisconnected):
        return None
    finally:
        connection.close()


def serve(port: int) -> None:
    # These values are created only in the daemon, never passed in argv or saved.
    buffer = secrets.token_bytes(BUFFER_SIZE)
    nonce = secrets.token_hex(32)
    fingerprint = hashlib.sha256(f"{os.getpid()}:{time.monotonic_ns()}:{secrets.token_hex(32)}".encode()).hexdigest()
    counter = 0

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_POST(self):
            nonlocal counter
            if self.path not in {"/probe", "/increment", "/stop"}:
                self.send_error(404)
                return
            counter += 1
            body = json.dumps({"protocol": "aca-state-worker-v1", "worker_alive": True,
                               "worker_boot_nonce": nonce, "in_memory_counter": counter,
                               "buffer_checksum": hashlib.sha256(buffer).hexdigest(),
                               "process_start_fingerprint": fingerprint, "pid": os.getpid()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            if self.path == "/stop":
                threading.Thread(target=self.server.shutdown, daemon=True).start()

    server = HTTPServer(("127.0.0.1", port), Handler)
    with server:
        server.serve_forever(poll_interval=0.05)


def run(action: str, directory: Path, port: int, timeout: float) -> dict:
    if action == "start":
        existing = request(port, "probe", min(timeout, 0.5))
        if existing:
            return disk_evidence(directory) | existing | {"worker_started": False}
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "sentinel.txt"
        # Restart must not repair or replace a missing/corrupt snapshot silently.
        if not path.exists():
            with path.open("xb") as file:
                file.write(SENTINEL_CONTENT)
                file.flush()
                os.fsync(file.fileno())
        process = subprocess.Popen(
            [sys.executable, str(Path(__file__).resolve()), "serve", "--port", str(port)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            close_fds=True, start_new_session=os.name != "nt",
        )
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            answer = request(port, "probe", min(0.25, timeout))
            if answer:
                return disk_evidence(directory) | answer | {"worker_started": True}
            if process.poll() is not None:
                raise RuntimeError("Worker daemon failed to start")
            time.sleep(0.02)
        process.terminate()
        process.wait(timeout=2)
        raise TimeoutError("Worker readiness deadline exceeded")
    answer = request(port, action, timeout)
    if action == "increment" and answer is None:
        raise RuntimeError("Worker is not running")
    if action == "stop":
        deadline = time.monotonic() + timeout
        while answer and time.monotonic() < deadline:
            time.sleep(0.02)
            if request(port, "probe", min(timeout, 0.25)) is None:
                break
        else:
            if answer:
                raise TimeoutError("Worker did not stop")
        return disk_evidence(directory) | {"worker_alive": False}
    return disk_evidence(directory) | (answer or {"worker_alive": False})


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("start", "probe", "increment", "stop", "serve"))
    parser.add_argument("--directory", type=Path, default=Path("/workspace/demo"))
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--timeout", type=float, default=5.0)
    args = parser.parse_args()
    if not 1 <= args.port <= 65535 or not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("Port and timeout must be positive and within range")
    if args.action == "serve":
        serve(args.port)
        return 0
    try:
        print(json.dumps(run(args.action, args.directory, args.port, args.timeout), sort_keys=True))
        return 0
    except (OSError, ValueError, RuntimeError, http.client.HTTPException) as exc:
        print(json.dumps({"error": type(exc).__name__}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
