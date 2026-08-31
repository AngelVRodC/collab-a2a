"""Usage sharing: how it travels, and what it is for.

The point is that an agent can look at who has quota left before handing out
the next task, so the figures have to reach everyone, not just the host.
"""

from __future__ import annotations

from collab.config import share_stats_enabled


def _join(client, session, name):
    r = client.post("/ext/collab/v1/join",
                    json={"invite": session["invite"], "name": name, "hello": {}})
    assert r.status_code == 200, r.text
    return r.json()


def _headers(joined):
    return {"Authorization": f"Bearer {joined['token']}"}


def _person(client, headers, name):
    people = client.get("/ext/collab/v1/participants",
                        headers=headers).json()["participants"]
    return next(p for p in people if p["name"] == name)


def test_sharing_is_on_by_default(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_CONFIG", str(tmp_path / "config.json"))
    assert share_stats_enabled() is True


def test_sharing_can_be_turned_off(tmp_path, monkeypatch):
    monkeypatch.setenv("COLLAB_CONFIG", str(tmp_path / "config.json"))
    from collab.config import set_share_stats

    set_share_stats(False)
    assert share_stats_enabled() is False


def test_stats_posted_to_the_hub_reach_everyone(client, session, host_headers):
    """Sent to the host, but the whole session can read them."""
    bob = _join(client, session, "bob")
    client.post("/ext/collab/v1/stats", headers=_headers(bob), json={
        "machine": "bobs-laptop",
        "stats": {"model": "Opus 5", "cost_usd": 1.24, "quota_five_hour": 42.0},
    })

    seen = _person(client, host_headers, "bob")
    assert seen["machine"] == "bobs-laptop"
    assert seen["stats"]["quota_five_hour"] == 42.0

    # And a third party sees them too, not just the host.
    carol = _join(client, session, "carol")
    assert _person(client, _headers(carol), "bob")["stats"]["cost_usd"] == 1.24


def test_stats_ride_along_with_an_ordinary_message(client, session, host_headers):
    """Piggybacking keeps them current without a separate heartbeat."""
    bob = _join(client, session, "bob")
    client.post("/ext/collab/v1/messages", headers=_headers(bob), json={
        "text": "on it",
        "stats": {"quota_five_hour": 88.5, "model": "Opus 5"},
    })
    assert _person(client, host_headers, "bob")["stats"]["quota_five_hour"] == 88.5


def test_later_stats_merge_rather_than_replace(client, session, host_headers):
    bob = _join(client, session, "bob")
    h = _headers(bob)
    client.post("/ext/collab/v1/messages", headers=h,
                json={"text": "a", "stats": {"model": "Opus 5", "cost_usd": 1.0}})
    client.post("/ext/collab/v1/messages", headers=h,
                json={"text": "b", "stats": {"cost_usd": 2.0}})

    stats = _person(client, host_headers, "bob")["stats"]
    assert stats["cost_usd"] == 2.0
    assert stats["model"] == "Opus 5", "an update must not drop what it omits"


def test_quota_is_readable_for_balancing_work(client, session, host_headers):
    """The whole point: pick whoever has headroom left."""
    bob = _join(client, session, "bob")
    carol = _join(client, session, "carol")
    client.post("/ext/collab/v1/messages", headers=_headers(bob),
                json={"text": "x", "stats": {"quota_five_hour": 91.0}})
    client.post("/ext/collab/v1/messages", headers=_headers(carol),
                json={"text": "y", "stats": {"quota_five_hour": 12.0}})

    people = client.get("/ext/collab/v1/participants",
                        headers=host_headers).json()["participants"]
    with_quota = [(p["name"], p["stats"]["quota_five_hour"])
                  for p in people if (p.get("stats") or {}).get("quota_five_hour")]
    assert min(with_quota, key=lambda pair: pair[1])[0] == "carol"


def test_an_agent_that_shares_nothing_is_not_a_problem(client, session, host_headers):
    _join(client, session, "bob")
    assert _person(client, host_headers, "bob").get("stats") in ({}, None)
