#!/usr/bin/env python3
"""Research monitor: deterministic retrieval and de-duplication for recurring searches.

All state lives in git-tracked files, so every run is reproducible and auditable:
  state/items.jsonl   every item ever recorded (append-only)
  state/runs.jsonl    every search execution, with the exact query and date window (append-only)
  runs/<run_id>/      per-run output: new_items.json (this script), triage.json (Claude)

Subcommands
  fetch   Run enabled searches (PubMed + RSS/Atom feeds) and record new items.
  add     Record hits Claude found with web search (de-duplicated by URL/DOI).
  status  Show last run per search and item counts.

Only this script writes to state/. Nothing persists until the run is committed and pushed,
so a failed run leaves the store untouched and the next run simply retries.
"""
from __future__ import annotations

import argparse
import datetime as dt
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
SEARCHES = ROOT / "searches.yaml"
ITEMS = ROOT / "state" / "items.jsonl"
RUNS_LOG = ROOT / "state" / "runs.jsonl"
RUNS_DIR = ROOT / "runs"

EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
TOOL = "research-monitor"
USER_AGENT = "research-monitor/1.0 (+https://github.com)"
ID_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]*$")
DOI_PATTERN = re.compile(r"10\.\d{4,9}/[^\s\"<>]+")
TRACKING_PARAM = re.compile(r"^(utm_|fbclid$|gclid$|mc_)")

EXIT_PARTIAL = 3  # some sources failed; the rest were recorded


# --------------------------------------------------------------------------- files

def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def append_jsonl(path: Path, records: list[dict]) -> None:
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")


def write_json(path: Path, data) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def load_config() -> dict:
    cfg = yaml.safe_load(SEARCHES.read_text(encoding="utf-8")) or {}
    searches = cfg.get("searches") or []
    ids = [s.get("id") for s in searches]
    for sid in ids:
        if not sid or not ID_PATTERN.match(str(sid)):
            sys.exit(f"searches.yaml: invalid search id {sid!r} (use lowercase letters, digits, hyphens)")
    dupes = {i for i in ids if ids.count(i) > 1}
    if dupes:
        sys.exit(f"searches.yaml: duplicate search ids: {', '.join(sorted(dupes))}")
    cfg.setdefault("settings", {})
    cfg["searches"] = searches
    return cfg


# --------------------------------------------------------------------------- identity / dedupe

def norm_url(url: str) -> str:
    p = urllib.parse.urlsplit(url.strip())
    query = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=True)
             if not TRACKING_PARAM.match(k)]
    path = p.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit((p.scheme.lower(), p.netloc.lower(), path,
                                    urllib.parse.urlencode(query), ""))


def norm_doi(doi: str) -> str:
    d = doi.strip().lower()
    return re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", d)


def item_keys(item: dict) -> list[str]:
    keys = []
    if item.get("pmid"):
        keys.append(f"pmid:{item['pmid']}")
    if item.get("doi"):
        keys.append(f"doi:{norm_doi(item['doi'])}")
    if item.get("url") and not item.get("pmid"):
        keys.append(f"url:{norm_url(item['url'])}")
    return keys


def load_seen() -> set[str]:
    seen: set[str] = set()
    for rec in read_jsonl(ITEMS):
        seen.update(rec.get("keys", []))
    return seen


class RunItems:
    """New items found in this run, merged across searches and sources."""

    def __init__(self) -> None:
        self.items: list[dict] = []
        self.index: dict[str, dict] = {}

    def collect(self, found: list[dict], search_id: str, seen: set[str]) -> int:
        """Add unseen items; return how many new-to-the-store items matched this search."""
        n = 0
        for it in found:
            keys = item_keys(it)
            if not keys:
                continue
            existing = next((self.index[k] for k in keys if k in self.index), None)
            if existing is not None:  # already new in this run via another search/source
                for field, value in it.items():  # e.g. enrich a feed hit with PubMed metadata
                    if value and not existing.get(field):
                        existing[field] = value
                if search_id not in existing["searches"]:
                    existing["searches"].append(search_id)
                    n += 1
                for k in keys:
                    if k not in existing["keys"]:
                        existing["keys"].append(k)
                        self.index[k] = existing
                continue
            if any(k in seen for k in keys):
                continue
            rec = dict(it, searches=[search_id], keys=keys)
            self.items.append(rec)
            for k in keys:
                self.index[k] = rec
            n += 1
        return n


