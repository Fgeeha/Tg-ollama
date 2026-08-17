"""OLLAMA_API_STYLE=openai: talking to LiteLLM/OpenAI-compatible proxies instead of native Ollama."""
import pytest

from bot.utils.ollama import OllamaClient


class _FakeStream:
    """Minimal stand-in for httpx's streaming response context manager."""

    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


@pytest.fixture
async def openai_client():
    c = OllamaClient("http://localhost:4000", api_style="openai")
    c._available_models = [{"name": "m:latest", "size": 0}]
    yield c
    await c.close()


@pytest.mark.asyncio
async def test_openai_style_chat_stream_parses_sse_and_usage(openai_client, monkeypatch):
    """LiteLLM/OpenAI SSE deltas must map onto the same chunk shape chat.py expects."""
    lines = [
        'data: {"choices":[{"delta":{"content":"Hel"}}]}',
        'data: {"choices":[{"delta":{"content":"lo"}}]}',
        'data: {"choices":[],"usage":{"prompt_tokens":5,"completion_tokens":2}}',
        "data: [DONE]",
    ]
    monkeypatch.setattr(
        openai_client.client, "stream",
        lambda *a, **kw: _FakeStream(lines),
    )

    chunks = [
        c async for c in openai_client.chat_stream("m", [{"role": "user", "content": "hi"}])
    ]

    text = "".join(c["message"]["content"] for c in chunks if not c["done"])
    assert text == "Hello"
    final = chunks[-1]
    assert final["done"] is True
    assert final["prompt_eval_count"] == 5
    assert final["eval_count"] == 2


@pytest.mark.asyncio
async def test_openai_style_list_models_maps_id_to_name(monkeypatch):
    """OpenAI-style /v1/models has no size/family; only the id survives as "name"."""
    c = OllamaClient("http://localhost:4000", api_style="openai")

    class _Resp:
        def raise_for_status(self):
            pass

        def json(self):
            return {"data": [{"id": "gpt-4o-mini"}, {"id": "llava"}]}

    async def fake_get(path):
        assert path == "/v1/models"
        return _Resp()

    monkeypatch.setattr(c.client, "get", fake_get)
    models = await c.list_models()
    await c.close()

    assert [m["name"] for m in models] == ["gpt-4o-mini", "llava"]


def test_openai_style_messages_with_images_use_content_parts():
    """A flat "images" field must become OpenAI's content-parts list, not be dropped."""
    converted = OllamaClient._to_openai_messages([
        {"role": "user", "content": "what's this", "images": ["Zm9v"]},
    ])

    assert converted[0]["content"][0] == {"type": "text", "text": "what's this"}
    assert converted[0]["content"][1]["image_url"]["url"] == "data:image/jpeg;base64,Zm9v"
