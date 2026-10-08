#!/usr/bin/env python3
"""Build the static website (site/) from digests/, runs/, state/ and searches.yaml.

Read-only with respect to the monitor's data: this script never touches state/, runs/ or digests/.
Usage: python scripts/build_site.py [--out site]
"""
import argparse
import html
import json
import re
import shutil
from collections import Counter
from pathlib import Path

import markdown
import yaml

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "site_src"
REL = ("practice-changing", "high", "medium", "low")


def jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


def run_date(run_id):
    return f"{run_id[0:4]}-{run_id[4:6]}-{run_id[6:8]}"


def esc(s):
    return html.escape(str(s), quote=True)


def load_triage():
    """{run_id: {key: {relevance, reason}}} and {run_id: n_new_items}."""
    triage, n_new = {}, {}
    for d in sorted((ROOT / "runs").glob("*/")):
        t, n = d / "triage.json", d / "new_items.json"
        if t.exists():
            triage[d.name] = json.loads(t.read_text(encoding="utf-8"))
        if n.exists():
            n_new[d.name] = len(json.loads(n.read_text(encoding="utf-8")))
    return triage, n_new


URL_RE = re.compile(r'(?<![">=/\w])(https?://[^\s<]+?)(?=[.,;)]*(?:\s|<|$))')


def linkify(body):
    """Digests carry bare URLs; Markdown does not autolink them."""
    return URL_RE.sub(lambda m: f'<a href="{m.group(1)}" rel="noopener">{m.group(1)}</a>', body)


def page(template, **kw):
    out = template
    for k, v in kw.items():
        out = out.replace("{{" + k + "}}", v)
    return out


