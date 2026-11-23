"""Проверяем цикл agent._run_turn на фейковом OpenAI-клиенте."""
from types import SimpleNamespace

from game_memory_agent.agent import _run_turn, _assistant_payload


class FakeSession:
    def status(self):
        return {"attached": False, "pid": None, "name": None, "candidates": 0, "frozen": []}


def _message(content=None, tool_calls=None):
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _call(name, arguments, call_id="call_1"):
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=arguments),
    )


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

        client = self

        class Completions:
            def create(self, **kwargs):
                client.requests.append(kwargs)
                return SimpleNamespace(choices=[SimpleNamespace(message=client.responses.pop(0))])

        self.chat = SimpleNamespace(completions=Completions())


def test_turn_with_tool_call():
    session = FakeSession()
    client = FakeClient(
        [
            _message(tool_calls=[_call("status", "{}")]),
            _message(content="Состояние: не подключены."),
        ]
    )
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "статус"}]

    _run_turn(client, "test-model", messages, session)

    assert len(client.requests) == 2
    assert client.requests[0]["tools"], "инструменты должны передаваться в запрос"
    # assistant с tool_calls, затем результат инструмента
    assert messages[2]["tool_calls"][0]["function"]["name"] == "status"
    assert messages[3]["role"] == "tool"
    assert '"attached": false' in messages[3]["content"]
    assert messages[4]["content"] == "Состояние: не подключены."


def test_turn_without_tools():
    session = FakeSession()
    client = FakeClient([_message(content="Привет!")])
    messages = [{"role": "system", "content": "sys"}, {"role": "user", "content": "hi"}]

    _run_turn(client, "test-model", messages, session)

    assert messages[-1] == {"role": "assistant", "content": "Привет!"}


def test_assistant_payload_shape():
    message = _message(
        content="",
        tool_calls=[_call("scan", '{"dtype": "i32", "value": 1}')],
    )
    payload = _assistant_payload(message)
    assert payload["role"] == "assistant"
    assert payload["tool_calls"][0]["type"] == "function"
    assert payload["tool_calls"][0]["function"]["name"] == "scan"
