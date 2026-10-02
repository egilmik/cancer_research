# Research monitor

Recurring literature searches (PubMed, RSS/Atom feeds, web) run by a Claude Code routine, with all
history kept in git.

- **Retrieval and de-duplication are deterministic.** `scripts/monitor.py` queries PubMed E-utilities
  and feeds, and checks every hit against everything ever recorded (PMID, DOI, normalized URL).
- **Judgement is Claude's.** Claude triages the new items against each search's criteria and writes a digest.
- **Git is the transaction.** A run's state, triage and digest land in one commit. If a run dies before
  pushing, nothing is recorded and the next run picks up the same window again.

```
searches.yaml            what to search, and what counts as relevant
scripts/monitor.py       fetch | add | status
state/items.jsonl        every item ever seen (append-only)
state/runs.jsonl         every query executed: exact query, PubMed's translation, date window, counts
runs/<run_id>/           new_items.json (script) + triage.json (Claude)
digests/<run_id>.md      the readable output
CLAUDE.md                the protocol Claude follows
ROUTINE_PROMPT.md        paste into the routine
```

## Setup

1. **Create a private GitHub repo** from this folder and push it. Leave `main` unprotected: the
   routine commits state there, and routines refuse to push to protected branches.

2. **Edit `searches.yaml`.** Set your email (NCBI asks for one) and replace the example search. Paste
   PubMed queries exactly as you'd type them in the PubMed search box; the script adds the date window.

3. **Optional: test locally.**
   ```bash
   python3 -m venv .venv && . .venv/bin/activate && pip install -r requirements.txt
   python scripts/monitor.py fetch --dry-run -v      # queries live, writes nothing
   ```

4. **Create a cloud environment** at claude.ai/code (environment selector → new environment):
   - Network access: **Custom**. Tick "Also include default list of common package managers"
     (needed for `pip install`), and add `eutils.ncbi.nlm.nih.gov` plus the domain of every feed in
     `searches.yaml`. Without this, PubMed calls fail with 403 `host_not_allowed`.
   - Optional: an NCBI API key as `NCBI_API_KEY` (raises the rate limit from 3 to 10 requests/s).

5. **Create the routine** at claude.ai/code/routines → New routine:
   - Prompt: paste `ROUTINE_PROMPT.md`.
   - Repository: this repo. Environment: the one from step 4.
   - Trigger: Schedule, e.g. weekly at 07:07 (avoid the exact hour; those runs can start late).
     Optionally add an **API** trigger too, for on-demand runs from scripts.
   - Connectors: all of yours are included by default. Remove everything except PubMed (optional,
     used only to enrich items without abstracts).

6. **Verify.** Click **Run now**, then open the run. A green status only means the session didn't
   crash, so read the transcript and check the commit on `main`. Click **Run now** again: the second
   run should report 0 new items. That confirms de-duplication works.

## Running on demand

- **Run now** on the routine page. Optionally add text such as:
  ```
  only: example-search
  since: 2026-01-01
  ```
- **API trigger**: POST to the routine's `/fire` endpoint with the same text in `{"text": "..."}`.
- **Interactive**: open a Claude Code session on the repo and ask, e.g. "what high-relevance items
  did we find this quarter?" or "add a search for X". CLAUDE.md tells Claude to answer from the files.

## Maintenance

- **Changing a query**: bump its `version`. Its window restarts at `initial_lookback_days`, and items
  already seen are still never reported twice.
- **Backfilling**: `since: YYYY-MM-DD` in the run text, or `fetch --since` locally. This is safe to
  repeat, because de-duplication filters out anything already recorded.
- **Truncation**: if a search returns more than `max_results`, its `runs.jsonl` line gets
  `"truncated": true` and the digest flags it. Narrow the query or raise the limit.
- **Status**: `python scripts/monitor.py status` shows the last successful run, item count and
  latest error per search.
