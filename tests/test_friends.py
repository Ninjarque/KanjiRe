"""Friends: presence, invites, join requests - driven through the in-process
broker, so the whole thing runs with no network at all."""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from kanjire.net import config
from kanjire.net.friends import LOBBY, OFFLINE, ONLINE, FriendService
from kanjire.net.transport import LoopbackBroker, LoopbackTransport
from kanjire.userstate import UserState


def _state(tmp_path, name, code):
    st = UserState(path=tmp_path / f"{name}.json")
    st.data.setdefault("settings", {})["mp_name"] = name
    st.data["settings"]["friend_code"] = code
    st.save()
    return st


def _svc(broker, tmp_path, name, code):
    svc = FriendService(_state(tmp_path, name, code),
                        transport=LoopbackTransport(broker))
    return svc


def test_friend_code_is_minted_once_and_kept(tmp_path):
    st = UserState(path=tmp_path / "u.json")
    code = st.friend_code
    assert len(code) == 8 and code.isalnum() and code.isupper()
    assert st.friend_code == code, "the code must not change between calls"
    # ...and it survives a restart, or friends would lose you every launch.
    assert UserState(path=tmp_path / "u.json").friend_code == code


def test_you_see_a_friend_come_online_and_enter_a_room(tmp_path):
    broker = LoopbackBroker()
    me = _svc(broker, tmp_path, "me", "AAAA1111")
    them = _svc(broker, tmp_path, "ken", "BBBB2222")
    me.state.add_friend("BBBB2222", "ken")

    assert me.connect() is None
    me.tick(100.0)
    assert me.friends()[0]["status"] == OFFLINE, "not online until they say so"

    assert them.connect() is None            # publishes retained presence
    me.tick(101.0)
    f = me.friends()[0]
    assert f["status"] == ONLINE and f["name"] == "ken"

    them.set_status(LOBBY, "ABCDE")
    me.tick(102.0)
    f = me.friends()[0]
    assert f["status"] == LOBBY and f["room"] == "ABCDE", \
        "you can't ask to join a room you can't see"


def test_a_friend_who_quits_stops_showing_as_online(tmp_path):
    broker = LoopbackBroker()
    me = _svc(broker, tmp_path, "me", "AAAA1111")
    them = _svc(broker, tmp_path, "ken", "BBBB2222")
    me.state.add_friend("BBBB2222", "ken")
    me.connect(); them.connect()
    me.tick(100.0)
    assert me.friends()[0]["status"] == ONLINE

    them.close()                     # clean quit: clears its retained presence
    me.tick(101.0)
    assert me.friends()[0]["status"] == OFFLINE


def test_a_friend_who_crashes_stops_showing_as_online(tmp_path):
    """The will is what saves us here: no goodbye, but the broker publishes the
    empty retained presence on their behalf. Without a *retained* will their
    'online' flag would sit on the broker forever."""
    broker = LoopbackBroker()
    me = _svc(broker, tmp_path, "me", "AAAA1111")
    them = _svc(broker, tmp_path, "ken", "BBBB2222")
    me.state.add_friend("BBBB2222", "ken")
    me.connect(); them.connect()
    me.tick(100.0)
    assert me.friends()[0]["status"] == ONLINE

    broker.disconnect(them.transport)      # killed app
    me.tick(101.0)
    assert me.friends()[0]["status"] == OFFLINE
    assert config.TOPIC_ROOT + "/user/BBBB2222/presence" not in broker.retained


def test_stale_presence_is_not_trusted_forever(tmp_path):
    broker = LoopbackBroker()
    me = _svc(broker, tmp_path, "me", "AAAA1111")
    them = _svc(broker, tmp_path, "ken", "BBBB2222")
    me.state.add_friend("BBBB2222", "ken")
    me.connect(); them.connect()
    me.tick(100.0)
    assert me.friends()[0]["status"] == ONLINE
    # Retained presence outlives the client, so a snapshot we haven't heard
    # re-confirmed in a long time must not keep reading as "online".
    me.tick(100.0 + 10_000)
    assert me.friends()[0]["status"] == OFFLINE


def test_invite_and_join_request_reach_the_other_player(tmp_path):
    broker = LoopbackBroker()
    me = _svc(broker, tmp_path, "me", "AAAA1111")
    them = _svc(broker, tmp_path, "ken", "BBBB2222")
    me.state.add_friend("BBBB2222", "ken")
    them.state.add_friend("AAAA1111", "me")
    me.connect(); them.connect()

    me.invite("BBBB2222", "ABCDE")
    got = them.poll()
    assert len(got) == 1
    assert got[0]["type"] == "invite" and got[0]["room"] == "ABCDE"
    assert got[0]["from"] == "AAAA1111" and got[0]["name"] == "me"

    them.ask_to_join("AAAA1111")
    got = me.poll()
    assert len(got) == 1 and got[0]["type"] == "join_request"
    assert got[0]["from"] == "BBBB2222"


