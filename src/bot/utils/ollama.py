"""Ollama API client for model interactions."""
import time
from collections.abc import AsyncGenerator
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

logger = structlog.get_logger()


class OllamaError(Exception):
    """Base exception for Ollama client errors."""
    pass


class OllamaConnectionError(OllamaError):
    """Raised when connection to Ollama fails."""
    pass


class OllamaModelNotFoundError(OllamaError):
    """Raised when requested model is not found."""
    pass


class OllamaClient:
    """Client for interacting with Ollama API."""

    def __init__(
        self,
        base_url: str = "http://localhost:11434",
        timeout: int = 60,
        keep_alive: str | None = None,
        options: dict[str, Any] | None = None,
        api_key: str | None = None,
        api_style: str = "ollama",
    ):
        """Initialize Ollama client.

        Args:
            base_url: Ollama API base URL.
            timeout: Per-operation timeout, seconds.
            keep_alive: How long Ollama keeps the model in memory after a
                request; avoids reloading it before every answer. Ignored in
                "openai" api_style (no such concept in that API).
            options: Generation options passed through to Ollama
                (temperature, num_ctx, ...). In "openai" api_style only
                "temperature" has an equivalent; the rest are ignored.
            api_key: Bearer token sent as Authorization header, for LiteLLM
                or other Ollama-compatible endpoints that require auth.
            api_style: "ollama" for the native Ollama REST API, or "openai"
                for OpenAI-compatible proxies (e.g. LiteLLM) that don't
                implement Ollama's native /api/* routes.
        """
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.keep_alive = keep_alive
        self.options = options or {}
        self._openai = api_style == "openai"
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
            headers=headers,
        )
        self._available_models: list[dict[str, Any]] = []
        self._last_model_check = 0
        self._model_check_interval = 60  # Refresh model list every 60 seconds
        self._vision_capable_cache: dict[str, bool] = {}

    async def close(self) -> None:
        """Close the HTTP client."""
        await self.client.aclose()

    @retry(
        retry=retry_if_exception_type(httpx.TransportError),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
    )
    async def verify_connection(self) -> bool:
        """Verify connection to Ollama server."""
        try:
            response = await self.client.get("/v1/models" if self._openai else "/api/tags")
            response.raise_for_status()
            logger.info("Ollama connection verified", base_url=self.base_url)
            return True
        except httpx.HTTPError as e:
            logger.error("Failed to connect to Ollama", error=str(e))
            raise OllamaConnectionError(f"Cannot connect to Ollama at {self.base_url}") from e

    async def list_models(self, force_refresh: bool = False) -> list[dict[str, Any]]:
        """List available Ollama models."""
        current_time = time.time()

        # Check if we need to refresh the model list
        if (
            force_refresh
            or not self._available_models
            or current_time - self._last_model_check > self._model_check_interval
        ):
            try:
                response = await self.client.get("/v1/models" if self._openai else "/api/tags")
                response.raise_for_status()
                if self._openai:
                    # OpenAI-style listing carries no size/family info.
                    self._available_models = [
                        {"name": model["id"]}
                        for model in response.json().get("data", [])
                    ]
                else:
                    self._available_models = response.json().get("models", [])
                self._last_model_check = current_time
                logger.info(f"Found {len(self._available_models)} available models")
            except httpx.HTTPError as e:
                logger.error("Failed to list models", error=str(e))
                if not self._available_models:
                    raise OllamaError(f"Failed to list models: {e}") from e

        return self._available_models

    async def get_model_names(self) -> list[str]:
        """Get list of model names only."""
        models = await self.list_models()
        return [model["name"] for model in models]

    async def model_exists(self, model_name: str) -> bool:
        """Check if a model exists."""
        try:
            models = await self.get_model_names()
        except OllamaError:
            return False
        # Handle model names with and without tags (e.g., "llama2" and "llama2:latest")
        model_base = model_name.split(":")[0]
        return any(
            model == model_name or model.startswith(f"{model_base}:")
            for model in models
        )

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        **kwargs
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Потоковый чат: async generator, ‘async for ... in client.chat_stream(...)’."""
        if not await self.model_exists(model):
            raise OllamaModelNotFoundError(f"Model '{model}' not found")

        start_time = time.time()

        if self._openai:
            payload = {
                "model": model,
                "messages": self._to_openai_messages(messages),
                "stream": True,
                "stream_options": {"include_usage": True},
                **kwargs,
            }
            temperature = self.options.get("temperature")
            if temperature is not None:
                payload.setdefault("temperature", temperature)
            async for chunk in self._chat_stream_openai(payload, start_time):
                yield chunk
            return

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            **kwargs,
        }
        if self.keep_alive:
            payload.setdefault("keep_alive", self.keep_alive)
        if self.options:
            payload.setdefault("options", self.options)

        async for chunk in self._chat_stream(payload, start_time):
            yield chunk

    @staticmethod
    def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Convert Ollama-style messages (flat "images" field) to OpenAI content parts.

        ponytail: assumes image/jpeg regardless of actual format, since the
        caller never tracks a mime type; swap in real sniffing if a
        non-JPEG source starts producing broken image_url data URIs.
        """
        converted = []
        for message in messages:
            images = message.get("images")
            if not images:
                converted.append({"role": message["role"], "content": message.get("content", "")})
                continue
            content: list[dict[str, Any]] = []
            text = message.get("content", "")
            if text:
                content.append({"type": "text", "text": text})
            content.extend(
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{img}"}}
                for img in images
            )
            converted.append({"role": message["role"], "content": content})
        return converted

    async def _chat_stream_openai(
            self,
            payload: dict[str, Any],
            start_time: float
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream an OpenAI-compatible chat completion, adapted to the Ollama chunk shape."""
        import json

        try:
            async with self.client.stream(
                    "POST",
                    "/v1/chat/completions",
                    json=payload,
                    timeout=httpx.Timeout(self.timeout),
            ) as response:
                response.raise_for_status()

                usage: dict[str, Any] = {}
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data_str = line[len("data:"):].strip()
                    if data_str == "[DONE]":
                        break

                    data = json.loads(data_str)
                    if data.get("usage"):
                        usage = data["usage"]

                    choices = data.get("choices") or []
                    content = choices[0].get("delta", {}).get("content") if choices else None
                    if content:
                        yield {"message": {"content": content}, "done": False}

                yield {
                    "message": {"content": ""},
                    "done": True,
                    "response_time_ms": int((time.time() - start_time) * 1000),
                    "prompt_eval_count": usage.get("prompt_tokens", 0),
                    "eval_count": usage.get("completion_tokens", 0),
                }

        except httpx.HTTPError as e:
            logger.error("Stream chat failed", error=str(e))
            raise OllamaError(f"Stream chat failed: {e}") from e

    async def _chat_stream(
            self,
            payload: dict[str, Any],
            start_time: float
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream chat response."""
        try:
            async with self.client.stream(
                    "POST",
                    "/api/chat",
                    json=payload,
                    # Per-operation timeout, not a total one: a long generation keeps
                    # streaming chunks, while a hung Ollama trips the read timeout.
                    # timeout=None here meant OLLAMA_TIMEOUT never applied to streaming.
                    timeout=httpx.Timeout(self.timeout),
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if not line:
                        continue
                    import json
                    data = json.loads(line)

                    # Добавим тайминг в финальный чанк
                    if data.get("done"):
                        data["response_time_ms"] = int((time.time() - start_time) * 1000)

                    yield data

        except httpx.HTTPError as e:
            logger.error("Stream chat failed", error=str(e))
            raise OllamaError(f"Stream chat failed: {e}") from e

    async def show_model_info(self, model: str) -> dict[str, Any]:
        """Get detailed information about a model."""
        if self._openai:
            # No equivalent in the OpenAI API (no license/template/quantization).
            return {}
        try:
            response = await self.client.post(
                "/api/show",
                json={"name": model}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error("Failed to get model info", model=model, error=str(e))
            raise OllamaError(f"Failed to get model info: {e}") from e

    async def supports_images(self, model: str) -> bool:
        """Return True if the model can process images."""
        if model in self._vision_capable_cache:
            return self._vision_capable_cache[model]

        try:
            info = await self.show_model_info(model)
        except OllamaError:
            self._vision_capable_cache[model] = False
            return False

        capabilities = info.get("capabilities")
        if isinstance(capabilities, list):
            # Ollama reports this authoritatively; trust it and stop.
            supported = "vision" in capabilities
        else:
            # Older Ollama builds omit "capabilities" - sniff families and the name.
            details = info.get("details") or {}
            names = [details.get("family"), *(details.get("families") or []), model]
            markers = ("vision", "vlm", "llava", "clip", "multimodal")
            supported = any(
                marker in name.lower()
                for name in names
                if isinstance(name, str)
                for marker in markers
            )

        self._vision_capable_cache[model] = supported
        return supported

    async def health_check(self) -> bool:
        """Perform health check on Ollama server."""
        try:
            response = await self.client.get("/v1/models" if self._openai else "/")
            return response.status_code == 200
        except Exception:
            return False
