#!/usr/bin/env python3
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLIENT = ROOT / "main.py"
SERVER = ROOT / "srv" / "server.py"
UPSTREAM_REF = "upstream/main"


def run_git(args):
    p = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(p.stderr.strip() or p.stdout.strip() or "git command failed")
    return p.stdout


def read_ref_file(ref, path):
    return run_git(["show", f"{ref}:{path}"])


def read_local_file(path):
    return Path(path).read_text(encoding="utf-8", errors="replace")


def extract_client_features(src):
    actions_sent = set(re.findall(r'"action"\s*:\s*"([^"]+)"', src))
    actions_recv = set(re.findall(r'act\s*==\s*"([^"]+)"', src))
    ui_hooks = set(re.findall(r"def\s+(on_[a-zA-Z0-9_]+)\(", src))
    return actions_sent, actions_recv, ui_hooks


def extract_server_features(src):
    actions_handled = set(re.findall(r'action\s*==\s*"([^"]+)"', src))
    commands = set(re.findall(r'cmd\s*==\s*"([^"]+)"', src))
    return actions_handled, commands


def print_delta(title, upstream_set, local_set):
    missing = sorted(upstream_set - local_set)
    added = sorted(local_set - upstream_set)
    print(f"\n[{title}]")
    print(f"  Missing vs upstream: {len(missing)}")
    if missing:
        print(f"  -> {', '.join(missing[:25])}" + (" ..." if len(missing) > 25 else ""))
    print(f"  Added in fork: {len(added)}")
    if added:
        print(f"  -> {', '.join(added[:25])}" + (" ..." if len(added) > 25 else ""))


def main():
    try:
        up_client = read_ref_file(UPSTREAM_REF, "main.py")
        up_server = read_ref_file(UPSTREAM_REF, "srv/server.py")
    except Exception as e:
        print(f"Error: could not read {UPSTREAM_REF}. Run `git fetch upstream` first.\n{e}")
        return 2

    local_client = read_local_file(CLIENT)
    local_server = read_local_file(SERVER)

    up_sent, up_recv, up_hooks = extract_client_features(up_client)
    lc_sent, lc_recv, lc_hooks = extract_client_features(local_client)
    up_srv_actions, up_srv_cmds = extract_server_features(up_server)
    lc_srv_actions, lc_srv_cmds = extract_server_features(local_server)

    print("Thrive Messenger Upstream Parity Report")
    print(f"Upstream ref: {UPSTREAM_REF}")
    print(f"Local root: {ROOT}")

    print_delta("Client actions sent", up_sent, lc_sent)
    print_delta("Client actions received", up_recv, lc_recv)
    print_delta("Client UI hooks", up_hooks, lc_hooks)
    print_delta("Server actions handled", up_srv_actions, lc_srv_actions)
    print_delta("Server admin commands", up_srv_cmds, lc_srv_cmds)
    return 0


if __name__ == "__main__":
    sys.exit(main())
