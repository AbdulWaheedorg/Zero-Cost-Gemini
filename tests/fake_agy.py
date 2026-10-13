"""
Stand-in for the `agy` binary used by the test suite.

Behaviour is read from $HOME/scenario.json (HOME is the account directory the
proxy launches us with), and every invocation is appended to $HOME/calls.jsonl.

scenario keys:
  quota:      {"gemini_5h": f, "gemini_weekly": f, "tp_5h": f, "tp_weekly": f}
  quota_fail: true -> `/quota` exits 1
  quota_hang: true -> `/quota` never returns
  chat:       ok | quota_error | model_error | hang | hang_after_first |
              stderr_flood | no_result | empty | error_after_first | slow |
              overloaded | concurrency | hang_with_child
  reply:      text to stream back (default "Hello from <account dir name>")
  stderr:     text written to stderr before a normal reply
"""
import json
import os
import sys
import time

HOME = os.environ.get("HOME", "")


def scenario():
    try:
        with open(os.path.join(HOME, "scenario.json"), encoding="utf-8") as f:
            return json.load(f)
    except OSError:
        return {}


def record(entry):
    if HOME and os.path.isdir(HOME):
        with open(os.path.join(HOME, "calls.jsonl"), "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def emit(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def result(status="SUCCESS", response="", error=None, usage=None):
    r = {"status": status, "response": response,
         "usage": usage or {"input_tokens": 10, "output_tokens": 3, "thinking_tokens": 0,
                            "cache_read_tokens": 0, "total_tokens": 13}}
    if error:
        r["error"] = error
    emit({"event": "result", "result": r})


def bucket(window, frac):
    return {"id": window, "window": window, "remaining_fraction": frac,
            "reset_time": "2026-10-20T00:00:00Z", "description": ""}


def do_quota(argv, sc):
    record({"kind": "quota", "argv": argv})
    if sc.get("quota_hang"):
        time.sleep(3600)
    if sc.get("quota_fail"):
        sys.stderr.write("auth failed\n")
        sys.exit(1)
    q = {"gemini_5h": 1.0, "gemini_weekly": 1.0, "tp_5h": 1.0, "tp_weekly": 1.0, **sc.get("quota", {})}
    print(json.dumps({"status": "SUCCESS", "command": {"name": "usage", "data": {"groups": [
        {"name": "Gemini Models", "buckets": [bucket("5h", q["gemini_5h"]), bucket("weekly", q["gemini_weekly"])]},
        {"name": "Claude and GPT models", "buckets": [bucket("5h", q["tp_5h"]), bucket("weekly", q["tp_weekly"])]},
    ]}}}))


def do_chat(argv, sc):
    line = sys.stdin.readline()
    msg = json.loads(line)
    prompt = msg["message"]["content"]
    record({"kind": "chat", "argv": argv, "cwd": os.getcwd(), "prompt_len": len(prompt), "prompt_head": prompt[:2000],
            "pid": os.getpid()})

    model = next((a.split("=", 1)[1] for a in argv if a.startswith("--model=")), "")
    mode = sc.get("chat", "ok")
    reply = sc.get("reply") or f"Hello from {os.path.basename(HOME.rstrip(os.sep))}"
    if "ECHO_LEN" in prompt:
        reply = f"len={len(prompt)}"

    emit({"event": "init", "init": {"model": model}})

    effort = next((a.split("=", 1)[1] for a in argv if a.startswith("--effort=")), "")
    if effort and model.endswith(("-low", "-medium", "-high")) and not model.endswith("-" + effort):
        result("ERROR", error=f'invalid model selection (--model "{model}" --effort "{effort}"): '
                              f'--model {model} conflicts with --effort={effort}')
        return
    if model == "no-such-model":
        result("ERROR", error=f'invalid model selection (--model "{model}"): model {model} is not recognized as a known model')
        return
    if mode == "quota_error":
        result("ERROR", error="RESOURCE_EXHAUSTED: 429 You have exhausted your capacity on this model.")
        return
    if mode == "overloaded":
        result("ERROR", error="Our servers are experiencing high traffic right now, please try again in a minute.")
        return
    if mode == "concurrency":
        result("ERROR", error="Limit of concurrent requests reached (4), try again later.")
        return
    if mode == "model_error":
        result("ERROR", error="Something broke upstream")
        return
    if mode == "hang_with_child":
        # Like real agy, which spawns helpers (cmd.exe on Windows): the child must die too.
        import subprocess
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(3600)"])
        record({"kind": "child", "pid": child.pid})
        time.sleep(3600)
    if mode == "hang":
        time.sleep(3600)
    if mode == "stderr_flood":
        sys.stderr.write("x" * (2 * 1024 * 1024))
        sys.stderr.flush()
    if mode == "no_result":
        sys.stderr.write("panic: segfault\n")
        sys.exit(2)
    if mode == "empty":
        sys.stderr.write('jetski: no output produced — a tool required the "command" permission\n')
        result("SUCCESS", response="")
        return

    if sc.get("stderr"):
        sys.stderr.write(sc["stderr"] + "\n")
        sys.stderr.flush()

    words = reply.split(" ")
    for i, w in enumerate(words):
        emit({"event": "step_update", "step_update": {"text_delta": w + (" " if i < len(words) - 1 else "")}})
        if mode == "hang_after_first":
            time.sleep(3600)
        if mode == "error_after_first":
            result("ERROR", error="Stream interrupted: internal error")
            return
        if mode == "slow":
            time.sleep(0.4)
    result("SUCCESS", response=reply)


def main():
    argv = sys.argv[1:]
    sc = scenario()
    if argv[:1] == ["models"]:
        record({"kind": "models", "argv": argv})
        print("Fetching available models...")
        print("gemini-3.8-flash-low\tGemini 3.8 Flash (Low)")
        print("gemini-3.8-flash-high\tGemini 3.8 Flash (High)")
        print("claude-sonnet-4-6\tClaude Sonnet 4.6 (Thinking)")
        return
    if "/quota" in argv:
        do_quota(argv, sc)
        return
    do_chat(argv, sc)


if __name__ == "__main__":
    main()
