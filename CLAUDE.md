# Research monitor — operating protocol

This repository is a recurring literature monitor. **All memory of past searches lives in git**, never
in conversation memory. If it isn't in `state/`, `runs/` or `digests/`, it didn't happen.

## Layout

| Path | Written by | Contents |
|---|---|---|
| `searches.yaml` | the user (or you, only when asked) | search definitions and triage criteria |
| `scripts/monitor.py` | — | retrieval + de-duplication; the only writer of `state/` |
| `state/items.jsonl` | monitor.py | every item ever recorded (append-only) |
| `state/runs.jsonl` | monitor.py | every search execution: exact query, date window, counts, errors |
| `runs/<run_id>/new_items.json` | monitor.py | items new in that run, with abstracts |
| `runs/<run_id>/triage.json` | you | relevance judgement per new item |
| `digests/<run_id>.md` | you | human-readable digest of the run |

## Hard rules

- Never edit `state/*.jsonl` by hand. Never rewrite git history or force-push.
- Only `monitor.py` decides what is new. Do not mark anything as seen yourself.
- Every item in a digest must come from `runs/<run_id>/new_items.json`. Never add items from memory.
- A run that finds nothing still gets a digest and a commit, so gaps are visible.
- Report source errors at the top of the digest. Do not retry a failing source more than once.

## Scheduled run protocol

1. `pip install -q -r requirements.txt`
2. `python scripts/monitor.py fetch` (add `--only ID ...` and/or `--since YYYY-MM-DD` if the run
   prompt asked for it). Note the `run_id` it prints. Exit code 3 means some sources failed: continue.
3. For each entry in `web_queries_to_run` from step 2, run the query with your web search tool.
   Keep only results that are actual publications, preprints, trial records or announcements, not
   navigation or listing pages. Save them as a JSON list of `{"url", "title", "snippet", "date"}` and run:
   `python scripts/monitor.py add --run RUN_ID --search ID --query "EXACT QUERY" --file /tmp/web-N.json`
   Run `add` even when you keep zero results (pass `[]`), so the query is logged.
4. Read `runs/RUN_ID/new_items.json`. For each item, judge relevance against the `criteria` of every
   search in its `searches` list: `high`, `medium` or `low`, with a one-sentence reason grounded in
   the title/abstract. If the abstract is missing and the PubMed connector is available, you may use
   it to look the item up. Write `runs/RUN_ID/triage.json` as
   `{"<first key of the item>": {"relevance": "...", "reason": "..."}}`.
5. Write `digests/RUN_ID.md`:
   - Header: date, searches run, per-source counts and PubMed date windows (from this run's lines in
     `state/runs.jsonl`), any errors or `truncated` flags.
   - **High**: per item, 2–4 sentences (what was studied, main finding, why it matters for the
     criteria), then authors, journal, year and link.
   - **Medium**: one line each with link.
   - **Low**: title and link only.
6. Commit and push to `main`:
   `git add -A && git commit -m "run RUN_ID: N new, H high" && git push origin HEAD:main`.
   If the push is rejected because `main` moved, `git pull --rebase origin main` and push again.
7. Finish with a three-line summary: new items, high-relevance titles, and the digest path.

## Interactive use

- Questions about history ("what did we find on X", "when did Y last run", "has PMID Z been seen"):
  answer from `state/`, `runs/` and `digests/`, using `python scripts/monitor.py status` and grep.
  Cite run ids, PMIDs and DOIs.
- Adding or changing a search: edit `searches.yaml`. If you change an existing query, bump its
  `version`, which restarts its date window and keeps the log unambiguous.
- Testing a query without touching state: `python scripts/monitor.py fetch --dry-run --only ID -v`.
