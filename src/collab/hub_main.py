"""The detached hub process: ``python -m collab.hub_main <session_id>``.

Owns the tunnel as well as the server, so shutting the hub down takes the
public URL with it rather than leaving a dangling tunnel.
"""

from __future__ import annotations

import logging
import os
import sys

import uvicorn

from .server.app import create_app
from .server.session import HubConfig
from .server.store import Store
from .server.tunnel import start_tunnel


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

    tunnel = None
    if os.environ.get("COLLAB_NO_TUNNEL") != "1":
        tunnel = start_tunnel(cfg.port, log_path=str(cfg.dir / "ngrok.log"))
    if tunnel is not None:
        cfg.public_url = tunnel.public_url
        cfg.tunnel = "ngrok"
    else:
        cfg.public_url = ""
        cfg.tunnel = "none"
    # Written before serving so `collab host` can print the real URL.
    cfg.save()

    store = Store(cfg.db_path)
    app = create_app(
        store=store,
        session_id=cfg.session_id,
        host_name=cfg.host_name,
        public_url=cfg.public_url or cfg.local_url,
        invite_code=cfg.invite,
    )
    try:
        uvicorn.run(app, host=cfg.bind, port=cfg.port, log_level="warning", access_log=False)
    finally:
        if tunnel is not None:
            tunnel.stop()
        store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
