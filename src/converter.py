"""Builders for OpenAI-shaped response bodies and SSE frames."""
import time
import uuid
from typing import Any, Dict, Optional

import orjson


def new_completion_id() -> str:
    return f"chatcmpl-{uuid.uuid4().hex[:24]}"


def usage_block(usage: Optional[Dict[str, Any]]) -> Dict[str, int]:
    """Maps agy usage ({input,output,thinking,cache_read,total}_tokens) to OpenAI usage."""
    usage = usage or {}
    prompt = int(usage.get("input_tokens", 0))
    completion = int(usage.get("output_tokens", 0)) + int(usage.get("thinking_tokens", 0))
    block: Dict[str, Any] = {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "total_tokens": int(usage.get("total_tokens", prompt + completion)),
    }
    if usage.get("thinking_tokens"):
        block["completion_tokens_details"] = {"reasoning_tokens": int(usage["thinking_tokens"])}
    if usage.get("cache_read_tokens"):
        block["prompt_tokens_details"] = {"cached_tokens": int(usage["cache_read_tokens"])}
    return block


def chat_completion(
    completion_id: str,
    model: str,
    content: str,
    usage: Optional[Dict[str, Any]],
    finish_reason: str = "stop",
    created: Optional[int] = None,
) -> Dict[str, Any]:
    return {
        "id": completion_id,
        "object": "chat.completion",
        "created": created or int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": finish_reason,
            }
        ],
        "usage": usage_block(usage),
    }


def sse(payload: Any) -> bytes:
    return b"data: " + orjson.dumps(payload) + b"\n\n"


SSE_DONE = b"data: [DONE]\n\n"


def format_openai_chunk(
    delta: Optional[Dict[str, Any]],
    finish_reason: Optional[str],
    model: str,
    completion_id: str,
    created_ts: int,
    usage: Optional[Dict[str, Any]] = None,
) -> bytes:
    chunk: Dict[str, Any] = {
        "id": completion_id,
        "object": "chat.completion.chunk",
        "created": created_ts,
        "model": model,
        # delta=None produces the trailing usage-only chunk, which has no choices.
        "choices": [] if delta is None else [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }
    if usage is not None:
        chunk["usage"] = usage
    return sse(chunk)


def error_body(message: str, err_type: str, code: Optional[str] = None, param: Optional[str] = None) -> Dict[str, Any]:
    """OpenAI error envelope: {"error": {"message", "type", "param", "code"}}."""
    return {"error": {"message": message, "type": err_type, "param": param, "code": code}}
