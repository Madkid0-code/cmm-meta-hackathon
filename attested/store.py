"""SQLite store mirroring the production CMM handoff chain.

Table-for-table the same shape as the production system:
    sweep_configs -> job_posting_raw -> job_postings -> candidate_matches

Immutable-handoff rule, same as production: each stage writes ONLY its own
table. Nothing is updated in place; a stage's output is the next stage's input.
"""
import json
import os
import sqlite3
from datetime import datetime, timezone

SCHEMA = """
CREATE TABLE IF NOT EXISTS sweep_configs (
    id INTEGER PRIMARY KEY,
    candidate TEXT NOT NULL,
    keywords TEXT NOT NULL,          -- JSON list of search phrases
    match_skills TEXT NOT NULL,      -- JSON list; keep rule = >=2 present in JD
    location TEXT NOT NULL,
    contract_only INTEGER NOT NULL DEFAULT 0,
    posted_within_hours INTEGER NOT NULL DEFAULT 24,
    sources TEXT NOT NULL,           -- JSON list of source names
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS job_posting_raw (
    id INTEGER PRIMARY KEY,
    source TEXT NOT NULL,
    external_id TEXT NOT NULL,
    url TEXT,
    fetched_at TEXT NOT NULL,
    raw TEXT NOT NULL,               -- JSON payload exactly as the source gave it
    UNIQUE(source, external_id)
);
CREATE TABLE IF NOT EXISTS job_postings (
    id INTEGER PRIMARY KEY,
    raw_id INTEGER NOT NULL REFERENCES job_posting_raw(id),
    source TEXT NOT NULL,
    title TEXT,
    company TEXT,
    location TEXT,
    employment_type TEXT,
    posted_at TEXT,
    apply_url TEXT,
    jd_text TEXT,
    UNIQUE(raw_id)
);
CREATE TABLE IF NOT EXISTS candidate_matches (
    id INTEGER PRIMARY KEY,
    posting_id INTEGER NOT NULL REFERENCES job_postings(id),
    candidate TEXT NOT NULL,
    verdict TEXT NOT NULL,           -- PASS | SKIP
    defensible INTEGER NOT NULL,
    vulnerable INTEGER NOT NULL,
    detail TEXT NOT NULL,            -- JSON: split + reason
    created_at TEXT NOT NULL,
    UNIQUE(posting_id, candidate)
);
CREATE TABLE IF NOT EXISTS candidate_resumes (
    id INTEGER PRIMARY KEY,
    posting_id INTEGER NOT NULL REFERENCES job_postings(id),
    candidate TEXT NOT NULL,
    resume_path TEXT NOT NULL,
    guardrail TEXT NOT NULL,         -- PASSED | REJECTED
    created_at TEXT NOT NULL,
    UNIQUE(posting_id, candidate)
);
CREATE TABLE IF NOT EXISTS pursuits (
    id INTEGER PRIMARY KEY,
    posting_id INTEGER NOT NULL REFERENCES job_postings(id),
    candidate TEXT NOT NULL,
    route TEXT NOT NULL,             -- dice | linkedin | company_portal | board
    status TEXT NOT NULL,            -- ready | prepared | applied | failed | expired
    resume_id INTEGER REFERENCES candidate_resumes(id),
    apply_url TEXT,
    detail TEXT,                     -- outcome evidence (e.g. quoted confirmation)
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE(posting_id, candidate)
);
"""


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(path):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def add_config(db, form):
    """The 'shopper' step: a candidate form becomes a sweep config row."""
    cur = db.execute(
        "INSERT INTO sweep_configs (candidate, keywords, match_skills, location,"
        " contract_only, posted_within_hours, sources, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (
            form["name"],
            json.dumps(form["search"]["keywords"]),
            json.dumps(form["search"]["match_skills"]),
            form["search"].get("location", "United States"),
            1 if form["search"].get("contract_only") else 0,
            int(form["search"].get("posted_within_hours", 24)),
            json.dumps(form["search"].get("sources", ["dice", "remoteok", "arbeitnow"])),
            now_iso(),
        ),
    )
    db.commit()
    return cur.lastrowid