def test_friendship_needs_both_sides_to_agree(tmp_path):
    broker = LoopbackBroker()
    me = _svc(broker, tmp_path, "me", "AAAA1111")
    them = _svc(broker, tmp_path, "ken", "BBBB2222")
    me.connect(); them.connect()

    # I ask. Nobody is anybody's friend yet - it's a request, not an add.
    assert me.send_friend_request("BBBB2222", "ken") is True
    assert me.state.friends == [], "adding must not be one-sided any more"
    assert me.state.requested("BBBB2222")
    assert me.send_friend_request("BBBB2222", "ken") is False, "asked twice"

    got = [m for m in them.poll() if m["type"] == "friend_request"]
    assert got and got[0]["from"] == "AAAA1111" and got[0]["name"] == "me"
    assert them.state.friends == [], "a request must not add them either"

    # They accept: now BOTH sides have each other.
    them.accept_request("AAAA1111", "me")
    assert them.state.is_friend("AAAA1111")
    answer = [m for m in me.poll() if m["type"] == "friend_accept"]
    assert answer, "the answer never came back"
    assert me.state.is_friend("BBBB2222"), "accepting only added one side"
    assert not me.state.requested("BBBB2222"), "still listed as pending"


def test_a_declined_request_adds_nobody(tmp_path):
    broker = LoopbackBroker()
    me = _svc(broker, tmp_path, "me", "AAAA1111")
    them = _svc(broker, tmp_path, "ken", "BBBB2222")
    me.connect(); them.connect()

    me.send_friend_request("BBBB2222", "ken")
    them.poll()
    them.decline_request("AAAA1111")
    assert them.state.friends == []
    got = [m for m in me.poll() if m["type"] == "friend_decline"]
    assert got
    assert me.state.friends == []
    assert not me.state.requested("BBBB2222"), "a declined request must clear"
    # ...and the retained request is gone, so it can't haunt them next launch.
    assert not [t for t in broker.retained if t.endswith("/req/AAAA1111")]


def test_a_request_waits_for_someone_who_is_offline(tmp_path):
    """Requests are retained: you can add someone who has already gone to bed."""
    broker = LoopbackBroker()
    me = _svc(broker, tmp_path, "me", "AAAA1111")
    me.connect()
    me.send_friend_request("BBBB2222", "ken")      # they aren't even connected

    them = _svc(broker, tmp_path, "ken", "BBBB2222")
    them.connect()                                 # ...they open the app later
    got = [m for m in them.poll() if m["type"] == "friend_request"]
    assert got and got[0]["from"] == "AAAA1111", \
        "the request evaporated because they were offline when it was sent"


def test_an_answer_to_a_request_we_never_sent_is_ignored(tmp_path):
    broker = LoopbackBroker()
    me = _svc(broker, tmp_path, "me", "AAAA1111")
    liar = _svc(broker, tmp_path, "liar", "CCCC3333")
    me.connect(); liar.connect()

    liar._send("AAAA1111", {"type": "friend_accept"})   # nobody asked them
    assert me.poll() == []
    assert me.state.friends == [], "a forged acceptance added a stranger"


def test_a_stranger_cannot_message_you(tmp_path):
    """Knowing someone's code must not be enough to spam them with invites."""
    broker = LoopbackBroker()
    me = _svc(broker, tmp_path, "me", "AAAA1111")
    stranger = _svc(broker, tmp_path, "rando", "CCCC3333")
    me.connect(); stranger.connect()          # me has NOT added them

    stranger.invite("AAAA1111", "ABCDE")
    assert me.poll() == [], "an invite from a non-friend got through"


def test_removing_a_friend_forgets_them(tmp_path):
    broker = LoopbackBroker()
    me = _svc(broker, tmp_path, "me", "AAAA1111")
    them = _svc(broker, tmp_path, "ken", "BBBB2222")
    me.state.add_friend("BBBB2222", "ken")
    me.connect(); them.connect()
    me.tick(100.0)
    assert len(me.friends()) == 1

    assert me.remove_friend("BBBB2222")
    assert me.friends() == []
    # ...and they can no longer reach us.
    them.state.add_friend("AAAA1111", "me")
    them.invite("AAAA1111", "ZZZZZ")
    assert me.poll() == []


def test_you_cannot_add_yourself(tmp_path):
    st = _state(tmp_path, "me", "AAAA1111")
    assert st.add_friend("AAAA1111", "me") is False
    assert st.friends == []
