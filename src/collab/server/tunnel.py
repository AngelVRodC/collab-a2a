"""Optional public exposure via ngrok.

ngrok is detected and used when present, and recommended when not — it is never
installed automatically.  Without a tunnel the hub is still fully usable; it is
just reachable on this machine and LAN only.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import socket
import subprocess
import time
from dataclasses import dataclass

import httpx

NGROK_API = "http://127.0.0.1:4040/api/tunnels"
START_TIMEOUT = 25.0


def ngrok_path() -> str | None:
    return shutil.which("ngrok")


def ngrok_version() -> str | None:
    exe = ngrok_path()
    if not exe:
        return None
    try:
        out = subprocess.run([exe, "version"], capture_output=True, text=True,
                             timeout=5, check=False).stdout.strip()
        return out.splitlines()[0] if out else None
    except (OSError, subprocess.SubprocessError):
        return None


def local_ip() -> str:
    """Best-effort LAN address, for sharing without a tunnel."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))  # no packets sent; just picks the route
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@dataclass
class Tunnel:
    public_url: str
    process: subprocess.Popen | None = None

    def stop(self) -> None:
        if self.process and self.process.poll() is None:
            self.process.terminate()
            with contextlib.suppress(subprocess.TimeoutExpired):
                self.process.wait(timeout=5)


def _existing_tunnel(port: int) -> str | None:
    """Reuse a tunnel an already-running ngrok agent has for this port."""
    try:
        r = httpx.get(NGROK_API, timeout=2.0)
        if r.status_code != 200:
            return None
        for t in r.json().get("tunnels", []):
            addr = t.get("config", {}).get("addr", "")
            if addr.endswith(f":{port}") and t.get("public_url", "").startswith("https://"):
                return t["public_url"]
    except (httpx.HTTPError, ValueError):
        return None
    return None


def start_tunnel(port: int, *, log_path: str | None = None) -> Tunnel | None:
    """Return a public https URL for ``port``, or None if ngrok is unavailable."""
    exe = ngrok_path()
    if not exe:
        return None

    if (url := _existing_tunnel(port)) is not None:
        return Tunnel(public_url=url, process=None)

    log = open(log_path, "a") if log_path else subprocess.DEVNULL
    proc = subprocess.Popen(
        [exe, "http", str(port), "--log", "stdout"],
        stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        start_new_session=True, env=os.environ.copy(),
    )

    deadline = time.time() + START_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            return None  # ngrok exited (missing authtoken is the usual cause)
        if (url := _existing_tunnel(port)) is not None:
            return Tunnel(public_url=url, process=proc)
        time.sleep(0.4)

    proc.terminate()
    return None


NO_NGROK_HELP = """\
ngrok was not found, so this session is reachable on this machine only.

To share it with someone else, either:
  1. install ngrok      https://ngrok.com/download   then re-run `collab host`
  2. or tunnel it yourself, and hand out that URL instead:
       ngrok http {port}
       cloudflared tunnel --url http://localhost:{port}
       tailscale funnel {port}
"""
