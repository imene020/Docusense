"""LLM client with two interchangeable backends.

- "ollama": a self-hosted Ollama server (the private, on-prem deployment).
- "openai": any OpenAI-compatible API (Groq, Together, vLLM, LM Studio...),
  used for the free public demo where no GPU server is available.

Both return the same shape so the agent never knows which one is running.
"""
import asyncio
import time

import httpx

from app.core.config import get_settings

MAX_RETRIES = 3


async def _ollama(prompt: str, system: str | None, json_mode: bool) -> dict:
    s = get_settings()
    payload = {
        "model": s.ollama_model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.1, "num_ctx": 8192},
    }
    if system:
        payload["system"] = system
    if json_mode:
        payload["format"] = "json"
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(f"{s.ollama_base_url}/api/generate", json=payload)
        resp.raise_for_status()
        data = resp.json()
    return {
        "text": data.get("response", ""),
        "tokens": data.get("eval_count", 0),
        "duration_ms": data.get("eval_duration", 0) / 1e6,
    }


async def _openai_compatible(prompt: str, system: str | None, json_mode: bool) -> dict:
    s = get_settings()
    messages = ([{"role": "system", "content": system}] if system else []) + [
        {"role": "user", "content": prompt}
    ]
    payload = {"model": s.llm_model, "messages": messages, "temperature": 0.1}
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    t0 = time.perf_counter()
    async with httpx.AsyncClient(timeout=120) as client:
        for attempt in range(MAX_RETRIES):
            resp = await client.post(
                f"{s.llm_base_url.rstrip('/')}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {s.llm_api_key}"},
            )
            if resp.status_code != 429:  # free tiers rate-limit; back off and retry
                break
            wait = float(resp.headers.get("retry-after", 2 ** attempt))
            await asyncio.sleep(min(wait, 20))
        resp.raise_for_status()
        data = resp.json()
    return {
        "text": data["choices"][0]["message"]["content"] or "",
        "tokens": data.get("usage", {}).get("completion_tokens", 0),
        "duration_ms": (time.perf_counter() - t0) * 1000,
    }


async def generate(prompt: str, system: str | None = None, json_mode: bool = False) -> dict:
    """Returns {'text': str, 'tokens': int, 'duration_ms': float}."""
    if get_settings().llm_provider == "openai":
        return await _openai_compatible(prompt, system, json_mode)
    return await _ollama(prompt, system, json_mode)
