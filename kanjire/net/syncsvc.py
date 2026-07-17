"""Cross-device progress sync over the public relay — E2E encrypted.

The "account" is a random 32-byte secret key that never leaves the player's
devices. Linking a new device is a one-time pairing: the host shows a short
code, publishes the account key encrypted under an argon2id-derived key on a
retained topic, and the joiner redeems it. From then on every device
publishes its progress snapshot (gzip → SecretBox) in retained chunks under
an account topic derived by hashing the key — the relay only ever carries
ciphertext, and only key-holders can find or read it.

Convergence: devices merge every snapshot they see (kanjire.data.syncmerge
is idempotent/commutative/monotone) and republish only when their own
content changed — the account settles to identical digests everywhere. No
server, no storage, nothing to pay for: the broker relays and briefly
retains, the devices own the data.

Threading contract: paho callbacks only enqueue bytes; ALL state changes
(merge, export, publish) happen in :meth:`tick`, called from the UI thread
(sqlite connections are not thread-safe across threads).
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import secrets
import time
import uuid

from kanjire.net import config
from kanjire.net.transport import PahoTransport

#: Pairing codes: 8 chars of A-Z2-7 (base32-ish, unambiguous), ~40 bits.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LEN = 8
#: A pairing offer stays redeemable this long.
PAIR_WINDOW = 600.0
#: Re-announce / re-publish at most this often without changes.
HELLO_EVERY = 120.0
_CHUNK = 48 * 1024


def _b64(b: bytes) -> str:
    return base64.b64encode(b).decode("ascii")


def _unb64(s: str) -> bytes:
    return base64.b64decode(s.encode("ascii"))


def make_code() -> str:
    return "".join(secrets.choice(_CODE_ALPHABET) for _ in range(CODE_LEN))


def _code_key(code: str, salt: bytes) -> bytes:
    from nacl import pwhash
    return pwhash.argon2id.kdf(
        32, code.strip().upper().encode("ascii"), salt,
        opslimit=pwhash.argon2id.OPSLIMIT_INTERACTIVE,
        memlimit=pwhash.argon2id.MEMLIMIT_INTERACTIVE)


def _seal(key: bytes, payload: bytes) -> bytes:
    from nacl.secret import SecretBox
    return bytes(SecretBox(key).encrypt(payload))


def _open(key: bytes, blob: bytes) -> bytes | None:
    from nacl.secret import SecretBox
    try:
        return bytes(SecretBox(key).decrypt(blob))
    except Exception:
        return None   # forged / wrong key / corrupt: silently ignored


class SyncService:
    """Owns the account key, the relay connection, and the merge loop."""

    def __init__(self, state, stats_con, transport=None) -> None:
        self.state = state
        self.con = stats_con
        self.transport = transport
        self.connected = False
        self.status = ""            # one-line, for the Settings screen
        self.last_merge: dict | None = None

        self.device_id = state.setting("sync_device", "")
        if not self.device_id:
            self.device_id = uuid.uuid4().hex[:12]
            state.set_setting("sync_device", self.device_id)
        key_hex = state.setting("sync_key", "")
        self.key: bytes | None = bytes.fromhex(key_hex) if key_hex else None

        # pairing state
        self._pair_code: str | None = None
        self._pair_topic: str | None = None
        self._pair_deadline = 0.0
        self._join_waiting = False

        # inbox written by the network thread, drained by tick()
        self._inbox: list[tuple[str, bytes]] = []
        #: chunks[device] = {"meta": dict, "parts": {i: bytes}}
        self._chunks: dict[str, dict] = {}
        self._merged_digest: dict[str, str] = {}
        self._published_digest = ""
        self._last_hello = 0.0
        self._dirty = True          # publish ours on first opportunity

    # ------------------------------------------------------------------ #
    # Identity / linking
    # ------------------------------------------------------------------ #
    @property
    def linked(self) -> bool:
        return self.key is not None

    def account_id(self) -> str:
        assert self.key is not None
        return hashlib.sha256(b"kanjire-sync" + self.key).hexdigest()[:16]

    def create_account(self) -> None:
        """First device: mint the secret key locally."""
        if self.key is None:
            self.key = secrets.token_bytes(32)
            self.state.set_setting("sync_key", self.key.hex())

    def unlink(self) -> None:
        """Forget the account on THIS device (others keep theirs)."""
        self.close()
        self.key = None
        self.state.set_setting("sync_key", "")
        self.status = ""

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #
    def connect(self) -> str | None:
        if self.connected:
            return None
        if self.transport is None:
            self.transport = PahoTransport(
                config.BROKER_HOST, config.BROKER_PORT,
                client_id=f"kanjire-sync-{self.device_id}",
                keepalive=config.KEEPALIVE)
        self.transport.on_message(self._on_message)
        self.transport.on_connect(self._on_connect)
        err = self.transport.connect()
        if err:
            self.status = err
            return err
        self.connected = True
        return None

    def close(self) -> None:
        if self.transport is not None:
            try:
                self.transport.close()
            except Exception:
                pass
            self.transport = None
        self.connected = False

    def _on_connect(self) -> None:
        if self.key is not None:
            self.transport.subscribe(self._acct_topic("#"))
        if self._pair_topic:
            self.transport.subscribe(self._pair_topic)

    def _acct_topic(self, leaf: str) -> str:
        return f"{config.TOPIC_ROOT}/syncx/{self.account_id()}/{leaf}"

    def _on_message(self, topic: str, payload: bytes) -> None:
        self._inbox.append((topic, bytes(payload)))

    # ------------------------------------------------------------------ #
    # Pairing
    # ------------------------------------------------------------------ #
    def start_pairing(self) -> str:
        """Host side: create/ensure the account, offer it under a code."""
        self.create_account()
        self.connect()
        code = make_code()
        salt = secrets.token_bytes(16)
        sealed = _seal(_code_key(code, salt), self.key)
        digest = hashlib.sha256(code.encode("ascii")).hexdigest()[:16]
        topic = f"{config.TOPIC_ROOT}/pairx/{digest}"
        offer = json.dumps({"salt": _b64(salt), "box": _b64(sealed),
                            "ts": time.time()}).encode("utf-8")
        self.transport.publish(topic, offer, retain=True)
        self.transport.subscribe(self._acct_topic("#"))
        self._pair_code = code
        self._pair_topic = topic
        self._pair_deadline = time.monotonic() + PAIR_WINDOW
        return code

    def cancel_pairing(self) -> None:
        if self._pair_topic and self.transport is not None:
            try:  # clear the retained offer
                self.transport.publish(self._pair_topic, b"", retain=True)
            except Exception:
                pass
        self._pair_code = None
        self._pair_topic = None

    def join(self, code: str) -> str | None:
        """Joiner side: redeem *code*. Result arrives via tick()/status."""
        code = (code or "").strip().upper().replace(" ", "")
        if len(code) != CODE_LEN:
            return "bad code"
        err = self.connect()
        if err:
            return err
        digest = hashlib.sha256(code.encode("ascii")).hexdigest()[:16]
        self._pair_code = code
        self._pair_topic = f"{config.TOPIC_ROOT}/pairx/{digest}"
        self._join_waiting = True
        self._pair_deadline = time.monotonic() + 30.0
        self.transport.subscribe(self._pair_topic)
        return None

    def _try_redeem(self, payload: bytes) -> None:
        try:
            d = json.loads(payload.decode("utf-8"))
            key = _open(_code_key(self._pair_code, _unb64(d["salt"])),
                        _unb64(d["box"]))
        except Exception:
            key = None
        if key is None or len(key) != 32:
            self.status = "pairing failed (wrong code?)"
            self._join_waiting = False
            return
        self.key = key
        self.state.set_setting("sync_key", key.hex())
        self._join_waiting = False
        self._pair_code = None
        self.status = "linked!"
        self.transport.subscribe(self._acct_topic("#"))
        self._dirty = True

    # ------------------------------------------------------------------ #
    # The merge loop — UI thread only
    # ------------------------------------------------------------------ #
    def push_soon(self) -> None:
        """Something progress-y happened (game ended): sync when convenient."""
        self._dirty = True

    def tick(self) -> None:
        if not self.connected:
            return
        now = time.monotonic()
        # pairing expiry
        if self._pair_topic and now > self._pair_deadline:
            if self._join_waiting:
                self.status = "no device answered that code"
                self._join_waiting = False
            self.cancel_pairing()

        for topic, payload in self._drain():
            self._handle(topic, payload)

        if self.key is None:
            return
        if self._dirty or (now - self._last_hello) >= HELLO_EVERY:
            self._publish_snapshot(force=self._dirty)
            self._last_hello = now
            self._dirty = False

    def _drain(self) -> list[tuple[str, bytes]]:
        out, self._inbox = self._inbox, []
        return out

    def _handle(self, topic: str, payload: bytes) -> None:
        if self._pair_topic and topic == self._pair_topic:
            if self._join_waiting and payload:
                self._try_redeem(payload)
            return
        if self.key is None or not payload:
            return
        parts = topic.split("/")
        # …/syncx/<acct>/dev/<device>/snap/<i>
        if len(parts) < 3 or parts[-2] != "snap":
            return
        device = parts[-3]
        if device == self.device_id:
            return
        try:
            idx = int(parts[-1])
        except ValueError:
            return
        plain = _open(self.key, payload)
        if plain is None:
            return
        slot = self._chunks.setdefault(device, {"meta": None, "parts": {}})
        if idx == 0:
            try:
                slot["meta"] = json.loads(plain.decode("utf-8"))
                slot["parts"] = {}
            except ValueError:
                return
        else:
            slot["parts"][idx] = plain
        self._maybe_merge(device, slot)

    def _maybe_merge(self, device: str, slot: dict) -> None:
        meta = slot.get("meta")
        if not meta:
            return
        n = int(meta.get("n") or 0)
        if len(slot["parts"]) < n:
            return
        if self._merged_digest.get(device) == meta.get("digest"):
            return
        try:
            raw = b"".join(slot["parts"][i] for i in range(1, n + 1))
            snap = json.loads(gzip.decompress(raw).decode("utf-8"))
        except Exception:
            return
        from kanjire.data import syncmerge
        summary = syncmerge.merge_snapshot(self.con, self.state, snap)
        self._merged_digest[device] = meta.get("digest") or ""
        self.last_merge = {"device": device, "when": time.time(),
                           **summary}
        self.state.set_setting("sync_last", time.strftime("%Y-%m-%d %H:%M"))
        total = sum(summary.values())
        self.status = (f"merged {total} change(s) from another device"
                       if total else "in sync")
        # Our content may have advanced past theirs — republish if changed.
        self._publish_snapshot(force=False)

    def _publish_snapshot(self, *, force: bool) -> None:
        from kanjire.data import syncmerge
        snap = syncmerge.export_snapshot(self.con, self.state)
        dg = syncmerge.digest(snap)
        if not force and dg == self._published_digest:
            return
        raw = gzip.compress(json.dumps(
            snap, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
        parts = [raw[i:i + _CHUNK] for i in range(0, len(raw), _CHUNK)] or [b""]
        meta = json.dumps({"n": len(parts), "digest": dg,
                           "ts": time.time()}).encode("utf-8")
        base = self._acct_topic(f"dev/{self.device_id}/snap")
        self.transport.publish(f"{base}/0", _seal(self.key, meta),
                               retain=True)
        for i, part in enumerate(parts, start=1):
            self.transport.publish(f"{base}/{i}", _seal(self.key, part),
                                   retain=True)
        self._published_digest = dg
