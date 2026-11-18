#!/usr/bin/env python3
"""One-time migration: copy the live bot's state.json into Vercel KV.

Run BEFORE cutover, after the Vercel KV store exists and its env vars are
known. The live VM bot must be stopped first (or at least not writing),
otherwise a later VM save can overwrite what you just migrated — the
webhook bot and the polling bot share nothing but this one KV key.

Usage (from the project root):
    export KV_REST_API_URL=https://your-db.upstash.io
    export KV_REST_API_TOKEN=AX...
    python3 tools/migrate_state.py [path/to/state.json]

Defaults: state path is the live bot's
~/workspace/custom-connectors/custom.telegram/state.json, KV key "state".
The script prints the migrated key count and verifies the value by reading
it back. It never prints the token.
"""
import json
import os
import sys
import urllib.request

KV_KEY = os.environ.get("KV_STATE_KEY", "state")


def kv_request(path, method="GET", body=None):
    url = os.environ.get("KV_REST_API_URL", "").rstrip("/")
    token = os.environ.get("KV_REST_API_TOKEN", "")
    if not url or not token:
        raise SystemExit("KV_REST_API_URL and KV_REST_API_TOKEN must be set in the environment")
    req = urllib.request.Request(
        url + path, data=body, method=method,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main():
    default_state = os.path.expanduser(
        "~/workspace/custom-connectors/custom.telegram/state.json"
    )
    state_path = sys.argv[1] if len(sys.argv) > 1 else default_state
    with open(state_path, encoding="utf-8") as f:
        state = json.load(f)
    print(f"read {state_path}: {len(state)} top-level keys "
          f"(channel_id={state.get('channel_id')}, "
          f"subscribers={len(state.get('subscribers', []))}, "
          f"users={len(state.get('users', {}))})")
    payload = json.dumps([["SET", KV_KEY, json.dumps(state)]]).encode("utf-8")
    result = kv_request("/pipeline", method="POST", body=payload)
    results = result.get("result") or []
    if not results or results[0].get("result") != "OK":
        raise SystemExit(f"KV SET failed: {result}")
    back = kv_request(f"/get/{KV_KEY}").get("result")
    restored = json.loads(back) if back else {}
    if restored.get("channel_id") != state.get("channel_id"):
        raise SystemExit("verification failed: channel_id does not match after round-trip")
    print(f"migrated OK: key '{KV_KEY}' verified in KV "
          f"({len(restored)} keys, channel {restored.get('channel_id')})")


if __name__ == "__main__":
    main()
