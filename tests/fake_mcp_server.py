"""A stdio MCP server for the proxy tests, standard library only.

It answers the way real servers do, including the less common traffic a
proxy must not disturb: resources, prompts, a notification after a call, a
request of its own to the client (sampling), a large result, and unicode.
Tools: ok, fails (isError result), rpc_error (JSON-RPC error), slow.
"""

import json
import sys
import time


SEEN = set()


def send(msg):
    sys.stdout.write(json.dumps(msg, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        try:
            msg = json.loads(line)
        except ValueError:
            send({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
            continue
        if "method" not in msg:
            # a response from the client to our sampling request: echo it back
            # as a notification so the test can see it arrived unchanged
            send({"jsonrpc": "2.0", "method": "notifications/message",
                  "params": {"level": "info", "data": {"client_answered": msg}}})
            continue
        mid, method, params = msg.get("id"), msg["method"], msg.get("params") or {}
        if method == "initialize":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "protocolVersion": params.get("protocolVersion", "2025-06-18"),
                "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                "serverInfo": {"name": "fake-server", "version": "1.0"}}})
        elif method == "notifications/initialized":
            pass
        elif method == "tools/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": n, "inputSchema": {"type": "object"}} for n in ("ok", "fails", "rpc_error", "slow", "big")]}})
        elif method == "resources/list":
            send({"jsonrpc": "2.0", "id": mid, "result": {"resources": [
                {"uri": "file:///ünïcode.txt", "name": "ünïcode"}]}})
        elif method == "prompts/get":
            send({"jsonrpc": "2.0", "id": mid, "result": {"messages": [
                {"role": "user", "content": {"type": "text", "text": "héllo"}}]}})
        elif method == "ask_client":
            send({"jsonrpc": "2.0", "id": "srv-1", "method": "sampling/createMessage",
                  "params": {"messages": [], "maxTokens": 5}})
            send({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "tools/call":
            name = params.get("name")
            if name == "ok":
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "secret-result-token"}], "isError": False}})
                send({"jsonrpc": "2.0", "method": "notifications/progress",
                      "params": {"progressToken": 1, "progress": 1}})
            elif name == "fails":
                send({"jsonrpc": "2.0", "id": mid, "result": {
                    "content": [{"type": "text", "text": "429 rate limit exceeded secret-error-token"}],
                    "isError": True}})
            elif name == "flaky":
                # 503 on the first call with these arguments, fine after that
                key = json.dumps(params.get("arguments") or {}, sort_keys=True)
                if key not in SEEN:
                    SEEN.add(key)
                    send({"jsonrpc": "2.0", "id": mid, "result": {
                        "content": [{"type": "text", "text": "503 service unavailable"}], "isError": True}})
                else:
                    send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "ok"}]}})
            elif name == "rpc_error":
                send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32603, "message": "upstream timeout"}})
            elif name == "slow":
                time.sleep(float(params.get("arguments", {}).get("seconds", 1)))
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "slow ok"}]}})
            elif name == "big":
                send({"jsonrpc": "2.0", "id": mid, "result": {"content": [{"type": "text", "text": "x" * 2_000_000}]}})
        elif method == "exit_now":
            sys.exit(int(params.get("code", 3)))
        elif mid is not None:
            send({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}})


if __name__ == "__main__":
    main()
