"""Minimal Meta Model API client (OpenAI-compatible chat completions).

Auth: reads META_MODEL_API_KEY from the environment. A key provider can be
injected with set_key_provider() for hosted environments that broker keys.
Stdlib only.
"""
import json
import os
import urllib.request

API_URL = "https://api.meta.ai/v1/chat/completions"
MODEL = "muse-spark-1.3"

_key_provider = None


def set_key_provider(fn):
    global _key_provider
    _key_provider = fn


def _api_key():
    if _key_provider is not None:
        return _key_provider()
    key = os.environ.get("META_MODEL_API_KEY", "").strip()
    if not key:
        raise RuntimeError("META_MODEL_API_KEY is not set")
    return key


def chat(messages, max_tokens=4096, model=MODEL, progress=None):
    """One chat completion. Returns dict with content, usage, reasoning_tokens.

    When progress is given, the call streams (SSE) and progress is invoked
    with {"chunks": n, "chars": n, "elapsed_s": float} as content arrives,
    so a UI can show live activity instead of a silent wait. The final chunk
    carries usage, including hidden reasoning token counts.
    """
    payload = {"model": model, "messages": messages, "max_tokens": max_tokens}
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {_api_key()}",
    }
    if progress is None:
        request = urllib.request.Request(
            API_URL, data=json.dumps(payload).encode(), method="POST", headers=headers
        )
        with urllib.request.urlopen(request, timeout=180) as response:
            body = json.loads(response.read().decode())
        choice = (body.get("choices") or [{}])[0]
        usage = body.get("usage") or {}
        content = (choice.get("message") or {}).get("content") or ""
        used_model = body.get("model", model)
    else:
        import time

        payload["stream"] = True
        request = urllib.request.Request(
            API_URL, data=json.dumps(payload).encode(), method="POST", headers=headers
        )
        start = time.time()
        parts, usage, chunks, used_model = [], {}, 0, model
        with urllib.request.urlopen(request, timeout=300) as response:
            for raw in response:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                used_model = chunk.get("model", used_model)
                if chunk.get("usage"):
                    usage = chunk["usage"]
                delta = ((chunk.get("choices") or [{}])[0].get("delta") or {})
                if delta.get("content"):
                    parts.append(delta["content"])
                chunks += 1
                progress(
                    {
                        "chunks": chunks,
                        "chars": sum(len(p) for p in parts),
                        "elapsed_s": round(time.time() - start, 1),
                    }
                )
        content = "".join(parts)
    details = usage.get("completion_tokens_details") or {}
    return {
        "content": content,
        "usage": usage,
        "reasoning_tokens": details.get("reasoning_tokens"),
        "model": used_model,
    }
