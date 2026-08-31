"""File transfer: size cap, privacy, checksum, and delete-on-receipt."""

from __future__ import annotations

import hashlib
import io

from collab.protocol import MAX_FILE_BYTES


def _join(client, session, name):
    r = client.post("/ext/collab/v1/join",
                    json={"invite": session["invite"], "name": name, "hello": {}})
    return {"Authorization": f"Bearer {r.json()['token']}"}


def _upload(client, headers, content: bytes, name="artifact.bin", **params):
    return client.post("/ext/collab/v1/files", headers=headers,
                       files={"file": (name, io.BytesIO(content), "application/octet-stream")},
                       params=params)


def test_upload_download_roundtrip(client, session, host_headers):
    bob = _join(client, session, "bob")
    payload = b"\x00\x01binary build artifact\xff" * 100

    up = _upload(client, host_headers, payload, name="build.tar.gz")
    assert up.status_code == 200
    record = up.json()
    assert record["size"] == len(payload)
    assert record["sha256"] == hashlib.sha256(payload).hexdigest()
    assert record["download_url"].endswith(f"/files/{record['id']}/content")

    down = client.get(f"/ext/collab/v1/files/{record['id']}/content", headers=bob)
    assert down.status_code == 200
    assert down.content == payload, "bytes must survive exactly"
    assert down.headers["X-Collab-Sha256"] == record["sha256"]


def test_upload_is_announced_so_the_other_agent_learns_of_it(client, session, host_headers):
    _join(client, session, "bob")
    up = _upload(client, host_headers, b"data", name="report.pdf").json()
    events = client.get("/ext/collab/v1/history", headers=host_headers).json()["events"]
    shared = [e for e in events if e["kind"] == "file"]
    assert shared and shared[-1]["body"]["name"] == "report.pdf"
    assert shared[-1]["body"]["id"] == up["id"]


def test_file_is_deleted_once_receipt_is_confirmed(client, session, host_headers):
    bob = _join(client, session, "bob")
    record = _upload(client, host_headers, b"payload", name="a.bin").json()

    ack = client.post(f"/ext/collab/v1/files/{record['id']}/ack", headers=bob)
    assert ack.status_code == 200
    assert ack.json()["deleted"] is True

    # The only copy is gone, so a second fetch must fail rather than half-work.
    again = client.get(f"/ext/collab/v1/files/{record['id']}/content", headers=bob)
    assert again.status_code == 404
    assert client.get("/ext/collab/v1/files", headers=bob).json()["files"] == []


def test_ack_is_announced_back_to_the_sender(client, session, host_headers):
    bob = _join(client, session, "bob")
    record = _upload(client, host_headers, b"x", name="a.bin", to="bob").json()
    client.post(f"/ext/collab/v1/files/{record['id']}/ack", headers=bob)
    events = client.get("/ext/collab/v1/history", headers=host_headers).json()["events"]
    assert any(e["kind"] == "file" and e["body"].get("action") == "received" for e in events)


def test_oversized_upload_is_refused(client, host_headers):
    too_big = b"x" * (MAX_FILE_BYTES + 1024)
    r = _upload(client, host_headers, too_big, name="huge.bin")
    assert r.status_code == 413
    assert "10MB" in r.json()["detail"]
    assert client.get("/ext/collab/v1/files", headers=host_headers).json()["files"] == []


def test_a_file_sent_to_one_person_is_private(client, session, host_headers):
    bob = _join(client, session, "bob")
    carol = _join(client, session, "carol")
    record = _upload(client, host_headers, b"secret", name="key.pem", to="bob").json()

    assert client.get(f"/ext/collab/v1/files/{record['id']}/content",
                      headers=carol).status_code == 403
    assert client.get(f"/ext/collab/v1/files/{record['id']}/content",
                      headers=bob).status_code == 200
    assert client.get("/ext/collab/v1/files", headers=carol).json()["files"] == []


def test_download_requires_a_token(client, host_headers):
    record = _upload(client, host_headers, b"x").json()
    assert client.get(f"/ext/collab/v1/files/{record['id']}/content").status_code == 401


def test_sender_can_withdraw_a_file(client, session, host_headers):
    bob = _join(client, session, "bob")
    record = _upload(client, host_headers, b"oops", name="wrong.bin").json()
    assert client.delete(f"/ext/collab/v1/files/{record['id']}", headers=bob).status_code == 403
    assert client.delete(f"/ext/collab/v1/files/{record['id']}",
                         headers=host_headers).status_code == 200
    assert client.get(f"/ext/collab/v1/files/{record['id']}/content",
                      headers=bob).status_code == 404


