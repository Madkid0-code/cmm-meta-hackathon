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

Sources (all public, no login, no credentials):
- dice:      server-rendered search payload + public detail pages.
- remoteok:  public JSON API.
- arbeitnow: public JSON API.
- linkedin_guest: public guest endpoint; large platforms rate-limit
  datacenter IPs, so this source is attempt-and-degrade by design.
"""
import json
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


def dice_search(config, per_keyword=6, detail_cap=10):
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


def remoteok_search(config):
    data = json.loads(fetch("https://remoteok.com/api"))
    out = []
    for job in data[1:]:
        if not _within(job.get("date", ""), int(config["posted_within_hours"]) * 7):
            continue  # remoteok feed is thin; window widened deliberately
        out.append(job)
    return out


def arbeitnow_search(config):
    data = json.loads(fetch("https://www.arbeitnow.com/api/job-board-api"))
    return [
        j for j in data.get("data", [])
        if _within(j.get("created_at", 0), int(config["posted_within_hours"]) * 7)
    ]


# ---------------------------------------------------------------- LinkedIn guest

def linkedin_guest_search(config):
    q = urllib.parse.urlencode({
        "keywords": json.loads(config["keywords"])[0],
        "location": config["location"],
        "f_TPR": f"r{int(config['posted_within_hours']) * 3600}",
        "start": 0,
    })
    html = fetch(
        f"https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search?{q}")
    jobs = []
    for m in re.finditer(r'data-entity-urn="urn:li:jobPosting:(\d+)"', html):
        jobs.append({"job_id": m.group(1)})
    return jobs


SOURCES = {
    "dice": (dice_search, lambda p: p["card"].get("guid") or p["card"].get("id"),
             lambda p: p["card"].get("detailsPageUrl")),
    "remoteok": (remoteok_search, lambda p: str(p.get("id")),
                 lambda p: p.get("url")),
    "arbeitnow": (arbeitnow_search, lambda p: p.get("slug") or p.get("url"),
                  lambda p: p.get("url")),
    "linkedin_guest": (linkedin_guest_search, lambda p: p.get("job_id"),
                       lambda p: f"https://www.linkedin.com/jobs/view/{p.get('job_id')}"),
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
            payloads = fn(config)
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
    return counts