def active_configs(db):
    return db.execute("SELECT * FROM sweep_configs WHERE active=1").fetchall()


def insert_raw(db, source, external_id, url, raw_text):
    """Returns (raw_id, is_new). Dedupe is on (source, external_id)."""
    cur = db.execute(
        "INSERT OR IGNORE INTO job_posting_raw (source, external_id, url, fetched_at, raw)"
        " VALUES (?, ?, ?, ?, ?)",
        (source, external_id, url, now_iso(), raw_text),
    )
    db.commit()
    if cur.rowcount:
        return cur.lastrowid, True
    row = db.execute(
        "SELECT id FROM job_posting_raw WHERE source=? AND external_id=?",
        (source, external_id),
    ).fetchone()
    return row["id"], False


def unparsed_raw(db):
    return db.execute(
        "SELECT r.* FROM job_posting_raw r"
        " LEFT JOIN job_postings p ON p.raw_id = r.id WHERE p.id IS NULL"
    ).fetchall()


def insert_posting(db, raw_id, source, title, company, location, employment_type,
                   posted_at, apply_url, jd_text):
    cur = db.execute(
        "INSERT OR IGNORE INTO job_postings (raw_id, source, title, company, location,"
        " employment_type, posted_at, apply_url, jd_text)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (raw_id, source, title, company, location, employment_type, posted_at,
         apply_url, jd_text),
    )
    db.commit()
    return cur.lastrowid if cur.rowcount else None


def postings_without_match(db, candidate):
    return db.execute(
        "SELECT p.* FROM job_postings p LEFT JOIN candidate_matches m"
        " ON m.posting_id = p.id AND m.candidate = ? WHERE m.id IS NULL",
        (candidate,),
    ).fetchall()


def insert_match(db, posting_id, candidate, verdict, defensible, vulnerable, detail):
    cur = db.execute(
        "INSERT OR IGNORE INTO candidate_matches (posting_id, candidate, verdict,"
        " defensible, vulnerable, detail, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (posting_id, candidate, verdict, defensible, vulnerable,
         json.dumps(detail), now_iso()),
    )
    db.commit()
    return cur.lastrowid if cur.rowcount else None


def insert_resume(db, posting_id, candidate, resume_path, guardrail):
    cur = db.execute(
        "INSERT OR IGNORE INTO candidate_resumes (posting_id, candidate,"
        " resume_path, guardrail, created_at) VALUES (?, ?, ?, ?, ?)",
        (posting_id, candidate, resume_path, guardrail, now_iso()),
    )
    db.commit()
    if cur.rowcount:
        return cur.lastrowid
    row = db.execute(
        "SELECT id FROM candidate_resumes WHERE posting_id=? AND candidate=?",
        (posting_id, candidate)).fetchone()
    return row["id"]


def resume_for(db, posting_id, candidate):
    return db.execute(
        "SELECT * FROM candidate_resumes WHERE posting_id=? AND candidate=?",
        (posting_id, candidate)).fetchone()


def insert_pursuit(db, posting_id, candidate, route, resume_id, apply_url):
    cur = db.execute(
        "INSERT OR IGNORE INTO pursuits (posting_id, candidate, route, status,"
        " resume_id, apply_url, created_at, updated_at)"
        " VALUES (?, ?, ?, 'ready', ?, ?, ?, ?)",
        (posting_id, candidate, route, resume_id, apply_url, now_iso(), now_iso()),
    )
    db.commit()
    return cur.lastrowid if cur.rowcount else None


def set_pursuit_status(db, pursuit_id, status, detail=None):
    db.execute(
        "UPDATE pursuits SET status=?, detail=COALESCE(?, detail), updated_at=?"
        " WHERE id=?",
        (status, detail, now_iso(), pursuit_id))
    db.commit()


def pursuits_for(db, candidate):
    return db.execute(
        "SELECT pu.*, p.title, p.company, p.source FROM pursuits pu"
        " JOIN job_postings p ON p.id = pu.posting_id"
        " WHERE pu.candidate=? ORDER BY pu.id", (candidate,)).fetchall()
