import asyncio
import logging
import re
from collections.abc import AsyncIterator
import httpx
from config import OLLAMA_BASE_URL, OLLAMA_MODEL

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a friendly, conversational voice assistant. Your responses are spoken aloud.

Rules:
- Engage naturally with whatever the user wants to talk about.
- Ask thoughtful follow-up questions to keep the conversation going.
- If the user's words are unclear, make your best guess at their meaning and respond helpfully.
- Never say "I don't understand" or "Could you clarify" — always try to contribute something useful.
- Respond in plain sentences. No markdown, no bullet points, no code blocks, no emojis.
- Keep responses concise — 1 to 3 sentences.
- When citing documents, mention the source naturally (e.g. "On page three...").
"""

MAX_RETRIES = 2
RETRY_DELAY = 1.0
_RETRYABLE_STATUS = {500, 502, 503}

# Regex to strip <think>...</think> blocks from qwen3 output
_THINK_PATTERN = re.compile(r"<think>.*?</think>", re.DOTALL)


def _strip_think_tags(text: str) -> str:
    """Remove <think>...</think> blocks from LLM output."""
    return _THINK_PATTERN.sub("", text).strip()


class LLMService:
    def __init__(self):
        self.base_url = OLLAMA_BASE_URL
        self.model = OLLAMA_MODEL
        self._client: httpx.AsyncClient | None = None

    async def initialize(self):
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=120.0)
        logger.info("LLM client initialized: model=%s url=%s", self.model, self.base_url)

    async def close(self):
        if self._client:
            await self._client.aclose()

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("LLM client not initialized. Call initialize() first.")
        return self._client

    async def generate(
        self,
        user_message: str,
        context: str = "",
        conversation_history: list[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> str:
        """Generate a complete response (non-streaming) with retry on transient errors."""
        messages = self._build_messages(user_message, context, conversation_history)
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": False,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                response = await self.client.post("/api/chat", json=payload)
                response.raise_for_status()
                data = response.json()
                return _strip_think_tags(data["message"]["content"])
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in _RETRYABLE_STATUS or attempt == MAX_RETRIES:
                    raise
                last_error = e
                logger.warning("Ollama returned %d, retrying (%d/%d)...",
                               e.response.status_code, attempt + 1, MAX_RETRIES)
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
            except (httpx.ConnectError, httpx.ReadError) as e:
                if attempt == MAX_RETRIES:
                    raise
                last_error = e
                logger.warning("Ollama connection error, retrying (%d/%d): %s",
                               attempt + 1, MAX_RETRIES, e)
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
        raise last_error  # type: ignore[misc]

    async def generate_stream(
        self,
        user_message: str,
        context: str = "",
        conversation_history: list[dict] | None = None,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> AsyncIterator[str]:
        """Stream response tokens with retry on transient connection errors."""
        import json as json_mod
        messages = self._build_messages(user_message, context, conversation_history)
        payload = {
            "model": model or self.model,
            "messages": messages,
            "stream": True,
            "think": False,
            "options": {
                "temperature": temperature,
                "num_predict": max_tokens,
            },
        }
        last_error: Exception | None = None
        for attempt in range(MAX_RETRIES + 1):
            try:
                async with self.client.stream(
                    "POST", "/api/chat", json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line.strip():
                            chunk = json_mod.loads(line)
                            if "message" in chunk and "content" in chunk["message"]:
                                yield chunk["message"]["content"]
                    return  # streaming completed successfully
            except httpx.HTTPStatusError as e:
                if e.response.status_code not in _RETRYABLE_STATUS or attempt == MAX_RETRIES:
                    raise
                last_error = e
                logger.warning("Ollama stream returned %d, retrying (%d/%d)...",
                               e.response.status_code, attempt + 1, MAX_RETRIES)
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
            except (httpx.ConnectError, httpx.ReadError) as e:
                if attempt == MAX_RETRIES:
                    raise
                last_error = e
                logger.warning("Ollama stream connection error, retrying (%d/%d): %s",
                               attempt + 1, MAX_RETRIES, e)
                await asyncio.sleep(RETRY_DELAY * (attempt + 1))
        raise last_error  # type: ignore[misc]

    async def unload_from_gpu(self):
        """Tell Ollama to immediately unload the model from GPU (keep_alive=0)."""
        try:
            await self.client.post(
                "/api/generate",
                json={"model": self.model, "keep_alive": 0},
                timeout=10.0,
            )
            logger.info("Ollama model unloaded from GPU")
        except Exception as e:
            logger.warning("Failed to unload Ollama model: %s", e)

    async def list_models(self) -> list[dict]:
        """List available Ollama models."""
        response = await self.client.get("/api/tags")
        response.raise_for_status()
        data = response.json()
        return [
            {
                "name": m["name"],
                "size": m.get("size", 0),
                "modified_at": m.get("modified_at", ""),
            }
            for m in data.get("models", [])
        ]

    def _build_messages(
        self,
        user_message: str,
        context: str = "",
        conversation_history: list[dict] | None = None,
    ) -> list[dict]:
        messages = [{"role": "system", "content": SYSTEM_PROMPT}]

        if conversation_history:
            messages.extend(conversation_history)

        if context:
            user_content = (
                f"Use the following document text to answer my question.\n\n"
                f"Document text:\n---\n{context}\n---\n\n"
                f"My question: {user_message}"
            )
        else:
            user_content = user_message

        messages.append({"role": "user", "content": user_content})
        return messages


llm_service = LLMService()
