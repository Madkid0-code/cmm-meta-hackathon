"""The sweeper fleet — config-driven, multi-source, writes ONLY raw rows.

Mirrors the production CMM sweeper model:
- A sweep config (built from the seeker's form) says WHAT to shop for:
  keyword phrases, match skills, location, contract-only, recency window.
- Each source is an independent collector. A source that fails or is blocked
  is reported by the doctor line and the sweep continues with the rest —
  one bad source never stops the fleet.
- Everything found lands in job_posting_raw exactly as the source gave it.
  Dedupe is (source, external_id); a second layer (company + title) runs in
  the parser, like production.

Sources (owner-directed, 2026-10-28 — two sources, both strong):
- dice:      server-rendered search payload + public detail pages.
- linkedin:  SIGNED-IN LinkedIn, the advanced source. The guest endpoint
  returns ID-only stubs and cannot filter for contract jobs, so it was
  retired. Instead, a signed-in scout (a Muse Wonder agent working in the
  seeker's own LinkedIn session — the production CMM pattern) searches
  with the Contract + Past-24h filters applied and deposits full job
  records (title, company, location, type, JD text) as fragment files
  in <store>/linkedin_inbox/. This collector ingests those deposits.
  No credentials ever enter this codebase or the store; the session
  lives only in the scout's browser.

RemoteOK and Arbeitnow were removed on the owner's order (thin,
mostly non-contract feeds). Their parsers remain in parser.py so rows
already in a store still parse; they are never fetched again.
"""
import json
import os
import re
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

UA = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/130 Safari/537.36"
    )
}


def fetch(url, timeout=25):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


# ---------------------------------------------------------------- Dice

def _extract_joblist(html):
    """Pull the embedded search-results JSON array out of the Dice page."""
    text = html.replace('\\"', '"')
    marker = '"jobList":{"data":'
    i = text.find(marker)
    if i < 0:
        return []
    start = text.find("[", i)
    depth, in_str, esc = 0, False, False
    for j in range(start, len(text)):
        c = text[j]
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
        elif c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:j + 1])
                except json.JSONDecodeError:
                    return []
    return []


def _dice_description(html):
    """Extract the job-description div by balancing <div> depth."""
    m = re.search(r'<div[^>]*jobDescription[^>]*>', html)
    if not m:
        return ""
    start = m.end()
    depth, pos = 1, start
    tag_re = re.compile(r"</?div\b[^>]*>", re.I)
    while True:
        t = tag_re.search(html, pos)
        if not t:
            return html[start:]
        if t.group(0).startswith("</"):
            depth -= 1
            if depth == 0:
                return html[start:t.start()]
        elif not t.group(0).endswith("/>"):
            depth += 1
        pos = t.end()


def dice_search(config, db=None, per_keyword=6, detail_cap=10):
    """Returns list of raw payload dicts (card + full description HTML)."""
    posted_map = {12: "ONE", 24: "ONE", 48: "THREE", 72: "THREE",
                  168: "SEVEN"}
    posted = posted_map.get(int(config["posted_within_hours"]), "ONE")
    out, seen, details = [], set(), 0
    for phrase in json.loads(config["keywords"]):
        q = urllib.parse.urlencode({
            "q": phrase,
            "location": config["location"],
            "filters.postedDate": posted,
            **({"filters.employmentType": "CONTRACTS"} if config["contract_only"] else {}),
        })
        cards = _extract_joblist(fetch(f"https://www.dice.com/jobs?{q}"))
        for card in cards[:per_keyword]:
            ext = card.get("guid") or card.get("id")
            if not ext or ext in seen:
                continue
            seen.add(ext)
            payload = {"card": card, "description_html": ""}
            if details < detail_cap and card.get("detailsPageUrl"):
                try:
                    payload["description_html"] = _dice_description(
                        fetch(card["detailsPageUrl"]))
                    details += 1
                except Exception:
                    pass  # card alone still lands in raw; parser copes
            out.append(payload)
    return out


