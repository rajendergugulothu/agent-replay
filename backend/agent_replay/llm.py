"""OpenAI client construction, plus recording and playback of responses.

Recording saves real API responses to disk. Playback serves them back with no
network, which is what offline mode and the recorded-response tests use.
"""

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from agent_replay.config import Settings


class OfflineMiss(RuntimeError):
    """A request was made offline that has no recorded response."""


def request_key(kwargs: dict[str, Any]) -> str:
    relevant = {k: kwargs.get(k) for k in ("model", "messages", "response_format")}
    return hashlib.sha256(json.dumps(relevant, sort_keys=True).encode()).hexdigest()


def response_to_dict(response: Any) -> dict[str, Any]:
    message = response.choices[0].message
    return {
        "content": message.content,
        "tool_calls": [
            {"id": tc.id, "name": tc.function.name, "arguments": tc.function.arguments}
            for tc in (message.tool_calls or [])
        ],
        "prompt_tokens": response.usage.prompt_tokens,
        "completion_tokens": response.usage.completion_tokens,
    }


def response_from_dict(data: dict[str, Any]) -> Any:
    """Rebuild an object with the same attributes the agent reads from the SDK."""
    tool_calls = [
        SimpleNamespace(
            id=tc["id"],
            type="function",
            function=SimpleNamespace(name=tc["name"], arguments=tc["arguments"]),
        )
        for tc in data["tool_calls"]
    ]
    message = SimpleNamespace(content=data["content"], tool_calls=tool_calls or None)
    usage = SimpleNamespace(prompt_tokens=data["prompt_tokens"], completion_tokens=data["completion_tokens"])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)], usage=usage)


class _Wrapper:
    """Gives a class the client.chat.completions.create shape of the SDK."""

    def __init__(self) -> None:
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def create(self, **kwargs: Any) -> Any:
        raise NotImplementedError


class RecordingClient(_Wrapper):
    def __init__(self, inner: Any, directory: Path) -> None:
        super().__init__()
        self.inner = inner
        self.directory = directory
        directory.mkdir(parents=True, exist_ok=True)

    def create(self, **kwargs: Any) -> Any:
        response = self.inner.chat.completions.create(**kwargs)
        path = self.directory / f"{request_key(kwargs)}.json"
        # The same request can get different answers, so keep every one in order.
        recorded = json.loads(path.read_text()) if path.exists() else []
        recorded.append(response_to_dict(response))
        path.write_text(json.dumps(recorded, indent=1))
        return response


class PlaybackClient(_Wrapper):
    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory
        self._served: dict[str, int] = {}

    def create(self, **kwargs: Any) -> Any:
        key = request_key(kwargs)
        path = self.directory / f"{key}.json"
        if not path.exists():
            raise OfflineMiss("This step has no recorded response, so it cannot run offline.")
        recorded = json.loads(path.read_text())
        index = self._served.get(key, 0)
        self._served[key] = index + 1
        return response_from_dict(recorded[index % len(recorded)])


def make_client(settings: Settings, record_to: Path | None = None) -> Any:
    if settings.offline:
        return PlaybackClient(settings.recordings_dir)
    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set.")
    from openai import OpenAI

    client: Any = OpenAI(api_key=settings.openai_api_key, max_retries=2, timeout=60)
    return RecordingClient(client, record_to) if record_to else client
