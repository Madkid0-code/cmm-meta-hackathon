# CMM — Candidate Marketing Machine

**CMM works for the job seeker — it markets you, and it never lies for you.**

The zero-hallucination career agent with deterministic guardrails.

AI resume tools invent skills, beat the ATS, and get the job seeker destroyed in
the technical interview. CMM is the antidote: a multi-agent system where
Meta's Muse Spark agents do the reading and the drafting, and a deterministic
Python verifier — never an LLM — decides what the seeker may truthfully claim.

> The LLM proposes. The code disposes.

And it finishes the job: when you click **Apply** on a role that passed,
CMM's Applier goes and submits the application for you, in your own
signed-in session, and brings back the employer's confirmation. Nothing
is ever submitted without your click.

## The complete pipeline

CMM is not a "paste a JD" toy. It runs the same end-to-end model as the
production system it grows out of — the job seeker fills in **one form**, and
the machine does the rest:

```
FORM ──► sweep config ──► SWEEPER ──► job_posting_raw ──► PARSER
     ──► job_postings ──► keep rule ──► READER ──► VERIFIER ──► matches
     ──► BUILDER ──► candidate_resumes ──► APPLIER ──► pursuits (outcome loop)
```

Each stage writes only its own table (SQLite, CMM-shaped schema) — an
immutable handoff chain, exactly like production.

1. **The form** — the seeker attests their skills *with evidence*, sets hard
   boundaries ("never used Snowpark"), and says what to shop for: keyword
   phrases, match skills, location, contract-only, recency window. The form
   *is* the candidate record; the shopper step turns it into a sweep config.
2. **Sweeper fleet** — config-driven collectors, two sources, each reached
   the way that source actually allows:
   - **Dice — plain code, in the product.** The sweeper reads Dice's search
     and detail pages directly with the config's filters. No login, no AI.
   - **LinkedIn — a Muse browser agent, signed in as the seeker.** LinkedIn's
     real search (contract filter, last-24-hours, full job descriptions)
     exists only inside a member's signed-in session; its guest endpoint
     returns ID-only stubs and cannot filter for contract work, so it was
     retired. Instead, a **scout** — a Muse browser agent working in the
     seeker's own signed-in LinkedIn session, read-only, capped, with an
     identity guard and a hard stop at any security checkpoint — runs the
     config's filters and deposits full job records into the store's
     `linkedin_inbox/`. The sweeper ingests those deposits like any other
     source (keyword- and recency-checked, deduped). No credentials ever
     enter the code or the store; the session lives only in the browser.
     See *The agent layer* below.
   (RemoteOK and Arbeitnow were removed 2026-10-28 — thin, mostly
   non-contract feeds. Their parsers remain so stored rows still parse.)
   One blocked source never stops the fleet: the doctor line reports it
   and the sweep continues. Raw postings land untouched in
   `job_posting_raw`, deduped on (source, external id).
3. **Parser** — raw payloads become normalized postings with full JD text.
   Second dedupe layer: company + title.
4. **Keep rule** (production rule) — a posting survives only if at least 2 of
   the seeker's match skills appear in it. Everything else is dropped before
   any LLM token is spent.
5. **Reader Agent** (Muse Spark, Meta Model API) — extracts each surviving
   job's core technical constraints into structured JSON. No prose, no
   judgement.
6. **Deterministic Verifier** (Python, no LLM) — diffs every extracted
   constraint against the attested profile using canonical term identity
   (word-boundary matching, explicit aliases). `Snowpark` is not `PySpark`,
   and the verifier knows it. The match verdict — PASS or SKIP — is a
   deterministic rule over the split, never a model opinion and never a
   score. Hardened by a dedicated trap suite: Java ≠ JavaScript, one
   documented bridge only (PySpark attests Spark — never Spark Streaming),
   requirements deduped by canonical term, and metrics attested as
   number+unit pairs ("5 years" in a profile does not license "5%";
   `10×`, `$5M`, `5M records`, `2-fold` are all caught).
