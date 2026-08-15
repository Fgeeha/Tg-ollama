"""Ollama API client for model interactions."""
import time
from collections.abc import AsyncGenerator
from typing import Any, Optional

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

    def __init__(self, base_url: str = "http://localhost:11434", timeout: int = 60):
        """Initialize Ollama client."""
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.client = httpx.AsyncClient(
            base_url=self.base_url,
            timeout=httpx.Timeout(timeout),
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
            response = await self.client.get("/api/tags")
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
                response = await self.client.get("/api/tags")
                response.raise_for_status()
                self._available_models = response.json().get("models", [])
                self._last_model_check = current_time
                logger.info(f"Found {len(self._available_models)} available models")
            except httpx.HTTPError as e:
                logger.error("Failed to list models", error=str(e))
                if not self._available_models:
                    raise OllamaError(f"Failed to list models: {e}")

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

    async def pull_model(self, model_name: str) -> AsyncGenerator[str, None]:
        """Pull a model from Ollama registry (yields progress updates)."""
        try:
            async with self.client.stream(
                "POST",
                "/api/pull",
                json={"name": model_name},
            ) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if line:
                        yield line
        except httpx.HTTPError as e:
            logger.error(f"Failed to pull model {model_name}", error=str(e))
            raise OllamaError(f"Failed to pull model: {e}")

    async def generate(
            self,
            model: str,
            prompt: str,
            context: Optional[list[int]] = None,
            **kwargs
    ) -> dict[str, Any]:
        """Непотоковый вызов: возвращает один JSON-результат."""
        if not await self.model_exists(model):
            raise OllamaModelNotFoundError(f"Model '{model}' not found")

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": False,
            **kwargs
        }
        if context:
            payload["context"] = context

        start_time = time.time()
        try:
            response = await self.client.post("/api/generate", json=payload)
            response.raise_for_status()
            result = response.json()
            result["response_time_ms"] = int((time.time() - start_time) * 1000)
            return result
        except httpx.HTTPError as e:
            logger.error("Failed to generate response", model=model, error=str(e))
            raise OllamaError(f"Failed to generate response: {e}")

    async def generate_stream(
            self,
            model: str,
            prompt: str,
            context: Optional[list[int]] = None,
            **kwargs
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Потоковый вызов: async generator."""
        if not await self.model_exists(model):
            raise OllamaModelNotFoundError(f"Model '{model}' not found")

        payload = {
            "model": model,
            "prompt": prompt,
            "stream": True,
            **kwargs
        }
        if context:
            payload["context"] = context

        start_time = time.time()
        async for chunk in self._generate_stream(payload, start_time):
            yield chunk

    async def _generate_stream(
        self,
        payload: dict[str, Any],
        start_time: float
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Stream generation response."""
        try:
            async with self.client.stream(
                "POST",
                "/api/generate",
                json=payload,
                timeout=httpx.Timeout(None),  # No timeout for streaming
            ) as response:
                response.raise_for_status()

                async for line in response.aiter_lines():
                    if line:
                        import json
                        data = json.loads(line)

                        # Add timing to final response
                        if data.get("done"):
                            data["response_time_ms"] = int((time.time() - start_time) * 1000)

                        yield data

        except httpx.HTTPError as e:
            logger.error("Stream generation failed", error=str(e))
            raise OllamaError(f"Stream generation failed: {e}")

    async def chat(
        self,
        model: str,
        messages: list[dict[str, str]],
        **kwargs
    ) -> dict[str, Any]:
        """Чат без стриминга: возвращает один JSON-результат."""
        if not await self.model_exists(model):
            raise OllamaModelNotFoundError(f"Model '{model}' not found")

        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
            **kwargs,
        }

        start_time = time.time()
        try:
            response = await self.client.post("/api/chat", json=payload)
            response.raise_for_status()
            result = response.json()
            result["response_time_ms"] = int((time.time() - start_time) * 1000)
            return result
        except httpx.HTTPError as e:
            logger.error("Failed to chat", model=model, error=str(e))
            raise OllamaError(f"Failed to chat: {e}")

    async def chat_stream(
        self,
        model: str,
        messages: list[dict[str, str]],
        **kwargs
    ) -> AsyncGenerator[dict[str, Any], None]:
        """Потоковый чат: async generator, ‘async for ... in client.chat_stream(...)’."""
        if not await self.model_exists(model):
            raise OllamaModelNotFoundError(f"Model '{model}' not found")

        payload = {
            "model": model,
            "messages": messages,
            "stream": True,
            **kwargs,
        }

        start_time = time.time()
        async for chunk in self._chat_stream(payload, start_time):
            yield chunk

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
            raise OllamaError(f"Stream chat failed: {e}")

    async def embeddings(self, model: str, prompt: str) -> list[float]:
        """Get embeddings for text."""
        try:
            response = await self.client.post(
                "/api/embeddings",
                json={"model": model, "prompt": prompt}
            )
            response.raise_for_status()
            return response.json()["embedding"]
        except httpx.HTTPError as e:
            logger.error("Failed to get embeddings", model=model, error=str(e))
            raise OllamaError(f"Failed to get embeddings: {e}")

    async def show_model_info(self, model: str) -> dict[str, Any]:
        """Get detailed information about a model."""
        try:
            response = await self.client.post(
                "/api/show",
                json={"name": model}
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPError as e:
            logger.error("Failed to get model info", model=model, error=str(e))
            raise OllamaError(f"Failed to get model info: {e}")

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
            response = await self.client.get("/")
            return response.status_code == 200
        except Exception:
            return False
