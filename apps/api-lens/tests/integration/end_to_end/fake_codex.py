"""Stand in for the Codex App Server, scripted by the prompt it receives."""



PROBE = """
import json
import os
import sys
import time
from pathlib import Path

thread_id = "c47f8d99-9af1-4219-bc65-a55a27096cb8"
assert sys.argv[1:4] == ["app-server", "--listen", "stdio://"]
assert 'default_permissions="agent-lens-catalog"' in sys.argv
filesystem = next(arg for arg in sys.argv if arg.startswith('permissions.agent-lens-catalog.filesystem='))
assert json.dumps(str(Path.cwd())) + ' = "write"' in filesystem
assert 'permissions.agent-lens-catalog.network.enabled=false' in sys.argv
assert 'shell_environment_policy.inherit="none"' in sys.argv


def request(method):
    msg = json.loads(sys.stdin.readline())
    assert msg["method"] == method
    return msg


def reply(msg, result):
    print(json.dumps({"id": msg["id"], "result": result}), flush=True)


def notify(method, **params):
    print(json.dumps({"method": method, "params": params}), flush=True)


reply(request("initialize"), {})
request("initialized")
login = request("account/login/start")
assert login["params"]["type"] == "apiKey"
assert 'cli_auth_credentials_store="ephemeral"' in sys.argv
api_key = login["params"]["apiKey"]
assert api_key == "not-a-real-key"
assert not {"CODEX_API_KEY", "OPENAI_API_KEY"} & os.environ.keys()
reply(login, {"type": "apiKey"})
opening = json.loads(sys.stdin.readline())
resuming = opening["method"] == "thread/resume"
assert opening["method"] in {"thread/start", "thread/resume"}
assert opening["params"]["permissions"] == "agent-lens-catalog"
assert opening["params"]["approvalPolicy"] == "never"
assert opening["params"]["cwd"] == str(Path.cwd())
if resuming:
    assert opening["params"]["threadId"] == thread_id
    assert opening["params"]["excludeTurns"]
    opening_state = Path(os.environ["CODEX_HOME"])
    if (opening_state / "reject-resume").exists():
        with (opening_state / "resume-attempts").open("a") as attempts:
            print("reject the continuation", file=attempts)
        print(json.dumps({"id": opening["id"], "error": {"code": -32600}}), flush=True)
        raise SystemExit(1)
if not resuming:
    counter = Path(os.environ["CODEX_HOME"]) / "thread-started"
    if counter.exists():
        thread_id = "55555555-5555-4555-8555-555555555555"
    counter.touch()
reply(opening, {
    "thread": {"id": thread_id}, "cwd": str(Path.cwd()), "approvalPolicy": "never",
    "activePermissionProfile": {"id": "agent-lens-catalog"},
})
turn = request("turn/start")
assert turn["params"]["threadId"] == thread_id
prompt = turn["params"]["input"][0]["text"]
if prompt == "measure total duration":
    time.sleep(0.3)
reply(turn, {"turn": {"id": "current-turn"}})


def emit(raw, *, flush):
    event = json.loads(raw)
    kind = event.pop("type")
    ids = {"threadId": thread_id, "turnId": "current-turn"}
    if kind == "turn.started":
        notify("turn/started", **ids, turn={"id": "current-turn"})
    elif kind in {"turn.completed", "turn.failed"}:
        status = "completed" if kind == "turn.completed" else "failed"
        notify("turn/completed", **ids, turn={"id": "current-turn", "status": status})
    elif kind.startswith("item."):
        def camel(key):
            first, *parts = key.split("_")
            return first + "".join(part.title() for part in parts)
        item = event["item"]
        fields = {camel(key): value for key, value in item.items()}
        fields["type"] = camel(item["type"])
        if fields.get("status") == "in_progress":
            fields["status"] = "inProgress"
        notify(kind.replace(".", "/"), **ids, item=fields)
    elif kind == "error":
        notify("error", **ids, error={"message": event["message"]}, willRetry=False)
    elif kind == "future.event":
        notify("item/completed", **ids, item={
            "type": "agentMessage", "id": f'item_{event["sequence"]}', "text": str(event["sequence"]),
        })


state = Path(os.environ["CODEX_HOME"])
marker_file = state / "session-marker"
if prompt in ("catalog first", "catalog second"):
    target = Path.cwd() / "catalog.txt"
    if prompt == "catalog first":
        target.write_text("category filtering\\n")
    else:
        assert target.read_text() == "category filtering\\n"
        target.write_text(target.read_text() + "pagination\\n")
    emit(json.dumps({"type": "thread.started", "thread_id": thread_id}), flush=True)
    emit(json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "Edit recorded"}}), flush=True)
    emit('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt == "independent conversation":
    emit(json.dumps({"type": "thread.started", "thread_id": "55555555-5555-4555-8555-555555555555"}), flush=True)
    emit('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt == "save marker: niobium":
    assert not resuming
    marker_file.write_text("niobium")
if prompt in ("recall the marker", "reject the continuation"):
    assert resuming
    with (state / "resume-attempts").open("a") as attempts:
        print(prompt, file=attempts)
    if prompt == "reject the continuation":
        raise SystemExit(7)
if prompt in ("save marker: niobium", "recall the marker"):
    emit(json.dumps({"type": "thread.started", "thread_id": thread_id}), flush=True)
    emit('{"type":"turn.started"}', flush=True)
    if resuming:
        for total, last in (
            ({"inputTokens": 110, "cachedInputTokens": 33, "outputTokens": 22},
             {"inputTokens": 10, "cachedInputTokens": 3, "outputTokens": 2}),
            ({"inputTokens": 125, "cachedInputTokens": 37, "outputTokens": 25},
             {"inputTokens": 15, "cachedInputTokens": 4, "outputTokens": 3}),
        ):
            notify("thread/tokenUsage/updated", threadId=thread_id, turnId="current-turn",
                   tokenUsage={"total": total, "last": last})
        emit(json.dumps({"type": "item.completed", "item": {
            "type": "agent_message", "text": marker_file.read_text(),
        }}), flush=True)
    emit('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt == "delegation capture":
    child = {"threadId": "child-1", "turnId": "child-turn"}
    sibling = {"threadId": "child-2", "turnId": "sibling-turn"}
    root = {"threadId": thread_id, "turnId": "current-turn"}

    def edge(owner, child_id, item_id):
        notify("item/completed", **owner, item={
            "type": "subAgentActivity", "kind": "started", "id": item_id, "agentThreadId": child_id,
        })

    def command(owner, output):
        notify("item/completed", **owner, item={
            "type": "commandExecution", "id": "shared-id", "command": "cat catalog.py",
            "aggregatedOutput": output, "exitCode": 0, "status": "completed",
        })

    notify("turn/started", **root, turn={"id": "current-turn"})
    command(child, "child-output")
    command(child, "child-output")
    edge(root, "child-1", "spawn-child")
    (state / "delegation-first").touch()
    deadline = time.monotonic() + 10
    while not (state / "delegation-resume").exists():
        assert time.monotonic() < deadline
        time.sleep(0.01)
    edge(root, "child-2", "spawn-sibling")
    edge(child, "nested-child", "spawn-nested")
    command(sibling, "sibling-output")
    command(child, "conflicting-output")
    notify("item/completed", **child, item={"type": "agentMessage", "id": "reply", "text": "child-only"})
    notify("turn/completed", **child, turn={"id": "child-turn", "status": "failed"})
    notify("item/completed", threadId="unrelated", turnId="other",
           item={"type": "agentMessage", "id": "private-item", "text": "unrelated-output"})
    notify("item/completed", **root, item={"type": "agentMessage", "id": "reply", "text": "root-only"})
    notify("turn/completed", **root, turn={"id": "current-turn", "status": "completed"})
    raise SystemExit(0)
if prompt == "report command outcome":
    time.sleep(1)
emit(json.dumps({"type": "turn.started", "prompt": prompt, "cwd": str(Path.cwd())}), flush=True)
if prompt == "report a diagnostic":
    emit(json.dumps({"type": "error", "message": api_key}), flush=True)
    emit('{"type":"turn.failed"}', flush=True)
    raise SystemExit(0)
if prompt == "emit a big record":
    emit(json.dumps({"type": "item.completed", "item": {
        "id": "item_1", "type": "agent_message", "text": "x" * (128 * 1024),
    }}), flush=True)
    emit('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt == "report command outcome":
    emit(json.dumps({"type": "item.completed", "item": {
        "type": "command_execution", "status": "failed", "exit_code": 127,
        "command": "private-fake-command", "aggregated_output": "private-fake-stdout",
    }}), flush=True)
    notify("thread/tokenUsage/updated", threadId=thread_id, turnId="current-turn",
           tokenUsage={"total": {"inputTokens": 12, "cachedInputTokens": 4, "outputTokens": 3}})
    notify("item/completed", threadId="another-thread", turnId="another-turn",
           item={"type": "agentMessage", "id": "child-item", "text": "private-fake-event"})
    emit('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt == "measure command spans":
    def command(event_type, item_id, status, exit_code=None):
        emit(json.dumps({"type": event_type, "item": {
            "type": "command_execution", "id": item_id, "status": status, "exit_code": exit_code,
            "command": "private-command-text", "aggregated_output": "private-command-output",
        }}), flush=True)
    command("item.started", "item_1", "in_progress")
    time.sleep(0.03)
    command("item.started", "item_2", "in_progress")
    command("item.completed", "item_2", "failed", 127)
    time.sleep(0.04)
    command("item.completed", "item_1", "completed", 0)
    command("item.completed", "item_3", "completed", 0)
    command("item.started", "item_4", "in_progress")
    command("item.started", "private-invalid-id", "in_progress")
    command("item.completed", "private-invalid-id", "completed", 0)
    emit('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt == "exit 7":
    raise SystemExit(7)
if prompt == "stream across reconnect":
    state = Path(os.environ["CODEX_HOME"])
    def wait_for(marker):
        deadline = time.monotonic() + 10
        while not (state / marker).exists():
            if time.monotonic() >= deadline:
                raise SystemExit(8)
            time.sleep(0.01)

    for sequence in range(3, 34):
        emit(json.dumps({"type": "future.event", "sequence": sequence}), flush=True)
    wait_for("emit-34")
    emit(json.dumps({"type": "future.event", "sequence": 34}), flush=True)
    wait_for("emit-35")
    emit(json.dumps({"type": "future.event", "sequence": 35}), flush=True)
    wait_for("finish-run")
    emit('{"type":"turn.completed"}', flush=True)
    raise SystemExit(0)
if prompt == "stay silent":
    assert resuming
    release = state / "release"
    deadline = time.monotonic() + 5
    while not release.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    raise SystemExit(0)
if prompt.startswith("  --help"):
    assert prompt == '  --help\\nLeia o README "juniper" ☕.\\n'
release = Path(os.environ["CODEX_HOME"]) / "release"
deadline = time.monotonic() + 5
while not release.exists():
    if time.monotonic() >= deadline:
        raise SystemExit(8)
    time.sleep(0.01)
emit('{"type":"turn.completed"}', flush=True)
"""