def nav(active, depth):
    base = "../" * depth
    links = [("index", "Digests", "index.html"), ("pc", "Practice-changing", "practice-changing.html"),
             ("items", "All items", "items.html"), ("searches", "Searches", "searches.html")]
    return "".join(
        f'<a href="{base}{href}"' + (' aria-current="page"' if key == active else "") + f">{label}</a>"
        for key, label, href in links
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "site"))
    out = Path(ap.parse_args().out)
    if out.exists():
        shutil.rmtree(out)
    (out / "digests").mkdir(parents=True)
    shutil.copy(SRC / "style.css", out / "style.css")
    shutil.copy(SRC / "app.js", out / "app.js")
    (out / ".nojekyll").write_text("")
    template = (SRC / "page.html").read_text(encoding="utf-8")

    cfg = yaml.safe_load((ROOT / "searches.yaml").read_text(encoding="utf-8"))
    searches = cfg["searches"]
    sid_name = {s["id"]: s["name"] for s in searches}
    items = jsonl(ROOT / "state" / "items.jsonl")
    runs = jsonl(ROOT / "state" / "runs.jsonl")
    triage, n_new = load_triage()

    # --- items.json ------------------------------------------------------------------
    sid_list = sorted({s for it in items for s in it["searches"]})
    sid_idx = {s: i for i, s in enumerate(sid_list)}
    rel_counts = Counter()
    rows = []
    for it in items:
        t = triage.get(it["run"], {}).get(it["keys"][0], {})
        rel = t.get("relevance", "")
        rel_counts[rel or "unrated"] += 1
        rows.append([rel, it["title"], [sid_idx[s] for s in it["searches"]], it["source"],
                     it["first_seen"], it["url"], it["run"]])
    rows.sort(key=lambda r: REL.index(r[0]) if r[0] in REL else len(REL))  # stable: top tier first within a date
    rows.sort(key=lambda r: r[4], reverse=True)
    (out / "items.json").write_text(
        json.dumps({"searches": [{"id": s, "name": sid_name.get(s, s)} for s in sid_list], "rows": rows},
                   ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # --- digests ---------------------------------------------------------------------
    md = markdown.Markdown(extensions=["sane_lists", "tables"])
    digests = []
    for p in sorted((ROOT / "digests").glob("*.md"), reverse=True):
        run_id = p.stem
        counts = Counter(v["relevance"] for v in triage.get(run_id, {}).values())
        digests.append({"id": run_id, "date": run_date(run_id), "new": n_new.get(run_id, 0),
                        "pc": counts["practice-changing"], "high": counts["high"], "medium": counts["medium"], "low": counts["low"]})
        md.reset()
        body = md.convert(p.read_text(encoding="utf-8"))
        body = linkify(body)
        body = re.sub(r"<h2>(Practice-changing|High|Medium|Low)\b",
                      lambda m: f'<h2 class="rel-{m.group(1).lower()}">{m.group(1)}', body)
        title = f"Digest {run_id}"
        crumbs = '<p class="meta"><a href="../index.html">All digests</a> · run <code>%s</code></p>' % esc(run_id)
        (out / "digests" / f"{run_id}.html").write_text(
            page(template, title=esc(title), nav=nav("index", 1), base="../",
                 content=crumbs + f'<div class="digest">{body}</div>'), encoding="utf-8")

    # --- index -----------------------------------------------------------------------
    total_hi = sum(d["high"] for d in digests)
    stats = (
        f'<div class="stats"><div><b>{len(items):,}</b>items recorded</div>'
        f'<div><b>{len(runs):,}</b>query executions logged</div>'
        f'<div><b>{sum(1 for s in searches if s.get("enabled", True))}</b>active searches</div>'
        f'<div><b>{rel_counts["practice-changing"]:,}</b>practice-changing items</div>'
        f'<div><b>{rel_counts["high"]:,}</b>high-relevance items</div></div>'
    )
    latest = digests[0] if digests else None
    rows_html = "".join(
        f'<tr><td class="m"><a href="digests/{d["id"]}.html">{d["id"]}</a></td><td class="m">{d["date"]}</td>'
        f'<td class="m num">{d["new"]:,}</td>'
        f'<td class="num"><span class="pill practice-changing">{d["pc"]}</span></td>'
        f'<td class="num"><span class="pill high">{d["high"]}</span></td>'
        f'<td class="num"><span class="pill medium">{d["medium"]}</span></td>'
        f'<td class="num"><span class="pill low">{d["low"]}</span></td></tr>'
        for d in digests
    )
    lead = (f'<p class="meta">Latest digest: <a href="digests/{latest["id"]}.html">{latest["id"]}</a> '
            f'({latest["date"]}), {latest["new"]} new items, {latest["pc"]} practice-changing, {latest["high"]} high.</p>') if latest else ""
    content = (
        "<h1>Lung Cancer Monitor</h1>"
        '<p class="meta">Recurring PubMed, ClinicalTrials.gov, preprint and web searches, triaged against the '
        'criteria on the <a href="searches.html">Searches</a> page. Each run is one digest.</p>'
        + stats + lead +
        '<div class="tbl"><table><thead><tr><th>Run</th><th>Date</th><th class="num">New</th>'
        '<th class="num">Practice-changing</th><th class="num">High</th><th class="num">Medium</th><th class="num">Low</th></tr></thead>'
        f"<tbody>{rows_html}</tbody></table></div>"
    )
    (out / "index.html").write_text(
        page(template, title="Lung Cancer Monitor", nav=nav("index", 0), base="", content=content), encoding="utf-8")

    # --- practice-changing index ----------------------------------------------------
    pc_md = ROOT / "practice_changing.md"
    md.reset()
    body = md.convert(pc_md.read_text(encoding="utf-8")) if pc_md.exists() else "<h1>Practice-changing items</h1>"
    body = re.sub(r'href="digests/([\w-]+)\.md"', r'href="digests/\1.html"', linkify(body))
    (out / "practice-changing.html").write_text(
        page(template, title="Practice-changing items", nav=nav("pc", 0), base="",
             content=f'<div class="digest">{body}</div>'), encoding="utf-8")

    # --- items page ------------------------------------------------------------------
    content = (
        "<h1>All items</h1>"
        '<p class="meta">Every item the monitor has recorded, with the relevance it was triaged at.</p>'
        '<div class="filters">'
        '<input id="q" type="search" placeholder="Search titles, PMIDs, NCT ids" aria-label="Search items">'
        '<select id="fs" aria-label="Search filter"><option value="">All searches</option></select>'
        '<select id="fr" aria-label="Relevance filter"><option value="">Any relevance</option>'
        '<option>practice-changing</option><option>high</option><option>medium</option><option>low</option></select>'
        '<select id="fsrc" aria-label="Source filter"><option value="">Any source</option></select></div>'
        '<div class="tbl"><table><thead><tr><th>Relevance</th><th>Title</th><th>Search</th><th>Source</th>'
        '<th>First seen</th><th>Run</th></tr></thead><tbody id="rows"></tbody></table></div>'
        '<p class="count" id="count">Loading items…</p>'
        '<p><button type="button" id="more" class="btn" hidden>Show more</button></p>'
    )
    (out / "items.html").write_text(
        page(template, title="All items", nav=nav("items", 0), base="", content=content), encoding="utf-8")

    # --- searches page ---------------------------------------------------------------
    cards = []
    for s in searches:
        state = "" if s.get("enabled", True) else ' <span class="chip">disabled</span>'
        srcs = [k for k in ("pubmed", "clinicaltrials", "feeds", "web") if k in s]
        cards.append(
            f'<div class="card"><h3>{esc(s["name"])}{state}</h3>'
            f'<span class="id">{esc(s["id"])} · v{s.get("version", 1)} · {esc(", ".join(srcs))}</span>'
            f'<p>{esc(" ".join(str(s.get("criteria", "")).split()))}</p>'
            + (f'<p><b>Practice-changing:</b> {esc(" ".join(str(s["practice_changing"]).split()))}</p>'
               if s.get("practice_changing") else "") + "</div>"
        )
    content = ("<h1>Searches</h1>"
               '<p class="meta">Rendered from <code>searches.yaml</code>. The criteria are what each relevance rating is judged against.</p>'
               + (f'<div class="card"><h3>Practice-changing bar (all searches)</h3>'
                  f'<p>{esc(" ".join(str(cfg["practice_changing"]).split()))}</p></div>' if cfg.get("practice_changing") else "") +
               f'<div class="cards">{"".join(cards)}</div>')
    (out / "searches.html").write_text(
        page(template, title="Searches", nav=nav("searches", 0), base="", content=content), encoding="utf-8")

    print(f"built {out}: {len(digests)} digests, {len(items)} items, {len(searches)} searches")


if __name__ == "__main__":
    main()
