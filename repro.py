"""Reproduces the Biome daemon's file watcher deadlock (biomejs/biome#8240).

Starts `biome lsp-proxy` on project/ (created by setup.sh) and opens
src/m0/f2.ts. Then it switches git branches with plain `git checkout`.
Nothing is sent over LSP during the switch: the daemon's own file watcher
sees the changes.

It prints:
- the daemon's CPU use over 5 seconds, while it should be idle;
- how long a `textDocument/formatting` request takes to answer;
- whether the watcher still sees file changes: it edits src/m0/f1.ts on disk
  so that it imports f2.ts, and waits for `noImportCycles` to be reported on
  the open f2.ts.

Usage: python3 repro.py [switches] [path/to/biome]
"""
import json
import os
import subprocess
import sys
import threading
import time

ROOT = os.path.dirname(os.path.abspath(__file__))
PROJECT = os.path.join(ROOT, "project")
SWITCHES = int(sys.argv[1]) if len(sys.argv) > 1 else 4
BIOME = sys.argv[2] if len(sys.argv) > 2 else os.path.join(ROOT, "node_modules", ".bin", "biome")
OPEN_FILE = os.path.join(PROJECT, "src", "m0", "f2.ts")
OPEN_URI = "file://" + OPEN_FILE
EDITED_FILE = "src/m0/f1.ts"


def daemon_pids():
    out = subprocess.run(["pgrep", "-f", "biome __run_server"], capture_output=True, text=True).stdout
    return out.split()


if daemon_pids():
    sys.exit("A Biome daemon is already running. Run `biome stop` (or kill it) first.")

proc = subprocess.Popen([BIOME, "lsp-proxy"], cwd=PROJECT, stdin=subprocess.PIPE, stdout=subprocess.PIPE)
received = threading.Condition()
responses = {}
published = []  # diagnostics published for the open file, oldest first


def send(msg):
    body = json.dumps(msg).encode()
    proc.stdin.write(b"Content-Length: %d\r\n\r\n" % len(body) + body)
    proc.stdin.flush()


def reader():
    while line := proc.stdout.readline():
        if line.lower().startswith(b"content-length"):
            n = int(line.split(b":")[1])
            proc.stdout.readline()
            msg = json.loads(proc.stdout.read(n))
            if "method" in msg and "id" in msg:  # server -> client request
                result = [{}] if msg["method"] == "workspace/configuration" else None
                send({"jsonrpc": "2.0", "id": msg["id"], "result": result})
            elif "id" in msg:
                with received:
                    responses[msg["id"]] = msg
                    received.notify_all()
            elif msg.get("method") == "textDocument/publishDiagnostics" and msg["params"]["uri"] == OPEN_URI:
                with received:
                    published.append([str(d.get("code")) for d in msg["params"]["diagnostics"]])
                    received.notify_all()


def request(id, method, params, timeout):
    """Sends a request and returns the seconds it took to answer, or None."""
    start = time.monotonic()
    send({"jsonrpc": "2.0", "id": id, "method": method, "params": params})
    with received:
        received.wait_for(lambda: id in responses, timeout)
    return time.monotonic() - start if id in responses else None


def cpu_seconds(pid):
    # `ps -o time` prints the total CPU time used, e.g. "1:02.53".
    out = subprocess.run(["ps", "-o", "time=", "-p", pid], capture_output=True, text=True).stdout.strip()
    total = 0.0
    for part in out.split(":"):
        total = total * 60 + float(part)
    return total


def cpu_percent(pid, window=5):
    before = cpu_seconds(pid)
    time.sleep(window)
    return (cpu_seconds(pid) - before) / window * 100


def watcher_sees_changes(timeout=15):
    """Edits a file on disk to create an import cycle with the open file, and
    waits for Biome to report it. Restores the file afterwards."""
    with received:
        seen = len(published)
    path = os.path.join(PROJECT, EDITED_FILE)
    with open(path) as f:
        original = f.read()
    with open(path, "w") as f:
        f.write("import { f2 } from './f2';\n" + original)
    try:
        with received:
            return received.wait_for(
                lambda: any("noImportCycles" in code for codes in published[seen:] for code in codes),
                timeout,
            )
    finally:
        subprocess.run(["git", "checkout", "-q", "--", EDITED_FILE], cwd=PROJECT, check=True)
        time.sleep(5)


threading.Thread(target=reader, daemon=True).start()
uri = "file://" + PROJECT
request(1, "initialize", {
    "processId": os.getpid(), "rootUri": uri, "workspaceFolders": [{"uri": uri, "name": "project"}],
    "capabilities": {"workspace": {"configuration": True}}}, 30)
send({"jsonrpc": "2.0", "method": "initialized", "params": {}})
send({"jsonrpc": "2.0", "method": "textDocument/didOpen", "params": {"textDocument": {
    "uri": OPEN_URI, "languageId": "typescript", "version": 1, "text": open(OPEN_FILE).read()}}})
time.sleep(15)  # initial scan

pid = daemon_pids()[0]
print(f"daemon pid {pid}, version: {subprocess.run([BIOME, '--version'], capture_output=True, text=True).stdout.strip()}")
format_params = {"textDocument": {"uri": OPEN_URI}, "options": {"tabSize": 2, "insertSpaces": True}}


def report(label, id):
    cpu = cpu_percent(pid)
    took = request(id, "textDocument/formatting", format_params, 10)
    answer = f"answered in {took:.2f}s" if took is not None else "NO ANSWER after 10s"
    print(f"{label:<22} cpu={cpu:5.1f}%  formatting request {answer}", flush=True)


report("before switching:", 100)
print(f"{'':<22} watcher sees file changes: {'yes' if watcher_sees_changes() else 'NO'}", flush=True)
for i in range(SWITCHES):
    branch = "b" if i % 2 == 0 else "a"
    subprocess.run(["git", "checkout", "-q", branch], cwd=PROJECT, check=True)
    time.sleep(15)
    report(f"after checkout {branch} (#{i + 1}):", 101 + i)
print(f"{'':<22} watcher sees file changes: {'yes' if watcher_sees_changes() else 'NO'}", flush=True)

proc.terminate()
subprocess.run(["kill", "-9", pid], stderr=subprocess.DEVNULL)
print("Stopped the daemon.")
