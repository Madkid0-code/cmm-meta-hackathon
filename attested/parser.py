"""Parser: job_posting_raw -> job_postings. Writes ONLY its own table.

Normalizes each source's payload into the shared posting shape and turns
description HTML into plain JD text. Also runs the second dedupe layer
(company + normalized title), like production.
"""
import html as html_mod
import json
import re
from datetime import datetime, timezone


def html_to_text(raw_html):
    if not raw_html:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", raw_html, flags=re.S | re.I)
    text = re.sub(r"<br\s*/?>|</p>|</li>|</div>", "\n", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html_mod.unescape(text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n", text)
    return text.strip()


def classify(employment_type="", location="", source="", text=""):
    """Normalize a posting's job type and workplace from whatever the
    source actually states. 'unknown' is an honest answer — the flow's
    type filter keeps unknowns rather than guessing."""
    et = (employment_type or "").lower()
    blob = f"{location or ''} {(text or '')[:400]}".lower()
    if "part" in et:
        job_type = "part_time"
    elif "contract" in et or "temp" in et:
        job_type = "contract"
    elif "full" in et:
        job_type = "full_time"
    elif "part-time" in blob or "part time" in blob:
        job_type = "part_time"
    elif "contract" in blob:
        job_type = "contract"
    elif "full-time" in blob or "full time" in blob:
        job_type = "full_time"
    else:
        job_type = "unknown"
    loc = (location or "").lower()
    # Strong remote markers in the JD's opening lines count too — a
    # remote contract whose location field just says "United States"
    # (LinkedIn's habit) is remote work, not on-site. Deliberately
    # narrow phrases: a passing mention like "1 day/week remote" inside
    # an on-site role must NOT flip it.
    remote_markers = ("remote contract", "location: remote",
                      "fully remote", "100% remote", "distance remote",
                      "remote position", "remote role", "remote job",
                      "workplace: remote", "remote, us", "remote (us")
    if source == "remoteok" or "remote" in loc \
            or any(m in blob for m in remote_markers):
        workplace = "remote"
    elif "hybrid" in loc or "hybrid" in blob:
        workplace = "hybrid"
    elif loc.strip():
        workplace = "onsite"
    else:
        workplace = "unknown"
    return job_type, workplace


def _dice(row):
    payload = json.loads(row["raw"])
    card = payload.get("card", {})
    jd = html_to_text(payload.get("description_html", "")) or card.get("summary", "")
    loc = (card.get("jobLocation") or {}).get("displayName", "")
    title = card.get("title") or (card.get("summary", "").split("\n")[0][:120])
    return dict(
        title=title, company=card.get("companyName", ""), location=loc,
        employment_type=card.get("employmentType", ""),
        posted_at=card.get("postedDate", ""), apply_url=card.get("detailsPageUrl", ""),
        jd_text=jd)


def _remoteok(row):
    p = json.loads(row["raw"])
    return dict(
        title=p.get("position", ""), company=p.get("company", ""),
        location=p.get("location", ""), employment_type="",
        posted_at=p.get("date", ""), apply_url=p.get("url", ""),
        jd_text=html_to_text(p.get("description", "")))


def _arbeitnow(row):
    p = json.loads(row["raw"])
    ts = p.get("created_at")
    posted = (datetime.fromtimestamp(ts, tz=timezone.utc).isoformat(timespec="seconds")
              if isinstance(ts, (int, float)) else "")
    return dict(
        title=p.get("title", ""), company=p.get("company_name", ""),
        location=p.get("location", ""), employment_type="",
        posted_at=posted, apply_url=p.get("url", ""),
        jd_text=html_to_text(p.get("description", "")))


def _linkedin_guest(row):
    p = json.loads(row["raw"])
    return dict(
        title="", company="", location="", employment_type="", posted_at="",
        apply_url=row["url"] or "", jd_text="")


def _linkedin(row):
    """Signed-in scout deposit: full job records, JD included — nothing
    like the guest source's ID-only stubs. When the scout captured
    LinkedIn's workplace badge (Remote / Hybrid / On-site), it rides at
    the front of the location so classify() reads the stated workplace
    instead of guessing it from the city."""
    p = json.loads(row["raw"])
    jd = p.get("description_text") or html_to_text(p.get("description_html", ""))
    location = p.get("location", "")
    workplace = (p.get("workplace") or "").strip()
    if workplace and workplace.lower() not in location.lower():
        location = f"{workplace} — {location}" if location else workplace
    return dict(
        title=p.get("title", ""), company=p.get("company", ""),
        location=location,
        employment_type=p.get("employment_type", ""),
        posted_at=p.get("posted_at", ""),
        apply_url=p.get("apply_url") or row["url"] or "",
        jd_text=jd)


PARSERS = {
    "dice": _dice,
    "remoteok": _remoteok,
    "arbeitnow": _arbeitnow,
    "linkedin_guest": _linkedin_guest,
    "linkedin": _linkedin,
}


def _norm(s):
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def parse_all(db, store, emit):
    rows = store.unparsed_raw(db)
    made, dupes, thin = 0, 0, 0
    seen_keys = {
        (_norm(r["company"]), _norm(r["title"]))
        for r in db.execute("SELECT company, title FROM job_postings").fetchall()
    }
    for row in rows:
        parser = PARSERS.get(row["source"])
        if not parser:
            continue
        fields = parser(row)
        key = (_norm(fields["company"]), _norm(fields["title"]))
        if fields["title"] and key in seen_keys:
            dupes += 1
            # still record the posting row so the raw row is not re-processed;
            # it points at the same normalized job, dedupe layer 2.
            store.insert_posting(db, row["id"], row["source"], fields["title"],
                                 fields["company"], fields["location"],
                                 fields["employment_type"], fields["posted_at"],
                                 fields["apply_url"], fields["jd_text"])
            continue
        seen_keys.add(key)
        pid = store.insert_posting(db, row["id"], row["source"], fields["title"],
                                   fields["company"], fields["location"],
                                   fields["employment_type"], fields["posted_at"],
                                   fields["apply_url"], fields["jd_text"])
        if pid:
            made += 1
            if len(fields["jd_text"]) < 200:
                thin += 1
    emit("parser", f"parsed {made} postings ({dupes} company+title dupes, {thin} thin JDs)")
    return {"parsed": made, "dupes": dupes, "thin": thin}


def keyword_hits(posting, match_skills):
    """Production keep rule: how many of the seeker's match skills appear?
    'sql' matches as a substring (production rule); everything else needs a
    word boundary, so 'spark' never rides in on 'pyspark' text it isn't in."""
    text = f"{posting['title'] or ''}\n{posting['jd_text'] or ''}".lower()
    hits = []
    for skill in match_skills:
        s = skill.lower()
        if s == "sql":
            found = "sql" in text
        else:
            found = re.search(r"(?<![a-z0-9])" + re.escape(s) + r"(?![a-z0-9])", text)
        if found:
            hits.append(skill)
    return hits
