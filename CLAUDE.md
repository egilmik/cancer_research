# Research monitor — operating protocol

This repository is a recurring literature monitor. **All memory of past searches lives in git**, never
in conversation memory. If it isn't in `state/`, `runs/` or `digests/`, it didn't happen.

## Layout

| Path | Written by | Contents |
|---|---|---|
| `searches.yaml` | the user (or you, only when asked) | search definitions and triage criteria |
| `scripts/monitor.py` | — | retrieval (PubMed, ClinicalTrials.gov, feeds) + de-duplication; the only writer of `state/` |
| `state/items.jsonl` | monitor.py | every item ever recorded (append-only) |
| `state/runs.jsonl` | monitor.py | every search execution: exact query, date window, counts, errors |
| `runs/<run_id>/new_items.json` | monitor.py | items new in that run, with abstracts |
| `runs/<run_id>/triage.json` | you | relevance judgement per new item |
| `digests/<run_id>.md` | you | human-readable digest of the run |
| `practice_changing.md` | `scripts/build_index.py` | accumulating index of every practice-changing item; generated, never hand-edited |

## Hard rules

- Never edit `state/*.jsonl` by hand. Never rewrite git history or force-push.
- Only `monitor.py` decides what is new. Do not mark anything as seen yourself.
- Every item in a digest must come from `runs/<run_id>/new_items.json`. Never add items from memory.
- A run that finds nothing still gets a digest and a commit, so gaps are visible.
- Report source errors at the top of the digest. Do not retry a failing source more than once.
- Report what each study found, with its limitations (design, size, endpoints, follow-up).
  Do not extrapolate beyond the data or overstate preliminary results.

## Scheduled run protocol

1. `pip install -q -r requirements.txt`
2. `python scripts/monitor.py fetch` (add `--only ID ...`, `--since YYYY-MM-DD` and/or
   `--until YYYY-MM-DD` if the run prompt asked for it). Note the `run_id` it prints. Exit code 3 means some sources failed: continue.
3. For each entry in `web_queries_to_run` from step 2, run the query with your web search tool.
   Keep only results that are actual publications, preprints, trial records or announcements, not
   navigation or listing pages. Save them as a JSON list of `{"url", "title", "snippet", "date"}` and run:
   `python scripts/monitor.py add --run RUN_ID --search ID --query "EXACT QUERY" --file /tmp/web-N.json`
   Run `add` even when you keep zero results (pass `[]`), so the query is logged.
4. Read `runs/RUN_ID/new_items.json`. If it holds more than about 60 items, work through it in
   batches of 40 (slice it with python), adding to `triage.json` after each batch. For each item, judge relevance against the `criteria` of every
   search in its `searches` list: `practice-changing`, `high`, `medium` or `low`, with a one-sentence reason grounded in
   the title/abstract. `practice-changing` is a separate, stricter bar: the top-level `practice_changing`
   rubric in `searches.yaml` plus the search's own `practice_changing` line. It covers practice-changing
   trials (FLAURA-type), regulatory decisions and guideline changes. Expect 0–2 per run; when in doubt, use
   `high`. A practice-changing item also gets
   `"practice_change": {"kind": "trial" | "regulatory" | "guideline", "basis": "..."}`, where `basis` names the
   evidence in one line: for a trial the name, phase, n, comparator, primary endpoint and effect size (HR
   with CI, absolute difference); for a decision the agency, date, drug and indication; for a guideline
   the body, version and the recommendation that changed. Use only facts in the item; if the abstract does
   not give them, look the item up, and if they still are not there, rate it `high`. If the abstract is missing and the PubMed connector is available, you may use
   it to look the item up. For `source: clinicaltrials` items, judge by phase, design, sponsor,
   interventions and size; `ct_event: results` means results were just posted for that trial.
   Write `runs/RUN_ID/triage.json` as
   `{"<first key of the item>": {"relevance": "...", "reason": "...", "practice_change": {...}}}`
   (`practice_change` only for practice-changing items).
   **Backfill runs** (fetch with `--until`, or `--since` more than ~3 months back): every item
   still gets a relevance, but `low` items may have an empty reason (`""`) and `medium` reasons
   should be one short line. `high` items get the full treatment as usual.
5. Write `digests/RUN_ID.md`:
   - Header: date, searches run, per-source counts and PubMed date windows (from this run's lines in
     `state/runs.jsonl`), any errors or `truncated` flags.
   - **Practice-changing**, first and even when empty ("None this run."): per item, a full write-up of
     4–6 sentences: design, n, comparator, primary endpoint, key effect sizes with CIs, follow-up,
     the main toxicity trade-off, and what is still open (OS maturity, approval, reimbursement in
     Norway, applicability). For regulatory items: agency, date, indication and label wording, and the
     trial it rests on. Then authors, journal, year and link.
   - **High**, grouped by search: per item, 2–4 sentences (what was studied, main finding with
     the key numbers, why it matters for the criteria, main limitation), then authors, journal,
     year and link. New trial registrations go in their own sub-list with phase, sponsor and arms.
     Mark preprints as "preprint, not peer reviewed".
   - **Medium**: one line each with link.
   - **Low**: title and link only.
6. Run `python scripts/build_index.py`. It validates every `triage.json` and rebuilds
   `practice_changing.md`. If it reports errors, fix this run's `triage.json` and run it again.
   Then commit and push to `main`:
   `git add -A && git commit -m "run RUN_ID: N new, P practice-changing, H high" && git push origin HEAD:main`.
   If the push is rejected because `main` moved, `git pull --rebase origin main` and push again.
7. Finish with a three-line summary: new items, practice-changing and high-relevance titles, and the digest path.

## Interactive use

- Questions about history ("what did we find on X", "when did Y last run", "has PMID Z been seen"):
  answer from `state/`, `runs/` and `digests/`, using `python scripts/monitor.py status` and grep.
  Cite run ids, PMIDs and DOIs. For "what has changed practice", start from `practice_changing.md`.
- Re-rating an old item (e.g. promoting it to practice-changing when its final OS appears): change its
  entry in that run's `triage.json`, run `python scripts/build_index.py`, and commit with a message saying
  why. The original rating stays in git history.
- Adding or changing a search: edit `searches.yaml`. If you change an existing query, bump its
  `version`, which restarts its date window and keeps the log unambiguous.
- Testing a query without touching state: `python scripts/monitor.py fetch --dry-run --only ID -v`.
