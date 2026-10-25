"""Smoke test for a running stack (docker compose up): submit demo/tickets.json to the API and check each outcome.

    python demo/smoke_test.py                # ids get a fresh suffix, so reruns are not 'duplicates'
    python demo/smoke_test.py --suffix ""    # raw ids (a second run shows idempotency)

Exit code 0 = every ticket ended as expected, 1 = a mismatch, 2 = the API never became ready.
"""
import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

TICKETS = Path(__file__).parent / "tickets.json"
TERMINAL = ("completed", "failed")


def call(base: str, method: str, path: str, body: dict | None = None) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, method=method, data=data, headers={"content-type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read() or b"null")
    except urllib.error.HTTPError as err:
        return err.code, err.read().decode()


def wait_for_api(base: str, timeout: float) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            if call(base, "GET", "/openapi.json")[0] == 200:
                return True
        except OSError:
            pass
        time.sleep(1)
    return False


def wait_for_step(base: str, ticket_id: str, steps: tuple[str, ...], timeout: float = 30) -> dict:
    deadline = time.monotonic() + timeout
    status: dict = {}
    while time.monotonic() < deadline:
        code, body = call(base, "GET", f"/tickets/{ticket_id}")
        if code == 200 and isinstance(body, dict):
            status = body
            if status["step"] in steps:
                break
        time.sleep(0.5)
    return status


def run_case(base: str, case: dict, ticket_id: str) -> tuple[dict, list[str]]:
    """Wait for the ticket (approving or rejecting it if the case says so); return its summary and mismatches."""
    status = wait_for_step(base, ticket_id, ("awaiting_approval", *TERMINAL))
    if case["after"] in ("approve", "reject") and status.get("step") == "awaiting_approval":
        approval = {"approved": case["after"] == "approve", "reviewer": "smoke-test", "comment": "smoke test"}
        call(base, "POST", f"/tickets/{ticket_id}/approval", approval)
        status = wait_for_step(base, ticket_id, TERMINAL)

    decision = status.get("decision") or {}
    got = {
        "category": decision.get("category"),
        "priority": decision.get("priority"),
        "action": (decision.get("action") or {}).get("type"),
        "step": status.get("step"),
        "outcome": (status.get("result") or {}).get("outcome"),
    }
    expect = case["expect"]
    mismatches = [key for key, want in expect.items() if key != "violation" and got.get(key) != want]
    if expect.get("violation") and not status.get("violations"):
        mismatches.append("violation")
    return got, mismatches


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", default="http://localhost:8000")
    parser.add_argument("--suffix", default=time.strftime("-%H%M%S"))
    parser.add_argument("--wait", type=float, default=60, help="seconds to wait for the API to come up")
    args = parser.parse_args()

    if not wait_for_api(args.base, args.wait):
        print(f"API at {args.base} not ready after {args.wait:g}s", file=sys.stderr)
        return 2

    cases = json.loads(TICKETS.read_text())
    ids = {}
    for case in cases:
        ticket = {**case["ticket"], "ticket_id": case["ticket"]["ticket_id"] + args.suffix}
        code, body = call(args.base, "POST", "/tickets", ticket)
        if code not in (200, 202):
            print(f"POST /tickets {ticket['ticket_id']} -> {code}: {body}", file=sys.stderr)
            return 1
        ids[case["ticket"]["ticket_id"]] = ticket["ticket_id"]

    failures = 0
    print(f"{'ticket':8} {'category':10} {'prio':7} {'action':9} {'step':18} {'outcome':16} result")
    for case in cases:
        name = case["ticket"]["ticket_id"]
        got, mismatches = run_case(args.base, case, ids[name])
        failures += bool(mismatches)
        verdict = "OK" if not mismatches else "MISMATCH " + ",".join(mismatches)
        print(f"{name:8} {got['category'] or '-':10} {got['priority'] or '-':7} {got['action'] or '-':9} "
              f"{got['step'] or '-':18} {got['outcome'] or '-':16} {verdict}")

    print(f"\n{len(cases) - failures}/{len(cases)} as expected.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
