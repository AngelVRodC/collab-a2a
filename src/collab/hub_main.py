"""The detached hub process: ``python -m collab.hub_main <session_id>``.

Owns the tunnel as well as the server, so shutting the hub down takes the
public URL with it rather than leaving a dangling tunnel — and so a tunnel that
dies on its own can be brought back without disturbing the session.
"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from .server.app import create_app
from .server.session import HubConfig
from .server.store import Store
from .server.tunnel import TunnelSupervisor

logger = logging.getLogger(__name__)


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if len(sys.argv) < 2:
        print("usage: python -m collab.hub_main <session_id>", file=sys.stderr)
        return 2

    cfg = HubConfig.load(sys.argv[1], os.environ.get("COLLAB_HOME"))
    if cfg is None:
        print(f"no such session: {sys.argv[1]}", file=sys.stderr)
        return 1

    cfg.pid = os.getpid()

    supervisor = None
    if os.environ.get("COLLAB_NO_TUNNEL") != "1":
        supervisor = TunnelSupervisor(
            cfg.port,
            log_path=str(cfg.dir / "ngrok.log"),
            domain=cfg.domain or None,
        )
        supervisor.start()

    if supervisor is not None and supervisor.public_url:
        cfg.public_url = supervisor.public_url
        cfg.tunnel = "ngrok"
        # Only what we started: a tunnel we merely reused belongs to whoever
        # launched it, and stopping it would be taking something that is not
        # ours.
        cfg.tunnel_pid = supervisor.own_pid()
    else:
        cfg.public_url = ""
        cfg.tunnel = "none"
        cfg.tunnel_pid = 0
    # Written before serving so `collab host` can print the real URL.
    cfg.save()

    def remember_url(url: str) -> None:
        """Persist a new public address so `collab url` stays correct."""
        latest = HubConfig.load(cfg.session_id, cfg.home) or cfg
        latest.public_url = url
        latest.pid = os.getpid()
        # A relaunched tunnel is a different process.
        latest.tunnel_pid = supervisor.own_pid() if supervisor else 0
        latest.save()
        logger.warning("tunnel came back on a new address: %s", url)

    store = Store(cfg.db_path)
    app = create_app(
        store=store,
        session_id=cfg.session_id,
        host_name=cfg.host_name,
        public_url=cfg.public_url or cfg.local_url,
        invite_code=cfg.invite,
        title=cfg.title,
        supervisor=supervisor,
        on_url_change=remember_url,
    )
    try:
        uvicorn.run(app, host=cfg.bind, port=cfg.port, log_level="warning", access_log=False)
    finally:
        if supervisor is not None:
            supervisor.stop()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