# --- a display name is not identity -------------------------------------------
#
# Access is decided on participant ids. Only the sender side was ever an
# accidental lockout: delete_file compared raw names, while _may_touch resolved
# them, and resolve_name falls back to participant_names -- so a freed but
# unclaimed name still reached its old owner. The recipient test below is a
# regression guard for the SPEC section 9 promise, not a bug reproduction; it
# passes on the pre-change code too. The other three carry the change.


def _rename(client, headers, name):
    r = client.post("/ext/collab/v1/rename", json={"name": name}, headers=headers)
    assert r.status_code == 200, r.text
    return r


def test_the_recipient_can_still_download_after_renaming_themselves(
        client, session, host_headers):
    bob = _join(client, session, "bob")
    record = _upload(client, host_headers, b"secret", name="key.pem", to="bob").json()

    _rename(client, bob, "bob2")

    down = client.get(f"/ext/collab/v1/files/{record['id']}/content", headers=bob)
    assert down.status_code == 200, "a rename must not lock you out of your own file"
    assert down.content == b"secret"
    assert client.post(f"/ext/collab/v1/files/{record['id']}/ack",
                       headers=bob).status_code == 200


def test_the_sender_can_still_withdraw_after_renaming_themselves(
        client, session, host_headers):
    # The sender is a plain participant, not the host -- otherwise the host
    # bypass in delete_file would pass this test whatever the id check did.
    bob = _join(client, session, "bob")
    _join(client, session, "carol")
    record = _upload(client, bob, b"oops", name="wrong.bin", to="carol").json()

    _rename(client, bob, "bob2")

    assert client.delete(f"/ext/collab/v1/files/{record['id']}",
                         headers=bob).status_code == 200


def test_claiming_a_freed_name_inherits_none_of_its_files(client, session, host_headers):
    bob = _join(client, session, "bob")
    record = _upload(client, host_headers, b"secret", name="key.pem", to="bob").json()
    _rename(client, bob, "bob2")

    # The name "bob" is free again, and whoever takes it must inherit nothing.
    eve = _join(client, session, "bob")

    assert client.get(f"/ext/collab/v1/files/{record['id']}/content",
                      headers=eve).status_code == 403
    assert client.get("/ext/collab/v1/files", headers=eve).json()["files"] == []
    assert client.post(f"/ext/collab/v1/files/{record['id']}/ack",
                       headers=eve).status_code == 403


def test_a_file_with_no_ids_is_refused_to_everyone_but_the_host(
        client, session, host_headers):
    """A row written before the id columns cannot prove its two ends."""
    bob = _join(client, session, "bob")
    carol = _join(client, session, "carol")
    record = _upload(client, bob, b"legacy", name="old.bin", to="carol").json()

    store = session["store"]
    with store._lock:
        store._db.execute(
            "UPDATE files SET sender_id=NULL, recipient_id=NULL WHERE id=?",
            (record["id"],),
        )
        store._db.commit()

    # Even the real recipient is refused -- guessing at the ends is the bug.
    assert client.get(f"/ext/collab/v1/files/{record['id']}/content",
                      headers=carol).status_code == 403
    assert client.get("/ext/collab/v1/files", headers=carol).json()["files"] == []
    # The host is the one exception: the blob is on their own disk.
    assert client.get(f"/ext/collab/v1/files/{record['id']}/content",
                      headers=host_headers).status_code == 200


def test_acking_a_legacy_file_does_not_broadcast_it_on_replay(
        client, session, host_headers):
    """An addressed envelope with no id is private live and public on replay.

    ``hub._entitled`` accepts ``to`` or ``to_id``; ``store._visible_to`` reads
    ``recipient_id`` alone and treats an empty one as room-wide. So a reply
    envelope whose ``to_id`` fell back to ``""`` reaches almost nobody live and
    then everybody through ``/history``. The only row with no ``sender_id`` is
    one predating the id columns, and the host bypass is what makes it ackable.
    """
    bob = _join(client, session, "bob")
    carol = _join(client, session, "carol")
    dave = _join(client, session, "dave")
    record = _upload(client, bob, b"legacy", name="severance.pdf", to="carol").json()

    store = session["store"]
    with store._lock:
        store._db.execute(
            "UPDATE files SET sender_id=NULL, recipient_id=NULL WHERE id=?",
            (record["id"],),
        )
        store._db.commit()

    assert client.post(f"/ext/collab/v1/files/{record['id']}/ack",
                       headers=host_headers).status_code == 200

    seen = client.get("/ext/collab/v1/history", headers=dave).json()["events"]
    assert not any("severance.pdf" in str(e) for e in seen), \
        "a private transfer must not surface to a third party on replay"
    # The sender still learns their file landed.
    mine = client.get("/ext/collab/v1/history", headers=bob).json()["events"]
    assert any("severance.pdf" in str(e) for e in mine)
