#!/usr/bin/env python3
"""Provision / reset / tear down an event's resources through the app's host API.

A thin operator wrapper over the same plan/execute endpoints the host console
uses — for rehearsals, scripted event ops, and workspaces where you prefer the
CLI to clicking:

    # Dry-run (always safe): show the full bootstrap plan incl. workspace items
    python scripts/provision_event.py plan --app-url https://<app-host> --event <id>

    # Provision team schemas + seed SQL + workspace resources
    python scripts/provision_event.py bootstrap --app-url ... --event <id>

    # Drop team schemas only (keeps the event + workspace resources)
    python scripts/provision_event.py reset --app-url ... --event <id> --yes

    # Whole-event teardown: workspace resources + schemas + the event catalog
    python scripts/provision_event.py teardown --app-url ... --event <id> --yes

Auth: pass ``--token`` or set ``QUEST_APP_TOKEN`` (an OAuth token accepted by
the Databricks App, e.g. ``databricks auth token`` output for the workspace
hosting the app). The caller must satisfy the app's host gate.
"""

import argparse
import json
import os
import sys
import urllib.request


def _request(method: str, url: str, token: str, payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            return json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as exc:  # noqa: PERF203
        body = exc.read().decode(errors="replace")
        raise SystemExit(f"HTTP {exc.code} from {url}\n{body}")


def _print_plan(items):
    for item in items:
        flag = "OK " if item.get("within_namespace") else "BLK"
        kind = item.get("kind", "sql")
        print(f"  [{flag}] {kind:9s} {item.get('op', ''):28s} {item.get('target', '')}")
        if item.get("error"):
            print(f"        ! {item['error']}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["plan", "bootstrap", "reset", "teardown"])
    parser.add_argument("--app-url", required=True, help="Deployed app base URL")
    parser.add_argument("--event", required=True, help="Event id or slug")
    parser.add_argument("--token", default=os.getenv("QUEST_APP_TOKEN", ""))
    parser.add_argument("--plan-action", default="bootstrap",
                        choices=["bootstrap", "reset", "teardown"],
                        help="Which plan to dry-run (action=plan only)")
    parser.add_argument("--yes", action="store_true",
                        help="Confirm destructive actions (reset/teardown)")
    args = parser.parse_args()

    if not args.token:
        raise SystemExit("Provide --token or set QUEST_APP_TOKEN.")

    base = args.app_url.rstrip("/")
    event_base = f"{base}/api/host/events/{args.event}/resources"

    if args.action == "plan":
        result = _request("POST", f"{event_base}/plan", args.token,
                          {"action": args.plan_action})
        print(f"Plan ({result.get('action')}):")
        _print_plan(result.get("plan", []))
        blockers = result.get("blockers", [])
        print(f"Blockers: {len(blockers)}")
        return 1 if blockers else 0

    if args.action in ("reset", "teardown") and not args.yes:
        raise SystemExit(f"{args.action} is destructive — re-run with --yes.")

    payload = {"confirm": True} if args.action in ("reset", "teardown") else None
    result = _request("POST", f"{event_base}/{args.action}", args.token, payload)
    print(f"{args.action}: ok={result.get('ok')}")
    _print_plan(result.get("executed", []))
    ws = result.get("workspace") or {}
    if ws:
        print(f"workspace: ok={ws.get('ok')}")
        _print_plan(ws.get("executed", []))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