# ---------------------------------------------------------------- JSON APIs

def _within(iso_or_ts, hours):
    try:
        if isinstance(iso_or_ts, (int, float)):
            dt = datetime.fromtimestamp(iso_or_ts, tz=timezone.utc)
        else:
            dt = datetime.fromisoformat(iso_or_ts.replace("Z", "+00:00"))
    except Exception:
        return True
    return dt >= datetime.now(timezone.utc) - timedelta(hours=hours)


def _store_dir(db):
    """Directory holding the store's database file (the inbox lives
    beside it). Falls back to the cwd for in-memory stores."""
    try:
        for row in db.execute("PRAGMA database_list").fetchall():
            if row[1] == "main" and row[2]:
                return os.path.dirname(os.path.abspath(row[2]))
    except Exception:
        pass
    return os.getcwd()


def linkedin_search(config, db=None):
    """Ingest signed-in scout deposits from <store>/linkedin_inbox/.

    Each fragment is one scout run: {"searched_keywords": [...],
    "jobs": [{job_id, title, company, location, employment_type,
    posted_at, apply_url, description_text}]}. Fragments whose searched
    keywords do not overlap this config's keywords are skipped (a stale
    deposit from an older search must not leak into a new one). Deposits
    stay on disk after ingestion — insert_raw dedupes on
    (source, external_id), so re-reading a fragment is harmless.
    """
    inbox = os.path.join(_store_dir(db), "linkedin_inbox")
    if not os.path.isdir(inbox):
        return []
    wanted = {k.strip().lower() for k in json.loads(config["keywords"])}
    hours = int(config["posted_within_hours"])
    out, seen = [], set()
    for name in sorted(os.listdir(inbox)):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(inbox, name)) as f:
                frag = json.load(f)
        except Exception:
            continue
        frag_keys = {str(k).strip().lower()
                     for k in frag.get("searched_keywords") or []}
        if frag_keys and not (frag_keys & wanted):
            continue
        for job in frag.get("jobs") or []:
            ext = str(job.get("job_id") or "")
            if not ext or ext in seen:
                continue
            if job.get("posted_at") and not _within(job["posted_at"], hours):
                continue
            seen.add(ext)
            out.append(job)
    return out


SOURCES = {
    "dice": (dice_search, lambda p: p["card"].get("guid") or p["card"].get("id"),
             lambda p: p["card"].get("detailsPageUrl")),
    "linkedin": (linkedin_search, lambda p: str(p.get("job_id") or ""),
                 lambda p: p.get("apply_url")
                 or f"https://www.linkedin.com/jobs/view/{p.get('job_id')}"),
}


def sweep(db, store, config, emit):
    """Run every configured source; write raw rows; return per-source counts."""
    counts = {}
    for name in json.loads(config["sources"]):
        if name not in SOURCES:
            emit("sweeper", f"DOCTOR: unknown source '{name}' skipped")
            continue
        fn, ext_of, url_of = SOURCES[name]
        try:
            payloads = fn(config, db)
        except Exception as e:
            emit("sweeper", f"DOCTOR: source {name} unavailable ({type(e).__name__}) — fleet continues")
            counts[name] = 0
            continue
        new = 0
        for p in payloads:
            ext = ext_of(p)
            if not ext:
                continue
            _, is_new = store.insert_raw(db, name, str(ext), url_of(p), json.dumps(p))
            new += 1 if is_new else 0
        counts[name] = new
        emit("sweeper", f"{name}: {len(payloads)} fetched, {new} new raw postings")
        if name == "linkedin" and not payloads:
            emit("sweeper", "DOCTOR: linkedin — no signed-in scout deposit "
                            "in the inbox yet; the signed-in scout pulls "
                            "fresh contract jobs on the seeker's own "
                            "LinkedIn session and deposits them here")
    return counts
