#!/usr/bin/env python3
"""Progress of a backfill plan (e.g. backfill/2020.yaml), read from state/runs.jsonl.

A chunk is done when every search in it has an ok, untruncated PubMed record whose
since/until overrides match the chunk. Read-only: it never writes to state/.

    python scripts/backfill_status.py backfill/2020.yaml          # table of all chunks
    python scripts/backfill_status.py backfill/2020.yaml --next   # run text for the next pending chunk
"""
import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("plan")
    ap.add_argument("--next", action="store_true", help="print the run text for the next pending chunk")
    args = ap.parse_args()

    plan = yaml.safe_load(open(args.plan))
    known = {s["id"] for s in yaml.safe_load(open(ROOT / "searches.yaml"))["searches"]}
    groups = plan["groups"]
    unknown = {sid for g in groups.values() for sid in g} - known
    if unknown:
        sys.exit(f"unknown search id(s) in plan: {', '.join(sorted(unknown))}")

    ok: dict[tuple[str, str], set[str]] = {}
    started: dict[tuple[str, str], set[str]] = {}
    for line in open(ROOT / "state" / "runs.jsonl"):
        r = json.loads(line)
        if r.get("source") != "pubmed" or not r.get("since_override") or not r.get("until_override"):
            continue
        key = (r["since_override"], r["until_override"])
        started.setdefault(key, set()).add(r["run"])
        if r.get("status") == "ok" and not r.get("truncated"):
            ok.setdefault(key, set()).add(r["search"])

    pending = []
    for c in plan["chunks"]:
        key = (str(c["since"]), str(c["until"]))
        missing = [s for s in groups[c["group"]] if s not in ok.get(key, set())]
        state = "done" if not missing else ("partial" if key in started else "pending")
        if args.next:
            if missing:
                print(f"only: {', '.join(missing)}\nsince: {key[0]}\nuntil: {key[1]}")
                return 0
            continue
        runs = ",".join(sorted(started.get(key, ())))
        note = f"  missing: {', '.join(missing)}" if state == "partial" else ""
        print(f"{key[0]} .. {key[1]}  {c['group']:<16} {state:<8} {runs}{note}")
        if missing:
            pending.append(key)

    if args.next:
        print("backfill complete", file=sys.stderr)
        return 1
    print(f"\n{len(plan['chunks']) - len(pending)}/{len(plan['chunks'])} chunks done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
