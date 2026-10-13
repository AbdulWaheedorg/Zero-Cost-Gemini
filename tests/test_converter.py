import json

from src.converter import chat_completion, error_body, format_openai_chunk, usage_block


def test_usage_block_maps_agy_usage():
    usage = usage_block({"input_tokens": 100, "output_tokens": 20, "thinking_tokens": 5,
                         "cache_read_tokens": 40, "total_tokens": 125})
    assert usage == {
        "prompt_tokens": 100,
        "completion_tokens": 25,
        "total_tokens": 125,
        "completion_tokens_details": {"reasoning_tokens": 5},
        "prompt_tokens_details": {"cached_tokens": 40},
    }
    assert usage_block(None) == {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}


def test_chat_completion_shape():
    body = chat_completion("chatcmpl-1", "m", "Paris.", {"input_tokens": 5, "output_tokens": 2})
    assert body["id"] == "chatcmpl-1"
    assert body["choices"][0]["message"] == {"role": "assistant", "content": "Paris."}
    assert body["usage"]["total_tokens"] == 7


def test_format_openai_chunk():
    chunk = format_openai_chunk({"content": "Hello"}, None, "m", "c1", 1700000000)
    assert chunk.startswith(b"data: {") and chunk.endswith(b"\n\n")
    data = json.loads(chunk[len(b"data: "):])
    assert data["choices"] == [{"index": 0, "delta": {"content": "Hello"}, "finish_reason": None}]


def test_usage_only_chunk_has_no_choices():
    data = json.loads(format_openai_chunk(None, None, "m", "c1", 0, {"total_tokens": 1})[6:])
    assert data["choices"] == [] and data["usage"] == {"total_tokens": 1}


def test_error_body():
    assert error_body("bad", "invalid_request_error", param="model") == {
        "error": {"message": "bad", "type": "invalid_request_error", "param": "model", "code": None}
    }
