"""
LLM client for Memory OS worker tasks (reflection).

Select LLM_BACKEND explicitly for OpenRouter; Ollama remains the historical default.
"""
import os
import logging

import httpx

logger = logging.getLogger("cognitive-worker.llm")

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://host.docker.internal:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "deepseek-v4-flash:cloud")
OLLAMA_API_KEY = os.environ.get("OLLAMA_API_KEY", "")
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
OPENROUTER_MODEL = os.environ.get("OPENROUTER_MODEL", "deepseek/deepseek-v4.1-flash")
LLM_BACKEND = os.environ.get("LLM_BACKEND", "").strip().lower()


def get_auth_header() -> dict:
    if OLLAMA_API_KEY:
        return {"Authorization": f"Bearer {OLLAMA_API_KEY}"}
    return {}


def _use_openrouter() -> bool:
    if LLM_BACKEND not in {"", "ollama", "openrouter"}:
        raise ValueError("LLM_BACKEND must be ollama or openrouter")
    if LLM_BACKEND == "openrouter":
        if not OPENROUTER_API_KEY:
            raise ValueError("OpenRouter backend requires OPENROUTER_API_KEY")
        return True
    return False


async def _openrouter_chat(prompt: str, timeout: int, json_mode: bool = False) -> str:
    """OpenRouter chat completion.

    ``json_mode`` constrains the completion to a single JSON object. Without it
    the model may return its reasoning as prose, which the caller cannot parse
    (observed 2026-09-19: 19 of 30 chunks). If the provider rejects the
    parameter, retry once without it so an HTTP 400 never loses the call.
    """
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
    }
    payload = {
        "model": OPENROUTER_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.3,
        "max_tokens": 4096 if json_mode else 1024,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code == 400 and json_mode:
            logger.warning("OpenRouter rejected response_format (HTTP 400); retrying without it")
            payload.pop("response_format", None)
            resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    choices = data.get("choices") or []
    if not choices:
        return ""
    msg = choices[0].get("message") or {}
    content = msg.get("content") or ""
    if not content:
        content = msg.get("reasoning") or ""
    return content


async def ollama_chat(
    prompt: str,
    model: str | None = None,
    timeout: int = 120,
    json_mode: bool = False,
) -> str:
    """Return LLM text for reflection from the explicitly configured backend.

    ``json_mode`` is honoured on both paths: response_format on OpenRouter,
    format="json" on Ollama.
    """
    if _use_openrouter():
        return await _openrouter_chat(prompt, timeout, json_mode=json_mode)

    model = model or OLLAMA_MODEL
    url = f"{OLLAMA_BASE_URL.rstrip('/')}/api/generate"
    headers = {
        "Content-Type": "application/json",
        **get_auth_header(),
    }
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.7,
            "num_predict": 4096,
        },
    }
    if json_mode:
        payload["format"] = "json"
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, headers=headers, json=payload)
        if resp.status_code == 400 and json_mode:
            payload.pop("format", None)
            resp = await client.post(url, headers=headers, json=payload)
        resp.raise_for_status()
        data = resp.json()
    response = data.get("response", "")
    if not response:
        response = data.get("reasoning") or data.get("thinking") or ""
    return response