7. **Builder Agent** (Muse Spark) — for each PASS match, drafts a
   tailored narrative using only attested claims, reconciling the seeker's
   raw resume against the attested boundary (no *hallucination laundering*:
   a human's mild exaggeration must not leave as a concrete false claim).
   The Verifier checks every line: any unattested term or invented metric
   triggers a **REJECT payload** and a rewrite, in the open.
8. **Resume builder** — joins match + posting + attested profile into one
   complete tailored resume per PASS match (`candidate_resumes`): the
   guardrail-passed summary, only this job's defensible skills with their
   evidence, and the profile's evidence quoted verbatim — never rephrased
   into a stronger claim. The assembled resume passes the deterministic
   guardrail one final time before it exists. A match without a built
   resume is never eligible for the applier — the production rule.
9. **Applier** — the click is yours; the applying is CMM's. You see a role
   that passed, you click **Apply**, and the Applier goes and applies for
   you: the web UI records an **apply request** on your click, and a Muse
   browser-agent executor working in your own signed-in Dice session
   performs the submission and brings back the employer's confirmation
   verbatim. Behind that click, plain code does the bookkeeping: it queues
   a **pursuit** for every PASS match with a built resume (route from the
   posting's source: dice / linkedin / company_portal / board), assembles
   the complete application package (tailored resume + attested form
   answers + apply URL), and tracks the outcome loop: ready → prepared →
   applied / failed / expired, with the confirmation text recorded as
   evidence (`run_apply.py`). Routes that apply on a company's own site
   are reported honestly as package-only. One honest limit for this build:
   it never enters credentials and never submits as the seeker — the demo
   persona is fictional, and applying as a fictional person to real
   employers would be fraud. In production, this same stage hands off to
   signed-in per-route appliers on the seeker's own accounts.
10. **Evidence Dossier + Call Queue** — per match: **Defensible claims**
   (verified, each with its evidence) and **Vulnerable claims** (job
   requirements with no attested proof — claiming them is an interview
   liability) with a deterministic **Honest Pivot** built only from attested
   adjacent skills. There is no match score and no fit percentage, anywhere.
   The dossiers land in a call queue, and **the human decides**. The system
   never submits an application without your click, and never screens a
   candidate for an employer. It works for one person only: the job seeker.

A single-JD mode (`run_demo.py`) also exists for the reproducible fixture
case, and the **Intake Audit** runs in both modes: the seeker's own outgoing
resume is checked by the same deterministic guardrail before any agent runs —
the demo's climax is the machine catching the *human's* exaggerations.

## The agent layer — who does what, and where

CMM is deliberately split by *what kind of worker a job needs*. Nothing
here is "the AI does everything":

| Stage | Powered by | Where it runs |
|---|---|---|
| Sweeper — Dice | Plain code | Inside the product |
| Sweeper — LinkedIn (**the Scout**) | **Muse browser agent** in the seeker's own signed-in session | The seeker's browser session; deposits land in the product's `linkedin_inbox/` |
| Parser · Keep rule · **Verifier** | Plain code — never an LLM | Inside the product |
| Reader · Builder | **Muse Spark 1.3** (Meta Model API) | Called by the product |
| Applier — packages & outcome tracking | Plain code | Inside the product |
| Applier — the submission itself | **Muse browser agent** on the seeker's own signed-in session, on the seeker's click | The seeker's browser session |
| The final decision | **The human** | — |

Why Muse browser agents at all? Because two moments in a job search can only
happen *as the member*: LinkedIn's contract-filtered, hours-fresh search
exists only inside a signed-in session, and a Dice application can only
be submitted by the signed-in seeker. An API key cannot be a member. So
the parts that need an identity are done by a Muse browser agent sitting in
the seeker's own session — read-only while scouting, acting only on an
explicit Apply click while applying, identity-checked before it moves,
and stopped cold by any security checkpoint.

In this repository, the agent layer is represented by its two contracts:
the scout **deposit format** (`linkedin_inbox/*.json`, ingested by
`attested/sweeper.py`) and the **apply-request flag** the UI sets for the
executor. In the live demo those seats are worked by **Muse browser
agents** (Meta's Muse agent platform) so the behaviour can be watched
end to end — Muse Spark does the thinking; Muse browser agents do the
acting. In the product, the
Scout ships as a small add-on in the seeker's own browser — the session
never leaves the seeker's machine, and no LinkedIn password is ever
shared with or stored by CMM. (For the done-for-you service model, the
same scout runs in a per-candidate isolated browser profile; profiles
never mix.)

**Portal connections, independently confirmed (built Oct 31).** The
"Your accounts" Connect buttons follow the same rule. Pressing Connect
opens the portal's own sign-in page in a new tab in the seeker's
browser; the seeker signs in there themselves. With the **CMM Scout**
helper paired (a one-time code, issued in Your accounts; source in
`extension/`, downloadable from the site as `scout.zip`), the helper
reads the account name the portal displays *in that browser* and posts
it to `/api/connect-confirm` — the card flips to "✓ Connected as
\<name\>" with **no agent and no saved login anywhere near it**. The
pairing code is the only credential, and it identifies the browser,
never a portal account. Without Scout paired, a Muse browser agent
performs the same confirmation in the seeker's signed-in session —
the fallback, not the design.

## Why it is built this way

An LLM that grades its own honesty will eventually invent a skill. So the
verdict in CMM is 100% deterministic code — the same design rule battle
tested in the production system this project grows out of (see below). The
agents are constrained Actor–Critic style: Muse Spark generates, the code
verifies, and the reject-and-rewrite loop is *visible* in a live event stream
with a full audit trail of every caught hallucination.

## Run it

Stdlib-only Python 3. No dependencies.

```bash
export META_MODEL_API_KEY=<your Meta Model API key>

# Complete flow: form -> sweeper -> parser -> verdict -> dossiers -> call queue
python3 run_flow.py demo/form_alex.json examples_out/flow

# Single-JD fixture mode:
python3 run_demo.py --jd demo/jd_snowpark.md --profile demo/profile_attested.md --out examples_out
```

Outputs: per-mode `events.jsonl` (the full agent event stream, including
every guardrail rejection); in flow mode, `attested.db` (the CMM-shaped
store), `matches/<id>/dossier.md` per built match, and `call_queue.md` —
the human's decision list. In fixture mode, `dossier.md` and `drafts/`
(each Builder draft, accepted or rejected).

Run the tests (no API key needed):

```bash
python3 -m unittest discover -s tests -v
```

## Mission Control — the web UI

One screen over the whole flow, stdlib-only (`http.server` + vanilla JS,
no external assets), reading the pipeline's own store — every number on
screen is a number the pipeline actually produced:

```bash
export META_MODEL_API_KEY=<your key>   # only needed for the Run buttons
python3 run_web.py 8765                # http://127.0.0.1:8765
```

- **One login = one person** — an account belongs to exactly one seeker,
  so there is no "try another profile" switch inside a login. The
  onboarding form (name, skills with evidence, hard boundaries, an
  optional resume upload the Safety Check audits, search phrases, match
  skills, sources, job type and workplace) creates the account's own
  store; afterwards the seeker edits the two halves independently from
  the sidebar: **Your profile** (the person — *Save my profile*) and
  **Your search** (the sweep — *Save my search*). Neither save can
  clobber the other.
- **Upload first, profile fills in**: Your profile opens with a resume
  upload. The file is read on the server (`attested/ingest.py`) and
  audited against the attested profile by the same deterministic
  guardrail the pipeline runs at intake (`verifier.check_draft`) — the
  page shows the claims struck and the attested skills the resume
  confirms, and the name read from the resume is filled into the
  editor. The upload becomes the form's raw resume, so the next scan's
  intake check re-runs the identical audit in the open. Uploading never
  edits the profile by itself: the human reviews and attests with
  *Save my profile*.
- **Reset demo**: the board carries a *⟲ Reset demo* button that wipes
  the run — postings, matches, built resumes, pursuits, generated
  dossiers, the event feed — back to a blank board, so a fresh run (or
  a demo take) starts from nothing. The account itself (form, attested
  profile, scout deposits) is not a run and is never wiped. On the
  judge demo link, Reset goes further: it also restores the sandbox
  profile and search to their starting state and disconnects the
  portals, so every judge starts from the same clean slate.
- **Built resumes are deliverables**: every tailored resume downloads as
  an editable **Word (.docx)** or **PDF** (generated stdlib-only —
  `attested/export.py`), from the resume tab of its dossier.
- **Stop, honestly**: while a run is working the UI says **Running** and
  offers **Stop** — which asks one question: *keep the results so far*
  (the store saves every step; a later scan continues where it stopped)
  or *discard everything this run produced* (a ride's store is removed;
  a scan's new rows are rolled back by run snapshot). Runs halt
  cooperatively between steps — never mid-write.
- **Stat cards with a period filter** — jobs scanned, roles audited,
  passed, resumes built, applications prepared, and **applied**, counted
  by ET calendar day with a Today / Yesterday / This month / All time
  filter (today resets at midnight ET by construction; the board itself
  is never filtered, so waiting work never disappears overnight).
- **Your pipeline board** — four columns that mirror the outcome loop:
  Found (full job cards, expandable postings) → Resume Builder (one row
  per role: the job on the left, its tailored resume on the right — or
  the plain-words reason no resume was built) → Ready to Apply (the
  Apply button) → Outcome (applied with the employer's verbatim
  confirmation, in-flight, or not-applied with the honest reason).
- **🧠 How CMM works** — a page inside the product that explains the
  machine to the seeker (and to judges): every worker, what powers it
  (plain code / Muse Spark / Muse browser agent / the human), and a live
  count from the account's own pipeline on each step.
- **Live event stream** — every agent and guardrail event, actor-colored,
  with Muse Spark's reasoning progress visible while it thinks. Two live
  actions: evaluate the demo JD end-to-end in real time, or resume the
  full sweep against the same store.
- **Live Market Targets** — the judged roles (never "candidates", never
  "screening"): Title @ Company, verdict chip, defensible/vulnerable
  counts, pursuit status. A PASS row opens the **Evidence Dossier as claim
  cards** (Defensible with evidence; Vulnerable with reason + Honest
  Pivot), the **tailored resume** and the **application package** as
  documents, a **hallucination-intercept badge** built from the recorded
  audit trail, and the closing line: *My call — the human decides.*

## Models

- Reader + Builder: `muse-spark-1.3` via the Meta Model API
  (`https://api.meta.ai/v1/chat/completions`, OpenAI-compatible).
- Muse Spark spends completion tokens on hidden reasoning first, so all
  calls use generous `max_tokens`; reasoning token counts are surfaced in
  the event stream so latency reads as computation, not a stall.

## Production-proven architecture, clean hackathon implementation

CMM (this build) was built during the Meta Global AI Developer Hackathon
(Oct 26 – Nov 4, 2026), Startup track, theme "Build for Your World".

| | What it is |
|---|---|
| **Architectural heritage (pre-existing)** | The production CMM (Candidate Marketing Machine), a private multi-agent job-search system in daily use before the hackathon. It contributed *operational lessons only*: the deterministic-verdict rule, the attestation concept, and the verification-probe pattern, all proven against real job-market traffic. **No CMM code, data, or candidate information is in this repository.** |
| **Hackathon build (Oct 26 – Nov 4)** | Everything in this repository, written from scratch during the event: a 100% new stdlib-only Python codebase, the first implementation of these ideas on the Meta Model API (muse-spark-1.3), the onboarding-form → sweep-config model, the multi-source sweeper fleet and parser, the CMM-shaped SQLite handoff chain, the Reader and Builder agents, the deterministic verifier and its term-identity rules, the Intake Audit module, the reject-and-rewrite loop, the Evidence Dossier renderer, the call queue, the verifiable event-stream, the demo cases, and all tests. |

## Licence

© 2026 Sharyaz Hafeez Mohammed — all rights reserved. Public for Meta hackathon judging only; see `LICENSE`. No reuse, copying, or commercial use without written permission.