# --------------------------------------------------------------------------- http

def http_request(url: str, params: dict | None = None, data: dict | None = None,
                 timeout: int = 60) -> bytes:
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    body = urllib.parse.urlencode(data).encode() if data else None
    req = urllib.request.Request(url, data=body, headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except urllib.error.HTTPError as e:
            if e.code in (429, 500, 502, 503, 504) and attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise
        except urllib.error.URLError:
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
                continue
            raise
    raise RuntimeError("unreachable")


def describe_error(e: Exception, url_hint: str = "") -> str:
    msg = f"{type(e).__name__}: {e}"
    if isinstance(e, urllib.error.HTTPError) and e.code == 403:
        host = urllib.parse.urlsplit(url_hint).netloc if url_hint else "the host"
        msg += (f" — if this is a cloud run, {host} is probably not in the environment's "
                f"allowed domains (Network access → Custom)")
    return msg


# --------------------------------------------------------------------------- pubmed

def _text(el) -> str:
    return " ".join("".join(el.itertext()).split()) if el is not None else ""


def parse_pubmed_article(art) -> dict | None:
    mc = art.find("MedlineCitation")
    if mc is None or mc.find("Article") is None:
        return None
    a = mc.find("Article")
    pmid = (mc.findtext("PMID") or "").strip()

    abstract = []
    for t in a.findall("Abstract/AbstractText"):
        label, txt = t.get("Label"), _text(t)
        abstract.append(f"{label}: {txt}" if label else txt)

    authors = []
    for au in a.findall("AuthorList/Author"):
        if au.findtext("LastName"):
            authors.append(f"{au.findtext('LastName')} {au.findtext('Initials') or ''}".strip())
        elif au.findtext("CollectiveName"):
            authors.append(au.findtext("CollectiveName"))

    pubdate = a.find("Journal/JournalIssue/PubDate")
    year = ""
    if pubdate is not None:
        year = pubdate.findtext("Year") or pubdate.findtext("MedlineDate") or ""

    ids = {aid.get("IdType"): (aid.text or "").strip()
           for aid in art.findall("PubmedData/ArticleIdList/ArticleId")}
    doi = ids.get("doi", "")
    if not doi:
        for e in a.findall("ELocationID"):
            if e.get("EIdType") == "doi":
                doi = (e.text or "").strip()

    return {
        "source": "pubmed",
        "pmid": pmid,
        "doi": doi,
        "pmcid": ids.get("pmc", ""),
        "title": _text(a.find("ArticleTitle")),
        "authors": ", ".join(authors[:3]) + (" et al." if len(authors) > 3 else ""),
        "journal": a.findtext("Journal/ISOAbbreviation") or a.findtext("Journal/Title") or "",
        "year": year,
        "pub_types": [t.text for t in a.findall("PublicationTypeList/PublicationType") if t.text],
        "abstract": "\n".join(abstract),
        "url": f"https://pubmed.ncbi.nlm.nih.gov/{pmid}/",
    }


class PubMed:
    def __init__(self, email: str | None, api_key: str | None) -> None:
        self.base = {"tool": TOOL}
        if email:
            self.base["email"] = email
        if api_key:
            self.base["api_key"] = api_key
        self.delay = 0.11 if api_key else 0.34  # NCBI: 10 req/s with key, 3 without
        self._last = 0.0

    def _call(self, endpoint: str, params: dict, post: bool = False) -> bytes:
        wait = self._last + self.delay - time.monotonic()
        if wait > 0:
            time.sleep(wait)
        p = {**self.base, **params}
        try:
            url = f"{EUTILS}/{endpoint}"
            return http_request(url, data=p) if post else http_request(url, params=p)
        finally:
            self._last = time.monotonic()

    def search(self, term: str, mindate: str, maxdate: str, max_results: int):
        ids: list[str] = []
        retstart, count, translation, warnings = 0, 0, "", None
        while len(ids) < max_results:
            raw = self._call("esearch.fcgi", {
                "db": "pubmed", "term": term, "retmode": "json",
                "retmax": min(500, max_results - len(ids)), "retstart": retstart,
                "datetype": "edat", "mindate": mindate, "maxdate": maxdate,
            })
            res = json.loads(raw)["esearchresult"]
            if "ERROR" in res:
                raise RuntimeError(f"PubMed: {res['ERROR']}")
            count = int(res.get("count", 0))
            translation = res.get("querytranslation", translation)
            warnings = res.get("warninglist") or res.get("errorlist") or warnings
            batch = res.get("idlist", [])
            ids.extend(batch)
            retstart += len(batch)
            if not batch or len(ids) >= count:
                break
        return count, ids, translation, warnings

    def fetch(self, pmids: list[str]) -> list[dict]:
        out = []
        for i in range(0, len(pmids), 200):
            raw = self._call("efetch.fcgi", {"db": "pubmed", "id": ",".join(pmids[i:i + 200]),
                                             "retmode": "xml"}, post=True)
            for art in ET.fromstring(raw).findall("PubmedArticle"):
                parsed = parse_pubmed_article(art)
                if parsed:
                    out.append(parsed)
        return out


def pubmed_window(search_id: str, version, runs: list[dict], settings: dict,
                  today: dt.date, since: dt.date | None) -> tuple[str, str]:
    """Start a few days before the last successful window ended (PubMed indexing lag);
    duplicates from the overlap are removed by de-duplication."""
    if since:
        start = since
    else:
        last = None
        for r in runs:  # chronological
            if (r.get("search") == search_id and r.get("source") == "pubmed"
                    and r.get("version") == version and r.get("status") == "ok"):
                last = r
        if last:
            end = dt.date.fromisoformat(last["maxdate"].replace("/", "-"))
            start = end - dt.timedelta(days=int(settings.get("overlap_days", 3)))
        else:
            start = today - dt.timedelta(days=int(settings.get("initial_lookback_days", 30)))
    return start.strftime("%Y/%m/%d"), today.strftime("%Y/%m/%d")


# --------------------------------------------------------------------------- feeds

def strip_html(s: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", s or "")).split())


def fetch_feed(url: str, keywords: list[str] | None) -> tuple[int, list[dict]]:
    import feedparser

    parsed = feedparser.parse(http_request(url))
    if parsed.bozo and not parsed.entries:
        raise RuntimeError(f"could not parse feed: {parsed.get('bozo_exception')}")
    kws = [k.lower() for k in (keywords or [])]
    items = []
    for e in parsed.entries:
        title = " ".join((e.get("title") or "").split())
        summary = strip_html(e.get("summary") or "")
        if kws and not any(k in f"{title} {summary}".lower() for k in kws):
            continue
        link = e.get("link") or e.get("id") or ""
        if not link:
            continue
        doi = ""
        for cand in (e.get("prism_doi"), e.get("dc_identifier"), e.get("id"), link, summary):
            m = DOI_PATTERN.search(cand or "")
            if m:
                doi = m.group(0).rstrip(".,;)")
                break
        items.append({"source": "feed", "feed": url, "url": link, "doi": doi, "title": title,
                      "summary": summary[:2000],
                      "published": e.get("published") or e.get("updated") or ""})
    return len(parsed.entries), items


# --------------------------------------------------------------------------- commands

def store_records(items: list[dict], run_id: str, today: dt.date) -> list[dict]:
    return [{"keys": it["keys"], "searches": it["searches"], "run": run_id,
             "first_seen": today.isoformat(), "source": it["source"],
             "title": it.get("title", ""), "url": it.get("url", "")} for it in items]


def cmd_fetch(args) -> int:
    cfg = load_config()
    settings = cfg["settings"]
    all_ids = {s["id"] for s in cfg["searches"]}
    if args.only:
        unknown = set(args.only) - all_ids
        if unknown:
            sys.exit(f"unknown search id(s): {', '.join(sorted(unknown))}")
        searches = [s for s in cfg["searches"] if s["id"] in set(args.only)]
    else:
        searches = [s for s in cfg["searches"] if s.get("enabled", True)]

    since = dt.date.fromisoformat(args.since) if args.since else None
    now = dt.datetime.now(dt.timezone.utc)
    today = now.date()
    run_id = now.strftime("%Y%m%d-%H%M%S")
    runs = read_jsonl(RUNS_LOG)
    seen = load_seen()
    found = RunItems()
    records: list[dict] = []
    pubmed: PubMed | None = None
    max_results = int(settings.get("max_results", 500))

    for s in searches:
        sid, version = s["id"], s.get("version", 1)
        base = {"run": run_id, "time": now.isoformat(timespec="seconds"), "search": sid,
                "version": version}

        query = (s.get("pubmed") or {}).get("query")
        if query:
            if pubmed is None:
                pubmed = PubMed(settings.get("email") or os.environ.get("NCBI_EMAIL"),
                                os.environ.get("NCBI_API_KEY"))
            mindate, maxdate = pubmed_window(sid, version, runs, settings, today, since)
            rec = dict(base, source="pubmed", query=query, mindate=mindate, maxdate=maxdate)
            if since:
                rec["since_override"] = args.since
            try:
                count, ids, translation, warnings = pubmed.search(query, mindate, maxdate,
                                                                  max_results)
                new = found.collect(pubmed.fetch(ids) if ids else [], sid, seen)
                rec.update(status="ok", count=count, retrieved=len(ids), new=new,
                           translation=translation)
                if count > len(ids):
                    rec["truncated"] = True
                if warnings:
                    rec["warnings"] = warnings
            except Exception as e:  # noqa: BLE001 — record and continue with other sources
                rec.update(status="error", error=describe_error(e, EUTILS))
            records.append(rec)

        for feed in s.get("feeds") or []:
            rec = dict(base, source="feed", query=feed)
            try:
                total, items = fetch_feed(feed, s.get("feed_keywords"))
                rec.update(status="ok", count=total, matched=len(items),
                           new=found.collect(items, sid, seen))
            except Exception as e:  # noqa: BLE001
                rec.update(status="error", error=describe_error(e, feed))
            records.append(rec)

    errors = [r for r in records if r["status"] != "ok"]
    summary = {
        "run_id": run_id,
        "dry_run": args.dry_run,
        "searches": [s["id"] for s in searches],
        "new_items": len(found.items),
        "per_source": [{k: r.get(k) for k in ("search", "source", "query", "mindate", "maxdate",
                                              "count", "new", "truncated", "status", "error")
                        if r.get(k) is not None} for r in records],
        "web_queries_to_run": [{"search": s["id"], "query": q}
                               for s in searches for q in (s.get("web") or [])],
    }

    if not args.dry_run:
        write_json(RUNS_DIR / run_id / "new_items.json", found.items)
        append_jsonl(ITEMS, store_records(found.items, run_id, today))
        append_jsonl(RUNS_LOG, records)
        summary["new_items_file"] = str((RUNS_DIR / run_id / "new_items.json").relative_to(ROOT))
    elif args.verbose:
        summary["items"] = found.items

    print(json.dumps(summary, ensure_ascii=False, indent=2))
    if errors:
        print(f"\nWARNING: {len(errors)} source(s) failed; see per_source.", file=sys.stderr)
        return EXIT_PARTIAL
    return 0


def cmd_add(args) -> int:
    cfg = load_config()
    search = next((s for s in cfg["searches"] if s["id"] == args.search), None)
    if search is None:
        sys.exit(f"unknown search id: {args.search}")
    run_dir = RUNS_DIR / args.run
    new_file = run_dir / "new_items.json"
    if not new_file.exists():
        sys.exit(f"no such run: {args.run} (run `fetch` first)")

    hits = json.loads(Path(args.file).read_text(encoding="utf-8"))
    candidates = [{"source": "web", "url": h["url"], "doi": h.get("doi", ""),
                   "title": " ".join((h.get("title") or "").split()),
                   "summary": h.get("snippet", ""), "published": h.get("date", "")}
                  for h in hits if h.get("url")]
    for c in candidates:
        if not c["doi"]:
            m = DOI_PATTERN.search(c["url"])
            c["doi"] = m.group(0).rstrip(".,;)") if m else ""

    found = RunItems()
    new = found.collect(candidates, args.search, load_seen())
    now = dt.datetime.now(dt.timezone.utc)

    existing = json.loads(new_file.read_text(encoding="utf-8"))
    write_json(new_file, existing + found.items)
    append_jsonl(ITEMS, store_records(found.items, args.run, now.date()))
    append_jsonl(RUNS_LOG, [{"run": args.run, "time": now.isoformat(timespec="seconds"),
                             "search": args.search, "version": search.get("version", 1),
                             "source": "web", "query": args.query, "count": len(hits),
                             "new": new, "status": "ok"}])
    print(json.dumps({"run_id": args.run, "search": args.search, "query": args.query,
                      "hits": len(hits), "new": new}, indent=2))
    return 0


def cmd_status(args) -> int:
    cfg = load_config()
    runs, items = read_jsonl(RUNS_LOG), read_jsonl(ITEMS)
    print(f"{'search':28} {'ver':>3} {'on':>3} {'last ok run':22} {'items':>6}  last error")
    for s in cfg["searches"]:
        sid = s["id"]
        mine = [r for r in runs if r.get("search") == sid]
        ok = [r["time"] for r in mine if r.get("status") == "ok"]
        last_err = next((r for r in reversed(mine) if r.get("status") != "ok"), None)
        err = ""
        if last_err and (not ok or last_err["time"] >= ok[-1]):
            err = f"{last_err['source']}: {last_err.get('error', '')[:60]}"
        n = sum(1 for it in items if sid in it.get("searches", []))
        print(f"{sid:28} {s.get('version', 1):>3} {'y' if s.get('enabled', True) else 'n':>3} "
              f"{(ok[-1] if ok else 'never'):22} {n:>6}  {err}")
    print(f"\n{len(items)} items in store, {len({r['run'] for r in runs})} runs logged.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    f = sub.add_parser("fetch", help="run searches and record new items")
    f.add_argument("--only", nargs="+", metavar="ID", help="run only these search ids")
    f.add_argument("--since", metavar="YYYY-MM-DD", help="override PubMed window start (backfill)")
    f.add_argument("--dry-run", action="store_true", help="query sources but write nothing")
    f.add_argument("-v", "--verbose", action="store_true", help="with --dry-run, print items")
    f.set_defaults(func=cmd_fetch)

    a = sub.add_parser("add", help="record web-search hits for a run")
    a.add_argument("--run", required=True)
    a.add_argument("--search", required=True)
    a.add_argument("--query", required=True, help="the exact web query that produced the hits")
    a.add_argument("--file", required=True, help="JSON list of {url, title, snippet, date, doi}")
    a.set_defaults(func=cmd_add)

    st = sub.add_parser("status", help="last run per search and item counts")
    st.set_defaults(func=cmd_status)

    args = ap.parse_args()
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
