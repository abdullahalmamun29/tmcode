"""A tiny language server for tests: flags lines containing BAD, completes, hovers, defines."""

import json
import sys

PULL = "--pull" in sys.argv  # newer servers: the editor asks for problems


def read():
    length = None
    while True:
        line = sys.stdin.buffer.readline()
        if not line:
            sys.exit(0)
        line = line.strip()
        if not line:
            break
        name, _, value = line.decode().partition(":")
        if name.lower() == "content-length":
            length = int(value)
    return json.loads(sys.stdin.buffer.read(length))


def send(msg):
    body = json.dumps(msg).encode()
    sys.stdout.buffer.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    sys.stdout.buffer.flush()


def problems(text):
    diags = []
    for row, line in enumerate(text.split("\n")):
        col = line.find("BAD")
        if col >= 0:
            diags.append({"range": {"start": {"line": row, "character": col},
                                    "end": {"line": row, "character": col + 3}},
                          "severity": 1, "message": "BAD is not allowed", "source": "fake"})
    return diags


def publish(uri, text):
    if not PULL:
        send({"jsonrpc": "2.0", "method": "textDocument/publishDiagnostics",
              "params": {"uri": uri, "diagnostics": problems(text)}})


docs = {}
while True:
    msg = read()
    method, params = msg.get("method"), msg.get("params") or {}
    if method == "initialize":
        # Ask the client something first, like real servers do.
        send({"jsonrpc": "2.0", "id": "cfg", "method": "workspace/configuration",
              "params": {"items": [{"section": "fake"}]}})
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {
            "textDocumentSync": 1, "hoverProvider": True, "definitionProvider": True,
            "completionProvider": {"triggerCharacters": ["."]},
            **({"diagnosticProvider": {"interFileDependencies": False, "workspaceDiagnostics": False}} if PULL else {}),
        }}})
    elif method == "textDocument/diagnostic":
        uri = params["textDocument"]["uri"]
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"kind": "full", "items": problems(docs.get(uri, ""))}})
    elif method == "textDocument/didOpen":
        doc = params["textDocument"]
        docs[doc["uri"]] = doc["text"]
        publish(doc["uri"], doc["text"])
    elif method == "textDocument/didChange":
        uri = params["textDocument"]["uri"]
        docs[uri] = params["contentChanges"][-1]["text"]
        publish(uri, docs[uri])
    elif method == "textDocument/completion":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {"isIncomplete": False, "items": [
            {"label": "alpha_function", "kind": 3, "insertText": "alpha_function(${1:x})", "insertTextFormat": 2},
            {"label": "alpine", "kind": 6},
            {"label": "zeta", "kind": 6},
        ]}})
    elif method == "textDocument/hover":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": {
            "contents": {"kind": "markdown", "value": "```python\ndef alpha_function(x)\n```\nDoes alpha."}}})
    elif method == "textDocument/definition":
        uri = params["textDocument"]["uri"]
        send({"jsonrpc": "2.0", "id": msg["id"], "result": [
            {"uri": uri, "range": {"start": {"line": 0, "character": 4}, "end": {"line": 0, "character": 9}}}]})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
    elif method == "exit":
        sys.exit(0)
    elif "id" in msg and method:
        send({"jsonrpc": "2.0", "id": msg["id"], "result": None})
