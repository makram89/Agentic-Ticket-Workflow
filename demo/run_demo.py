"""Submit demo/tickets.json to the running API and compare each result with the expectation.

    python demo/run_demo.py                # ids get a fresh suffix, so reruns are not 'duplicates'
    python demo/run_demo.py --suffix ""    # use the raw ids (a second run shows idempotency)
"""
import argparse, json, pathlib, time, urllib.error, urllib.request

def call(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, headers={"content-type": "application/json"},
                                 data=json.dumps(body).encode() if body is not None else None)
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()

def wait(base, tid, want, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        _, d = call(base, "GET", f"/tickets/{tid}")
        if isinstance(d, dict) and d["step"] in want:
            return d
        time.sleep(0.5)
    return d

ap = argparse.ArgumentParser()
ap.add_argument("--base", default="http://localhost:8000")
ap.add_argument("--suffix", default=time.strftime("-%H%M%S"))
args = ap.parse_args()

cases = json.loads((pathlib.Path(__file__).parent / "tickets.json").read_text())
ids = {}
for c in cases:
    t = dict(c["ticket"]); t["ticket_id"] += args.suffix
    ids[c["ticket"]["ticket_id"]] = t["ticket_id"]
    status, _ = call(args.base, "POST", "/tickets", t)
    assert status in (200, 202), (t["ticket_id"], status)

fails = 0
print(f"{'ticket':8} {'category':10} {'prio':7} {'action':9} {'step':18} {'outcome':16} result")
for c in cases:
    tid = ids[c["ticket"]["ticket_id"]]
    d = wait(args.base, tid, ("awaiting_approval", "completed", "failed"))
    if c["after"] in ("approve", "reject") and d["step"] == "awaiting_approval":
        call(args.base, "POST", f"/tickets/{tid}/approval",
             {"approved": c["after"] == "approve", "reviewer": "demo", "comment": "demo run"})
        d = wait(args.base, tid, ("completed", "failed"))
    dec, res = d["decision"] or {}, d["result"] or {}
    got = {"category": dec.get("category"), "priority": dec.get("priority"),
           "action": (dec.get("action") or {}).get("type"), "step": d["step"], "outcome": res.get("outcome")}
    e = c["expect"]
    bad = [k for k in e if k != "violation" and got.get(k) != e[k]]
    if e.get("violation") and not d["violations"]:
        bad.append("violation")
    fails += bool(bad)
    print(f"{c['ticket']['ticket_id']:8} {got['category'] or '-':10} {got['priority'] or '-':7} {got['action'] or '-':9} "
          f"{got['step']:18} {got['outcome'] or '-':16} {'OK' if not bad else 'MISMATCH ' + ','.join(bad)}")
print(f"\n{len(cases) - fails}/{len(cases)} as expected. Inspect one: curl -s localhost:8000/tickets/<id>{args.suffix}")
