from __future__ import annotations

import pytest

from collab.server.app import create_app
from collab.server.auth import new_secret
from collab.server.store import Store


@pytest.fixture()
def session(tmp_path):
    """A hub with a host already registered and an open invite."""
    store = Store(tmp_path / "hub.db")
    invite = new_secret()
    host_token = new_secret()
    store.add_invite(invite, ttl_seconds=3600)
    store.add_participant("alice", host_token, is_host=True, meta={"focus": "auth refactor"})
    store.add_room("general", "alice")
    app = create_app(store=store, session_id="s_test", host_name="alice",
                     public_url="http://testserver")
    return {"app": app, "store": store, "invite": invite, "host_token": host_token}


@pytest.fixture()
def client(session):
    from fastapi.testclient import TestClient
    with TestClient(session["app"]) as c:
        yield c


@pytest.fixture()
def host_headers(session):
    return {"Authorization": f"Bearer {session['host_token']}"}


@pytest.fixture()
def live_server(session):
    """A real uvicorn server on a free port.

    Streaming responses do not behave under Starlette's TestClient, and the SSE
    feed is the part most worth testing honestly, so these tests speak real HTTP.
    """
    import threading
    import time

    import httpx
    import uvicorn

    from collab.server.tunnel import free_port

    port = free_port()
    config = uvicorn.Config(session["app"], host="127.0.0.1", port=port,
                            log_level="error", access_log=False)
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    base = f"http://127.0.0.1:{port}"
    deadline = time.time() + 20
    while time.time() < deadline:
        try:
            if httpx.get(f"{base}/ext/collab/v1/health", timeout=1.0).status_code == 200:
                break
        except httpx.HTTPError:
            time.sleep(0.1)
    else:
        raise RuntimeError("the test server did not start")

    yield {"base": base, **session}

    server.should_exit = True
    thread.join(timeout=10)
