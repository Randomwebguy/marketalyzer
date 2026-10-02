import json
import stat
from itertools import count

import pytest

from marketalyzer.ai import conversations as conversations_module
from marketalyzer.ai.agent import run_turn
from marketalyzer.ai.conversations import (
    DEFAULT_TITLE,
    conversations_dir,
    delete_conversation,
    display_messages,
    list_conversations,
    load_conversation,
    new_conversation,
    save_conversation,
)


@pytest.fixture
def clock(monkeypatch):
    """Make every save one second later than the previous one."""
    seconds = count()
    monkeypatch.setattr(
        conversations_module,
        "_now",
        lambda: f"2026-01-01T10:00:{next(seconds):02d}+03:00",
    )


def chat(text):
    conversation = new_conversation()
    conversation["messages"].append({"role": "user", "content": text})
    return conversation


def test_new_conversation():
    conversation = new_conversation()
    assert len(conversation["id"]) == 32
    assert conversation["title"] == DEFAULT_TITLE
    assert conversation["created"] == conversation["updated"]
    assert conversation["messages"] == []
    assert conversation["usage"] == {
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "cost": 0.0,
    }
    assert new_conversation()["id"] != conversation["id"]


def test_round_trip(tmp_path, clock):
    conversation = chat("  Bugün   THYAO\nnasıl? ")
    created = conversation["created"]
    save_conversation(conversation)
    path = tmp_path / "home" / "ai" / "conversations" / f"{conversation['id']}.json"
    assert path.exists()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    loaded = load_conversation(conversation["id"])
    assert loaded == conversation
    assert loaded["title"] == "Bugün THYAO nasıl?"
    assert loaded["created"] == created
    assert loaded["updated"] > created


def test_title_is_shortened_and_kept():
    conversation = save_conversation(chat("a" * 100))
    assert conversation["title"] == "a" * 59 + "…"
    assert len(conversation["title"]) == 60
    conversation["messages"].append({"role": "user", "content": "başka"})
    assert save_conversation(conversation)["title"] == "a" * 59 + "…"
    renamed = chat("ilk mesaj")
    renamed["title"] = "Portföyüm"
    assert save_conversation(renamed)["title"] == "Portföyüm"
    assert save_conversation(new_conversation())["title"] == DEFAULT_TITLE


def test_title_from_text_parts():
    conversation = new_conversation()
    conversation["messages"].append(
        {"role": "user", "content": [{"type": "text", "text": "Parçalı mesaj"}]}
    )
    assert save_conversation(conversation)["title"] == "Parçalı mesaj"


def test_list_newest_first(clock):
    first = save_conversation(chat("birinci"))
    second = save_conversation(chat("ikinci"))
    first["messages"].append({"role": "assistant", "content": "yanıt"})
    save_conversation(first)
    listed = list_conversations()
    assert [item["id"] for item in listed] == [first["id"], second["id"]]
    assert listed[0] == {
        "id": first["id"],
        "title": "birinci",
        "updated": first["updated"],
        "messages": 2,
    }


def test_list_skips_unreadable_files():
    assert list_conversations() == []
    kept = save_conversation(chat("geçerli"))
    (conversations_dir() / f"{'0' * 32}.json").write_text("{broken")
    (conversations_dir() / "notes.json").write_text("{}")
    assert [item["id"] for item in list_conversations()] == [kept["id"]]


def test_delete():
    conversation = save_conversation(chat("silinecek"))
    delete_conversation(conversation["id"])
    with pytest.raises(KeyError):
        load_conversation(conversation["id"])
    with pytest.raises(KeyError):
        delete_conversation(conversation["id"])
    assert list_conversations() == []


@pytest.mark.parametrize(
    "bad",
    ["../settings", "../../etc/passwd", "A" * 32, "0" * 31, "0" * 32 + "\n", "", None],
)
def test_invalid_ids_are_rejected(tmp_path, bad):
    secret = tmp_path / "home" / "ai" / "settings.json"
    secret.parent.mkdir(parents=True)
    secret.write_text("{}")
    with pytest.raises(KeyError):
        load_conversation(bad)
    with pytest.raises(KeyError):
        delete_conversation(bad)
    with pytest.raises(KeyError):
        save_conversation({"id": bad, "messages": []})
    assert secret.exists()


def test_missing_conversation():
    with pytest.raises(KeyError):
        load_conversation("f" * 32)


STORED = [
    {"role": "system", "content": "gizli sistem mesajı"},
    {"role": "user", "content": "THYAO ve ASELS?"},
    {
        "role": "assistant",
        "content": "Bakıyorum.",
        "tool_calls": [
            {
                "id": "call_a",
                "type": "function",
                "function": {"name": "quote", "arguments": '{"symbol": "THYAO"}'},
            },
            {
                "id": "call_b",
                "type": "function",
                "function": {"name": "quote", "arguments": "not json"},
            },
            {
                "id": "call_c",
                "type": "function",
                "function": {"name": "quote", "arguments": "{}"},
            },
        ],
    },
    {"role": "tool", "tool_call_id": "call_a", "content": '{"last": 300.5}'},
    {"role": "tool", "tool_call_id": "call_b", "content": '{"error": "Geçersiz"}'},
    {"role": "tool", "tool_call_id": "unknown", "content": "{}"},
    {"role": "assistant", "content": None},
    {"role": "assistant", "content": "THYAO 300,5 TL."},
]


def test_display_messages():
    assert display_messages({"messages": STORED}) == [
        {"role": "user", "text": "THYAO ve ASELS?"},
        {"role": "assistant", "text": "Bakıyorum."},
        {
            "role": "tool",
            "id": "call_a",
            "name": "quote",
            "args": {"symbol": "THYAO"},
            "ok": True,
            "result": {"last": 300.5},
        },
        {
            "role": "tool",
            "id": "call_b",
            "name": "quote",
            "args": {},
            "ok": False,
            "result": "Geçersiz",
        },
        {
            "role": "tool",
            "id": "call_c",
            "name": "quote",
            "args": {},
            "ok": None,
            "result": None,
        },
        {"role": "assistant", "text": "THYAO 300,5 TL."},
    ]
    assert display_messages(new_conversation()) == []


def test_saved_turn_displays_like_the_live_events():
    class Tools:
        def specs(self):
            spec = {"name": "quote", "description": "", "parameters": {}}
            return [{"type": "function", "function": spec}]

        def call(self, name, args):
            return {"symbol": args["symbol"], "last": 300.5}

    steps = [
        [
            {
                "type": "tool_call",
                "index": 0,
                "id": "call_a",
                "name": "quote",
                "arguments": '{"symbol": "THYAO"}',
            },
            {"type": "finish", "reason": "tool_calls"},
        ],
        [{"type": "text", "text": "300,5 TL."}, {"type": "finish", "reason": "stop"}],
    ]
    conversation = chat("THYAO?")
    events = []
    result = run_turn(
        conversation["messages"],
        api_key="k",
        model="a/b",
        tools=Tools(),
        system_prompt="sistem",
        emit=events.append,
        stream=lambda *args: iter(steps.pop(0)),
    )
    conversation["messages"] += result.messages
    save_conversation(conversation)
    shown = display_messages(load_conversation(conversation["id"]))
    end = next(e for e in events if e["type"] == "tool_end")
    tool = next(item for item in shown if item["role"] == "tool")
    assert (tool["ok"], tool["result"]) == (end["ok"], end["result"])
    assert shown[-1] == {"role": "assistant", "text": "300,5 TL."}
    stored = json.loads(
        (conversations_dir() / f"{conversation['id']}.json").read_text()
    )
    assert "sistem" not in json.dumps(stored)
