# MASTER_IMPLEMENTATION_PLAN.md
## Speech Analytics & Compliance Prototype — Shaha Finlease

**Prepared as:** Complete end-to-end implementation plan (frontend, backend, AI pipeline, database, testing, delivery)
**Source of truth:** `Assignment Speech Analytics & Compliance (Prototype) – Shaha Finlease.pdf` (Oct 28, 2026, Kaustubh Manohar Upade)
**Supporting documents read:** `call_scripts.md` (5 sample call scripts), `Assignment Speech Analytics & Compliance (Prototype) – Shaha Finlease.md` (Markdown version of the PDF — confirmed identical in content)
**Timeline:** 2–3 days from receipt
**Plan status:** Complete planning document. No implementation performed yet.
**Last audited:** 2026-10-28

> **Working assumption, stated up front:** nothing in the source PDF is ambiguous about scope or ethics — this is a defensive compliance-monitoring tool built on consensual, synthetic role-play audio, explicitly prohibited from using real customer recordings. Every assumption below is marked `ASSUMPTION` and justified; everything else is a direct requirement traced back to the PDF.

---

## Table of Contents

1. [Complete Requirements Analysis](#1-complete-requirements-analysis)
2. [Technology Stack — Verified Free Tools](#2-technology-stack--verified-free-tools)
3. [Complete Frontend Plan](#3-complete-frontend-plan)
4. [Complete Backend Plan](#4-complete-backend-plan)
5. [AI and Audio-Processing Pipeline](#5-ai-and-audio-processing-pipeline)
6. [Database Design](#6-database-design)
7. [Compliance Rules and Scoring](#7-compliance-rules-and-scoring)
8. [Roles and Access Filtering](#8-roles-and-access-filtering)
9. [Analytics and Evaluation](#9-analytics-and-evaluation)
10. [Data Preparation and Test Strategy](#10-data-preparation-and-test-strategy)
11. [Repository Structure](#11-repository-structure)
12. [Ordered Phase-Wise Implementation Roadmap](#12-ordered-phase-wise-implementation-roadmap)
13. [Final Acceptance Checklist and Demo Script](#13-final-acceptance-checklist-and-demo-script)
14. [Appendix A — Key Assumptions](#appendix-a--key-assumptions)

> **AUDIT SUMMARY — corrections from the prior draft:**
> 1. **Mandatory LLM Selection:** GroqCloud `openai/gpt-oss-120b` is established as the **sole primary LLM** for speaker labelling, contextual compliance analysis (R2–R6), borrower sentiment, and agent calmness assessment. `llama-3.3-70b-versatile` is removed. Ollama is prohibited. No silent fallback or alternative LLM substitution is permitted.
> 2. **Hosted Transcription Only:** GroqCloud hosted `whisper-large-v3-turbo` is the **sole primary transcription service** (free tier: 20 RPM, 2,000 RPD, 7,200 audio-sec/hr). `faster-whisper` local model is completely removed. No local speech-to-text models or Ollama are used. Sarvam AI is documented strictly as a manual, opt-in hosted alternative with non-expiring trial credits (₹100–₹1,000), never automatically switched.
> 3. **Verified Free Tier & Rate Pacing:** GroqCloud free tier ($0, no credit card required) verified for both `openai/gpt-oss-120b` (30 RPM, 8,000 TPM, 1,000 RPD, 200,000 TPD) and `whisper-large-v3-turbo`. Because `openai/gpt-oss-120b` has an 8,000 TPM limit, sequential batch processing incorporates request pacing (≥20s delay between calls) to prevent HTTP 429 errors.
> 4. **Strict Error Reporting:** If `openai/gpt-oss-120b` quota is exhausted or the model is unavailable, the system reports explicit failure (`Call.status = 'failed'`, `failure_reason = 'LLM quota exceeded or model unavailable: openai/gpt-oss-120b'`). Any alternative model requires explicit user approval.
> 5. **R1 and segment_id:** R1 is deterministic Python metadata check (0 tokens). The `violation.segment_id` column is `NULLABLE`. R1 violations set `segment_id = NULL`. The `quote` for R1 is a descriptive string, not a transcript excerpt. `start_time_seconds = 0.0` for R1.
> 6. **Scoring consistency:** Confirmed a flag does NOT change the score. False alarm removes the deduction. Pending flags count at full weight. Score is recalculated and persisted atomically in the same transaction as the Review row insert.
> 7. **Ground truth methodology & R6 scope:** Precision/recall counts ALL violations (pending + confirmed + false_alarm) as "system flagged". R6 check scoped to first 2 agent turns only.

---

## 1. Complete Requirements Analysis

### 1.1 What this system is

A **demo-ready prototype** for Shaha Finlease, an RBI-registered NBFC that buys non-performing/written-off personal-loan and credit-card-loan portfolios and recovers dues via telecalling. The system:

- Ingests **finished call recordings** (not live calls — live monitoring is explicitly out of scope / bonus-only).
- Transcribes each call with **per-line Agent/Borrower labels**, across Hindi, Marathi, English, Kannada, often **code-mixed**.
- Detects **six compliance rule breaches** and **borrower sentiment** using an LLM.
- Produces a **0–100 compliance score** per call.
- Lets a **manager review and confirm/reject** each flag, with the score updating accordingly.
- Shows a **dashboard** with breach breakdowns and **accuracy numbers** (precision/recall per rule, WER).
- Supports **three role views with no login**: Caller, Manager, Head of Collections — a top switcher filters all visible data.

### 1.2 Users and roles (explicit)

| Role | Scope of visibility | Source |
|---|---|---|
| Caller | Own calls only | PDF §2, "a caller sees only their own calls" |
| Manager | Their team's calls | PDF §2, "a manager their team's" |
| Head of Collections | All three offices | PDF §2, "the head all three offices" |

Org facts (seed-data constraints, explicit): 3 offices (Jogeshwari, Lower Parel, Bangalore), 60 callers, 7 managers, 1 Head of Collections.

### 1.3 Mandatory features (all 8 are must-have, per PDF §4)

| # | Feature | "Done when" criterion (verbatim intent) |
|---|---|---|
| 1 | Role views | Switching role shows only that role's calls, flags, scores |
| 2 | Upload recordings | 20 audio files (WAV/MP3) + metadata CSV (call ID, caller, office, start time) process in one batch |
| 3 | Transcription | Hindi/English/mixed transcribed, each line labelled Agent/Borrower; WER reported on 3 hand-typed transcripts |
| 4 | Rule detection | Each flag shows rule code, timestamp, exact quote, severity |
| 5 | Call review page | Audio player beside transcript; clicking a flag plays audio from that moment |
| 6 | Sentiment | Each call shows borrower sentiment (positive/neutral/negative) and whether agent stayed calm, from text |
| 7 | Scorecard and review | Every call gets 0–100 score; manager marks each flag Confirmed/False alarm with comment; score updates |
| 8 | Dashboard and accuracy | Breaches by caller/office/rule; top-5 riskiest callers; precision/recall per rule on the 20 labelled calls |

### 1.4 Compliance rule set (exact, from PDF §3 — must be implemented exactly as specified)

| Code | Rule | Example breach | Severity |
|---|---|---|---|
| R1 | Calls only between 8 AM and 7 PM | Call starts at 7:40 PM (from metadata) | High |
| R2 | No abusive or foul language | Slurs or swearing in any language | Critical |
| R3 | No threats or intimidation | Police, jail, arrest, "we will come to your house" | Critical |
| R4 | No shaming or disclosure to third parties | Telling a relative or employer about the debt | Critical |
| R5 | Never ask for OTP, PIN, CVV or passwords | "Tell me the OTP you received" | High |
| R6 | Mandatory opening (name, company, purpose) | Agent omits name/company/purpose | Medium |

R1 is **deterministic** (metadata timestamp check). R2–R6 are **transcript/LLM-based** (phrasing varies by language; a keyword list alone will miss most breaches — PDF is explicit that this requires sending the transcript to an LLM with the rule table in the prompt, requesting structured JSON back).

### 1.5 Screens (exact, from PDF §5 — four screens, no more, no fewer for MVP)

1. **Upload** — batch audio + metadata CSV
2. **Call list** — filtered by active role view
3. **Call review** — audio player beside transcript, flags, scorecard, review buttons
4. **Dashboard** — accuracy numbers, breach breakdowns

### 1.6 Core data entities (exact, from PDF §5)

`Office`, `User` (role, office, manager), `Call` (caller, account, start time, duration, language, score), `TranscriptSegment` (speaker, start, end, text, sentiment), `Rule`, `Violation` (rule, segment, quote, severity), `Review` (decision, reviewer, comment, time).

Severities (exact, closed set): **Critical, High, Medium, Low.**

### 1.7 Data volume and test-set requirements (explicit)

- **~2,000 call records**, metadata-only, generated by a seed script (resettable demo).
- **20 of those calls** have real voice recordings (1–2 min each), Hindi/English/mixed, scripted and recorded as role-plays by the team — **no TTS, no real customer audio** (hard ethical/legal constraint stated twice in the PDF).
- **Hand-labelled ground truth** for all 20 calls: every rule must appear **at least twice** across the set; **at least 6 calls must be clean** (no violations).
- **5 unseen calls** brought on demo day, run live through the same pipeline.

### 1.8 Technical constraints (explicit)

- Stack is **suggested, not mandated**: Python + FastAPI, SQLite or PostgreSQL, server-rendered pages or Streamlit — but "any stack you can finish in 2–3 days is acceptable."
- Transcription: **use a hosted API** (GroqCloud hosted `whisper-large-v3-turbo` as primary; Sarvam AI as documented manual hosted fallback) — do **not** self-host/run transcription models or local Ollama.
- Use an **LLM** (GroqCloud `openai/gpt-oss-120b` as sole primary model) for speaker labelling and for contextual compliance (R2–R6) / sentiment detection.
- AI coding assistants are allowed, but the author must be able to explain every line.
- **API keys must never enter the repository.** Groq API key is kept exclusively on the backend.

### 1.9 Deliverables (explicit)

- Git repository
- README: setup steps, assumptions, per-rule accuracy numbers
- 20 labelled voice recordings
- 10-minute live demo

### 1.10 Explicit demo story (used later to drive the acceptance checklist)

> Upload a batch → open a call → jump straight to a flagged threat → play that moment → confirm it as manager → see caller's score and dashboard update → switch to caller view (shows only their own calls) → run 5 unseen calls.

### 1.11 Judging criteria (explicit — these become non-negotiable acceptance gates)

1. The demo story works without errors.
2. Each role view shows **only** what it should.
3. Critical breaches are caught **without flooding managers with false alarms**.
4. Accuracy numbers are **reported honestly** (including "insufficient sample size" where true).
5. Flags point to the **exact moment** (timestamp-accurate).
6. Code is clean enough to explain.

### 1.12 Explicit three-day plan (constrains the roadmap in §12)

| Day | Focus | End-of-day state |
|---|---|---|
| 1 | Data model, seed script, record+label 20 calls, upload, transcription | 20 calls transcribed with Agent/Borrower labels |
| 2 | Rule detection, sentiment, scorecard, call review page | Every call has flags and a score; clicking a flag plays that moment |
| 3 | Manager review, role views, dashboard, accuracy on test set, README, demo practice | Precision/recall shown; one-command start; demo ready |

### 1.13 Bonus features — explicitly out of scope until all 8 mandatory features work

- Live monitoring via streamed audio chunks
- Raised-voice detection from audio (not just text)
- Real login with hashed passwords

**This plan treats these as Phase 4 / stretch only** (see §12.5). No roadmap task before that phase may depend on them.

### 1.14 Mandatory vs. inferred vs. optional — explicit separation

**Mandatory (PDF-stated, non-negotiable):**
All 8 features in §1.3; the exact 6 rules in §1.4; the 4 screens in §1.5; the 7 entities in §1.6; the 4-severity set; role-filtered no-login switcher; hosted transcription API; LLM-based rule/sentiment detection; WER + precision/recall reporting; one-command seed/reset; README with honest assumptions.

**Inferred (reasonable engineering decisions needed to satisfy the above, not dictated by the PDF):**
Specific framework choices, exact DB schema types/indexes, exact scoring formula weights, exact component boundaries, specific LLM prompt structure, specific retry/error-handling policy, specific file-storage layout, specific chart library.

Each inferred decision is marked `ASSUMPTION` where it first appears, with its rationale, so it can be challenged or swapped without re-deriving the whole plan.

**Optional (explicitly bonus, deferred):**
Live streaming monitoring, raised-voice/audio-based tone detection, real authentication with hashed passwords.

### 1.15 Requirements-to-Implementation Traceability Matrix

| Req ID | Requirement (source) | Implementing component(s) | Verified by |
|---|---|---|---|
| RQ-01 | Role switcher, no login (§2, §8 in PDF) | `RoleSwitcher` component, `X-Role`/`X-User-Id` request context, backend query filters | Manual test: switch role, assert call-list count changes per §8.4 |
| RQ-02 | Batch upload: audio + metadata CSV (§4.2) | `UploadPage`, `POST /api/uploads/batch`, `upload_service.py` | Upload 20 files + CSV, assert all rows created with `status=queued` |
| RQ-03 | Transcription, Agent/Borrower labels (§4.3) | `transcription_service.py` (Groq hosted `whisper-large-v3-turbo`) + `llm_service.py` (Groq `openai/gpt-oss-120b` speaker labelling) | WER computed on 3 hand-typed transcripts (§9.6) |
| RQ-04 | Rule detection, quote+timestamp+severity (§4.4) | `compliance_service.py`, Groq `openai/gpt-oss-120b` structured JSON call, `Violation` table | Each of 20 labelled calls checked against ground truth (§9.7) |
| RQ-05 | Call review page: player beside transcript, click-flag-to-play (§4.5) | `CallReviewPage`, `AudioPlayer`, `TranscriptPanel`, `FlagList` | Manual test: click flag, assert player seeks to `violation.start_time` |
| RQ-06 | Sentiment + agent-calm flag (§4.6) | `compliance_service.py` (same LLM call as R2–R6), `Call.sentiment`, `Call.agent_stayed_calm` | Spot-check against 3 scripted calls with known tone |
| RQ-07 | Scorecard, manager Confirm/False-alarm + comment, score updates (§4.7) | `ReviewPanel`, `POST /api/violations/{id}/review`, `scoring_service.py` | Confirm a flag → assert score recalculates and persists |
| RQ-08 | Dashboard: breaches by caller/office/rule, top-5 riskiest, precision/recall (§4.8) | `DashboardPage`, `GET /api/analytics/*`, `evaluation_service.py` | Compare dashboard numbers to manually computed values on 20 calls |
| RQ-09 | R1–R6 exactly as specified (§3) | `rules.py` constants + `compliance_service.py` | Unit tests per rule (§10.3) |
| RQ-10 | 4 severities exactly: Critical/High/Medium/Low (§5) | `Violation.severity` enum | Schema constraint + seed data check |
| RQ-11 | 4 screens exactly (§5) | `/upload`, `/calls`, `/calls/:id`, `/dashboard` routes | Route inventory review |
| RQ-12 | 7 entities (§5) | SQLAlchemy models in `models.py` | Schema review against §6 |
| RQ-13 | ~2,000 metadata-only calls, 20 with audio (§5, §Data) | `seed_script.py` | Row count assertions post-seed |
| RQ-14 | 20 hand-labelled calls, every rule ≥2×, ≥6 clean (§5, §Test set) | `data/ground_truth.json` + manual labelling | Label-coverage check script (§10.1) |
| RQ-15 | 5 unseen calls on demo day (§5, §Demo story) | Same upload pipeline, no special-casing | Re-run upload flow with new files |
| RQ-16 | Hosted transcription API only, no self-hosted model (§6 Tech) | `transcription_service.py` calls Groq hosted Whisper API (`whisper-large-v3-turbo`); manual Sarvam AI fallback; zero local ASR or Ollama | Code review: no local ASR model loaded |
| RQ-17 | API keys never in repo (§6 Tech) | `.env`, `.env.example`, `.gitignore` | Repo scan for secrets before submission |
| RQ-18 | One-command start/reset (§Deliverables, §Judging) | `Makefile` / `docker-compose.yml` / `run.sh` | Fresh-clone smoke test |
| RQ-19 | README: setup, assumptions, per-rule accuracy (§Deliverables) | `README.md` | Review against §13.3 checklist |
| RQ-20 | No real customer recordings, no TTS (§Ground rules) | Process/documentation control, not code | README statement + recording review |

---

## 2. Technology Stack — Verified Free Tools

### 2.1 Chosen stack and why

| Layer | Choice | Rationale |
|---|---|---|
| Backend | **Python 3.11 + FastAPI** | PDF-suggested; async-friendly for calling hosted STT/LLM APIs concurrently; auto-generates OpenAPI schema |
| Database | **SQLite** for dev/demo, schema written to be **PostgreSQL-compatible** | 2,000 rows is trivial for SQLite; zero setup for a reviewer; `ASSUMPTION`: demo-day judges should not need to install Postgres |
| ORM / migrations | **SQLAlchemy 2.0 + Alembic** | Explicit schema control, repeatable seed/reset |
| Frontend | **React 18 + Vite + TypeScript** | SPA gives reliable audio-player-seek interaction (RQ-05) without page reloads. `ASSUMPTION`: Streamlit rejected — Streamlit's rerun model prevents "click flag → seek audio" without a full reload, which is a judged interaction |
| Styling | **Tailwind CSS + design-token layer** | Fast to build consistent, non-generic UI in the time budget |
| Charts | **Recharts** | Lightweight, composable, sufficient for bar charts |
| State/data-fetching | **TanStack Query** for server state, **React Context** for active role | Role state is small and global; everything else is server-cache-shaped |
| Audio playback | Native `<audio>` element with `useAudioPlayer` hook | No library needed; `audio.currentTime = seconds` handles seeking |
| Transcription (primary) | **GroqCloud hosted `whisper-large-v3-turbo`** — **VERIFIED free tier, no credit card** | Hosted API; 20 RPM, 2,000 RPD, max 25 MB per file, 7,200 audio-sec/hr (2 hrs/hr), 28,800 audio-sec/day (8 hrs/day). 20 calls × ~2 min avg = 40 min ≪ free limits. Hosted API, zero local model download. |
| Transcription (manual hosted alternative) | **Sarvam AI** (`saaras:v2` / `saaras:v1`) | Non-expiring signup/trial credits (₹100–₹1,000). Hosted API fallback if Groq STT fails or quota exhausted. Never automatically switched; requires manual configuration. ₹30/audio-hour. |
| LLM (primary and only) | **GroqCloud hosted `openai/gpt-oss-120b`** — **VERIFIED free tier, no credit card** | 30 RPM, 8,000 TPM, 1,000 RPD, 200,000 TPD. Context window: 131,072 tokens. Max completion: 65,536 tokens. Sole primary model for speaker labelling, compliance rules R2–R6, borrower sentiment, and agent calmness assessment. Request pacing enforced to respect 8,000 TPM limit. No silent substitution. |
| Speaker labelling | GroqCloud `openai/gpt-oss-120b` structured-JSON pass over raw transcript segments | Assigns Agent/Borrower turns based on conversational cues and opening identification while preserving STT timestamps |
| Compliance/sentiment LLM | GroqCloud `openai/gpt-oss-120b` structured-JSON pass (see §5.3) | Single call per transcript evaluating R2–R6, borrower sentiment, and agent calmness; R1 is evaluated deterministically in Python |
| Deployment | Local/single-VM demo: `docker-compose` with 2 services (api, web) + SQLite file volume | No infra budget in 2–3 days; one-command start per RQ-18 |

> **Free-tier clarity, billing verification, and rate pacing:**
> - **GroqCloud Free Tier:** Requires an account and API key from console.groq.com, but NO credit card or payment method. Both `whisper-large-v3-turbo` and `openai/gpt-oss-120b` are accessible on the Free Tier at $0 cost. Requests exceeding rate limits return HTTP 429 rather than billable charges (as long as no payment method is linked).
> - **Critical Free-Tier Rate Constraint:** `openai/gpt-oss-120b` has an 8,000 Tokens-Per-Minute (TPM) limit on the free tier. Since one call's transcript + prompt + rules table is ~2,500–3,500 tokens, batch ingestion of the 20 calls must be sequentially paced (e.g. minimum 20–25s delay between calls) or processed on demand to prevent HTTP 429 errors.
> - **Zero Local Models / No Ollama:** The assignment explicitly mandates a hosted transcription API and prohibits local ASR models. `faster-whisper` is completely eliminated, and local Ollama is strictly forbidden.
> - **Strict Model Integrity:** If `openai/gpt-oss-120b` returns a rate limit (HTTP 429) or is unavailable, the pipeline marks the call as `failed` with a clear message (`Call.status='failed'`, `failure_reason='LLM quota exceeded or model unavailable: openai/gpt-oss-120b'`). The system will NEVER silently substitute another model (e.g. `llama-3.3-70b-versatile`). Any model change must be an explicit decision approved by the user.

### 2.2 System diagram (text form)

```
┌─────────────────────┐        REST/JSON         ┌──────────────────────────┐
│   React SPA (Vite)  │ ───────────────────────▶ │   FastAPI backend        │
│  /upload /calls      │ ◀─────────────────────── │   (api/)                 │
│  /calls/:id /dashboard│      audio file URLs     │                          │
└─────────┬────────────┘                           │  routers/                │
          │ <audio src>                             │  services/               │
          │ GET /media/{call_id}.wav                │  models.py (SQLAlchemy)  │
          ▼                                         └──────────┬───────────────┘
   static audio files                                          │
   served by FastAPI                                           ▼
   (/media mount)                                     ┌─────────────────┐
                                                        │ SQLite/Postgres │
                                                        └─────────────────┘
                                                                 ▲
                              ┌──────────────────────────────────┴───────────┐
                              │  Background processing pipeline:              │
                              │  0. Python deterministic check → R1 violation │
                              │  1. Groq hosted Whisper STT → raw transcript  │
                              │  2. Groq gpt-oss-120b → TranscriptSegment rows│
                              │  3. Groq gpt-oss-120b → R2-R6 & Sentiment     │
                              │  4. Python scoring_service → Call.score       │
                              └───────────────────────────────────────────────┘
```

### 2.3 Component boundaries and data flow

1. **Upload boundary**: frontend never touches raw files after POST; backend stores them under `/data/audio/` and returns a `call_id`. All further frontend interaction is by `call_id`.
2. **Processing boundary**: transcription → labelling → compliance/sentiment → scoring is a **single backend pipeline function** (`pipeline.process_call(call_id)`), callable synchronously (upload-time, for 20 real calls — fast enough at this volume) or triggerable via a `POST /api/calls/{id}/reprocess` endpoint. `ASSUMPTION`: given only ~20 calls ever have real audio, a true async job queue (Celery/RQ) is overengineering per PDF's own "avoid unnecessary abstractions" instruction; a simple `BackgroundTasks` (FastAPI built-in) is sufficient and still non-blocking for the UI.
3. **API boundary**: frontend only ever calls the documented REST endpoints in §4.3 — no direct DB or LLM access from the browser.
4. **Role boundary**: role + user identity travel as request headers (`X-Role`, `X-User-Id`) set by the frontend's `RoleSwitcher`; backend is the **enforcement point** for filtering (§8), not just the frontend.

### 2.4 Configuration and secrets

- `.env` (gitignored) holds backend-only variables: `GROQ_API_KEY`, `GROQ_LLM_MODEL=openai/gpt-oss-120b`, `TRANSCRIPTION_PROVIDER=groq`, `GROQ_TRANSCRIPTION_MODEL=whisper-large-v3-turbo`, `DATABASE_URL` (and optional `SARVAM_API_KEY` for manual hosted fallback only). No API keys are exposed to the frontend.
- `.env.example` checked in with empty placeholders and explicit comments on free-tier limits.
- `pydantic-settings` `Settings` class loads and validates env vars at startup; app fails fast with a clear message if `GROQ_API_KEY` is missing — not a silent fallback to mock data or unauthorized models (important for RQ-20/§9.8 honesty).

### 2.5 Deployment and one-command startup

- `docker-compose.yml`: `api` (FastAPI + Uvicorn), `web` (Vite dev server or built static bundle served by Nginx).
- `make seed` → runs `scripts/seed_db.py` (resets DB, generates ~2,000 synthetic calls, loads the 20 real recordings + ground truth).
- `make dev` → `docker-compose up`.
- `ASSUMPTION`: judges will run this on their own machine during the 10-minute demo window, so **cold start must be under ~60 seconds** excluding first-time `docker pull`/`npm install`. This constrains §5 to avoid any heavy local model downloads.

### 2.6 Testing strategy overview (detail in §10)

Pyramid shape, biased toward the demo-critical path: unit tests for scoring/rule-matching logic (fast, no API calls), integration tests for API endpoints against a test SQLite DB, a handful of Playwright E2E tests for the actual demo story in §1.10, and a standalone **evaluation script** (not a unit test) that computes precision/recall/WER against the 20 labelled calls.


---

## 3. Complete Frontend Plan

### 3.1 Application shell, routing, navigation

```
/                     → redirect to /calls
/upload               → Upload
/calls                → Call List (role-filtered)
/calls/:callId        → Call Review
/dashboard             → Dashboard
```

**Top app bar (persistent across all routes):**
- Left: "Shaha Finlease — Compliance" wordmark (text, no logo asset needed).
- Center-left: primary nav — Calls | Upload | Dashboard (active route underlined).
- Right: **Role Switcher** — a 3-way segmented control: `Caller ▾ | Manager | Head of Collections`. When "Caller" or "Manager" is active, a second small dropdown appears to pick *which* caller/manager (since there's no login, the prototype needs a way to say "view as Priya the caller" vs "view as Rohan the manager"). `ASSUMPTION`: the PDF says "a caller sees only their own calls" — without login, the demo needs an explicit "view as" identity picker alongside the role; this is the simplest way to satisfy RQ-01 while staying true to "no login, not real authentication" (§8.3 PDF).

Switching role or identity: refetches call list, dashboard, and (if open) redirects away from a Call Review page the new identity can't access, back to `/calls`.

### 3.2 Design system

**Direction:** a trustworthy, dense, operations-console aesthetic — think fraud/compliance tooling, not a marketing dashboard. Flat surfaces, no gradients, no glassmorphism, restrained motion (150ms ease-out on state changes only).

**Color tokens:**

| Token | Hex | Use |
|---|---|---|
| `--surface-0` | `#F7F8FA` | App background |
| `--surface-1` | `#FFFFFF` | Cards, table rows |
| `--surface-2` | `#EEF1F5` | Hover/subtle fills |
| `--border` | `#DDE1E7` | Hairlines |
| `--text-primary` | `#13161B` | Body/headings |
| `--text-secondary` | `#5B6472` | Meta, labels |
| `--brand` | `#1F3A8F` | Primary actions, active nav, links |
| `--brand-hover` | `#16296B` | — |
| `--critical` | `#B3261E` | R2/R3/R4 severity, critical badges |
| `--high` | `#C45A00` | R1/R5 severity |
| `--medium` | `#9A7B00` | R6 severity |
| `--low` | `#5B6472` | Low severity (reserved; no current rule maps here but field exists per §1.6) |
| `--success` | `#1E7A34` | Confirmed-clean, calm agent, positive sentiment |
| `--warning-bg` | `#FFF4E5` | Banner backgrounds |
| `--error-bg` | `#FCEAE9` | Error banners |

Dark-mode variants are out of scope for the demo (`ASSUMPTION`: 2–3 day budget; single light theme only).

**Typography:** `Inter` (Google Fonts) for UI text, `IBM Plex Mono` for timestamps/durations/scores (tabular figures read better monospaced in a dense table). Scale: 12 / 13 / 14 (base) / 16 / 20 / 24 / 32px, line-height 1.4 for body, 1.2 for headings.

**Spacing scale:** 4 / 8 / 12 / 16 / 24 / 32 / 48px. Page content max-width 1440px, 24px side gutters, 16px grid gap in tables/cards.

**Radii/elevation:** 6px for inputs/buttons, 10px for cards, 1px hairline borders preferred over shadow; one shadow token (`0 1px 3px rgba(16,24,40,0.08)`) reserved for popovers/dialogs only.

**Severity badges:** pill shape, colored text on 10%-opacity tint of the severity color, uppercase 11px label (CRITICAL / HIGH / MEDIUM / LOW).

**Breakpoints:** desktop-first (`ASSUMPTION`: this is an internal ops tool used by managers at a desk, not a mobile app — PDF never mentions mobile). Full layouts at ≥1280px; a single tablet breakpoint at 768–1279px collapses the Call Review page's side-by-side player+transcript into a stacked layout; below 768px shows a "best viewed on desktop" notice rather than building a full mobile layout, to keep scope honest within the 2–3 day budget.

**Accessibility baseline:** all interactive elements keyboard-reachable and focus-ringed (`--brand` 2px outline); color is never the only signal for severity (badges always carry text, not just color); audio player has visible play/pause/seek controls (not custom-drawn without labels); tables use real `<table>` semantics; contrast ratios checked against WCAG AA for all token pairs above.

### 3.3 Shared/reusable components

| Component | Path | Responsibility | Variants/states |
|---|---|---|---|
| `AppShell` | `components/layout/AppShell.tsx` | Top bar, nav, role switcher, route outlet | — |
| `RoleSwitcher` | `components/layout/RoleSwitcher.tsx` | Role + identity selection, writes to `RoleContext` | loading (fetching identity list) |
| `SeverityBadge` | `components/ui/SeverityBadge.tsx` | Renders severity pill | critical/high/medium/low |
| `SentimentBadge` | `components/ui/SentimentBadge.tsx` | Renders sentiment pill | positive/neutral/negative |
| `ScoreGauge` | `components/ui/ScoreGauge.tsx` | 0–100 compliance score, color-ramped (green ≥80, amber 50–79, red <50) | loading, with/without delta-on-update animation |
| `DataTable` | `components/ui/DataTable.tsx` | Generic sortable/paginated table | loading (skeleton rows), empty, error |
| `FilterBar` | `components/ui/FilterBar.tsx` | Composable row of filter controls (dropdown, date range, search) | — |
| `StatusChip` | `components/ui/StatusChip.tsx` | Processing status (Queued/Transcribing/Analyzing/Ready/Failed) | 5 states, each color-coded |
| `AudioPlayer` | `components/audio/AudioPlayer.tsx` | Wraps `<audio>`, exposes `seekTo(seconds)`, play/pause, scrubber, current-time display | loading, playing, paused, error (file missing) |
| `TranscriptPanel` | `components/call-review/TranscriptPanel.tsx` | Scrollable list of `TranscriptSegment`s, highlights segment under playhead, click-to-seek | loading, empty |
| `FlagList` | `components/call-review/FlagList.tsx` | List of `Violation`s with rule code, quote, severity, jump-to-moment button | empty ("No violations detected") |
| `ReviewPanel` | `components/call-review/ReviewPanel.tsx` | Per-flag Confirm/False-alarm buttons + comment field | pending, submitting, submitted |
| `ConfirmDialog` | `components/ui/ConfirmDialog.tsx` | Generic confirmation modal (used before destructive/irreversible review actions) | — |
| `Toast` | `components/ui/Toast.tsx` | Success/error notifications | success, error |
| `EmptyState` | `components/ui/EmptyState.tsx` | Generic "nothing here" illustration-free block with optional CTA | — |
| `ErrorState` | `components/ui/ErrorState.tsx` | Generic error block with retry button | — |
| `UploadDropzone` | `components/upload/UploadDropzone.tsx` | Drag/drop or browse for audio files + CSV | idle, dragging, validating, error |
| `BatchUploadTable` | `components/upload/BatchUploadTable.tsx` | Per-file row with validation status, processing status | per-row: pending/valid/invalid/processing/done/failed |
| `MetricCard` | `components/dashboard/MetricCard.tsx` | Single KPI tile (e.g. "Precision R3: 0.92") | with/without "insufficient sample" caveat label |
| `BreachBarChart` | `components/dashboard/BreachBarChart.tsx` | Recharts bar chart, breaches by caller/office/rule | loading, empty |
| `RiskyCallerList` | `components/dashboard/RiskyCallerList.tsx` | Top-5 riskiest callers ranked list | empty ("not enough data") |

Reusability note: `DataTable`, `SeverityBadge`, `StatusChip`, `EmptyState`, `ErrorState`, `ConfirmDialog`, `Toast` are used on 2+ screens; everything under `call-review/`, `upload/`, `dashboard/` is screen-specific by design (no premature abstraction across those three, since their data shapes genuinely differ).

### 3.4 State management and data flow

- **`RoleContext`** (React Context, not a server-state concern): `{ role: 'caller'|'manager'|'head', activeUserId: string | null }`, persisted to `localStorage` so a page refresh during a demo doesn't reset the switcher.
- **TanStack Query** for everything server-derived: `useCalls(filters)`, `useCall(callId)`, `useDashboard(filters)`, `useUploadBatch()` (mutation), `useSubmitReview()` (mutation). Query keys include the active role/user so switching identity automatically refetches scoped data — no manual cache-busting needed.
- Mutations (`submitReview`, `uploadBatch`) **optimistically** update the relevant query cache (e.g. marking a flag "Confirmed" instantly) and roll back on error with a toast.
- No global Redux store; `ASSUMPTION`: the only non-server global state is "who am I viewing as," which Context handles cleanly at this scale.

### 3.5 Screen specifications

#### 3.5.1 Upload (`/upload`)

**Purpose & primary user:** Manager and Head of Collections only (final decision; supersedes the earlier "reachable by all roles" assumption). The server returns 403 `UPLOAD_FORBIDDEN` to a caller for the upload and batch-status endpoints, before the request body is read, so a refused upload creates no call and saves no file; the Upload link is hidden for callers and `/upload` shows an access-denied note. Roles remain a prototype convention, not authentication.

**Layout (top to bottom):**

```
┌─────────────────────────────────────────────────────────────┐
│ Page header: "Upload Calls"                                  │
│ Subtext: "Upload audio recordings with a metadata CSV."      │
├─────────────────────────────────────────────────────────────┤
│ ┌───────────────────────────┐  ┌───────────────────────────┐│
│ │  Audio files dropzone      │  │  Metadata CSV dropzone     ││
│ │  (accepts .wav, .mp3,      │  │  (accepts .csv, expects    ││
│ │   multiple)                │  │   call_id, caller, office, ││
│ │                             │  │   start_time columns)      ││
│ └───────────────────────────┘  └───────────────────────────┘│
├─────────────────────────────────────────────────────────────┤
│ [Validate Batch] button (disabled until both present)         │
├─────────────────────────────────────────────────────────────┤
│ BatchUploadTable                                               │
│  File name | Matched call_id | Validation | Processing status │
│  call_01.wav | CALL-2041 | ✓ Valid | ● Queued                 │
│  call_02.wav | CALL-2042 | ✓ Valid | ⟳ Transcribing            │
│  call_07.wav | (no match) | ✗ No matching CSV row | —          │
├─────────────────────────────────────────────────────────────┤
│ [Start Processing Batch] button                                │
│ Summary bar once running: "14 of 20 complete · 1 failed"       │
└─────────────────────────────────────────────────────────────┘
```

**Interactions and outcomes:**
- Dropping files triggers client-side validation: file extension, CSV required columns present, every audio filename has a matching `call_id` row (by filename convention `call_XX.wav` ↔ CSV `call_id`/`filename` column). Mismatches shown inline, batch cannot start until resolved or explicitly skipped per-row.
- "Start Processing Batch" → `POST /api/uploads/batch` (multipart: files[] + csv) → backend creates `Call` rows with `status=queued`, stores audio, kicks off `pipeline.process_call` per call as a background task.
- Frontend polls `GET /api/calls?batch_id=...` every 2s (TanStack Query `refetchInterval`) while any row is non-terminal, updating `StatusChip` per row live.
- On completion of a row: row becomes clickable, linking to `/calls/:callId`.
- Duplicate upload (same `call_id` re-uploaded): backend rejects with 409; frontend shows that row as "Already exists — view call" with a link instead of a failure.

**States:** empty (no files dropped yet), validating, ready-to-start, processing (live per-row status), partial-failure (some rows failed — summary banner, failed rows show error reason + "Retry" button calling `POST /api/calls/{id}/reprocess`), all-succeeded (banner + "Go to Call List" CTA).

**Responsive:** two dropzones stack vertically below 1024px; table scrolls horizontally on narrow viewports rather than collapsing columns (operators need to see status at a glance).

**Accessibility:** dropzones are also real `<input type=file>` behind a styled label (keyboard/screen-reader accessible, not drag-only); status changes announced via an `aria-live="polite"` region.

**Acceptance criteria:** uploading 20 valid WAV files + matching CSV results in 20 `Ready` rows within the processing-time budget; one mismatched filename is caught before processing starts; re-uploading an already-processed call_id is rejected, not silently duplicated.

#### 3.5.2 Call List (`/calls`)

**Purpose & primary user:** All three roles; the one screen where filtering behavior (RQ-01) is most visible.

**Layout:**

```
┌─────────────────────────────────────────────────────────────┐
│ Page header: "Calls" · subtext shows active scope            │
│   e.g. "Showing 142 calls · Jogeshwari office · Manager view"│
├─────────────────────────────────────────────────────────────┤
│ FilterBar: [Office ▾] [Caller ▾] [Severity ▾] [Date range]   │
│            [Search transcript/quote...]                       │
├─────────────────────────────────────────────────────────────┤
│ DataTable                                                      │
│  Call ID | Caller | Office | Date/Time | Duration | Lang      │
│  | Score | Top severity | Status | → (open)                   │
│  CALL-2041 | Pooja R. | Jogeshwari | Oct 12, 8:10pm | 2m14s    │
│  | Hindi | 58 | CRITICAL | Ready | →                            │
├─────────────────────────────────────────────────────────────┤
│ Pagination (25/page)                                            │
└─────────────────────────────────────────────────────────────┘
```

- Caller role: Office/Caller filters hidden (no meaning when scoped to self); table omits the Caller column.
- Manager role: Office filter hidden (fixed to their office); Caller filter shows only their direct reports.
- Head role: all filters active.
- Row click → `/calls/:callId`. Rows for calls still processing show a disabled/greyed state with `StatusChip` instead of score/severity.
- Sorting by Score (ascending, to surface worst calls first) is the default sort — directly supports the "point the manager to what went wrong" mission statement in the PDF's opening paragraph.
- Clicking a severity badge in a row is a quick-filter shortcut (sets Severity filter to that value).

**States:** loading (skeleton rows), empty ("No calls match these filters" + "Clear filters" button), error (retry), partial (some calls still processing — shown inline, not hidden).

**Responsive:** below 1024px, Duration/Language columns collapse into a secondary line under Call ID; filters move into a collapsible "Filters" drawer.

**Accessibility:** table headers are sort buttons with `aria-sort`; filter dropdowns are native `<select>`-backed (or Radix UI Select for styling, which preserves keyboard semantics) — never custom divs without ARIA roles.

**Acceptance criteria:** switching role/identity changes the row count and visible columns per §8.4's filter table, with no client-side-only filtering of data the backend shouldn't have sent in the first place (verified by network-tab inspection, not just UI hiding).

#### 3.5.3 Call Review (`/calls/:callId`)

**Purpose & primary user:** The core screen — where a manager (or head) actually does compliance review, and where the demo's "jump to the flagged threat" moment happens.

**Layout (desktop ≥1280px, two-column):**

```
┌───────────────────────────────────────────────────────────────┐
│ ← Back to Calls   ·   CALL-2041   ·  Pooja Rane · Jogeshwari    │
│ Oct 12, 8:10 PM · 2m14s · Hindi · Personal Loan                 │
├─────────────────────────────┬─────────────────────────────────┤
│ LEFT (sticky, 40%)           │ RIGHT (scrolls, 60%)             │
│                               │                                   │
│ AudioPlayer                  │  Tabs: [Transcript] [Flags (3)]  │
│  [▶] 0:42 ──●───── 2:14      │                                   │
│                               │  ── Transcript tab ──             │
│ ScoreGauge: 58 / 100          │  0:00 Agent: Namaste, main...     │
│  (red zone)                   │  0:06 Borrower: Ji haan...        │
│                               │  0:41 Agent: [CRITICAL R3 ●]      │
│ Sentiment: Negative            │        "hum aapke ghar bhi..."   │
│ Agent stayed calm: No          │  ...                              │
│                               │                                   │
│                               │  ── Flags tab ──                  │
│                               │  [CRITICAL] R3 Threats · 0:41     │
│                               │   "hum aapke ghar bhi aayenge"    │
│                               │   [▶ Play] [Confirm] [False alarm]│
│                               │   Comment: [............]          │
│                               │  [HIGH] R5 OTP request · 1:12      │
│                               │   ...                              │
└─────────────────────────────┴─────────────────────────────────┘
```

**Main sections, in order:**
1. **Header** — call metadata (ID, caller, office, timestamp, duration, language, account type).
2. **Left column (sticky on scroll):** `AudioPlayer`, `ScoreGauge`, sentiment + agent-calm summary.
3. **Right column, tabbed:**
   - **Transcript tab** (default): full `TranscriptPanel`, each segment showing speaker label, timestamp, text; segments with an attached violation show an inline severity marker; current playback position highlights the active segment and auto-scrolls it into view.
   - **Flags tab**: `FlagList` + `ReviewPanel` combined — every violation as a card with rule code/name, severity badge, exact quoted text, timestamp, a "▶ Play" button, and (manager/head only) Confirm / False alarm buttons + comment textarea.

**Data displayed and source:** `GET /api/calls/{id}` returns call metadata + score + sentiment; `GET /api/calls/{id}/transcript` returns ordered segments; `GET /api/calls/{id}/violations` returns violations with linked segment id, quote, timestamp, severity, and current review status.

**User interactions and outcomes:**
- Click a flag's "▶ Play" → `audioPlayer.seekTo(violation.start_time)` + auto-play. This is RQ-05's core acceptance test.
- Click a transcript segment → same seek behavior (bonus convenience, not explicitly required but a natural extension of the same mechanism).
- Manager/Head clicks "Confirm" on a flag → `ConfirmDialog` ("Confirm this as a genuine compliance breach?") → `POST /api/violations/{id}/review {decision: 'confirmed', comment}` → optimistic UI update (badge turns solid, buttons disable, shows reviewer name + time) → score recalculates server-side → `ScoreGauge` animates to new value.
- Manager/Head clicks "False alarm" → same flow with `decision: 'false_alarm'`, violation badge greys out with strikethrough-style treatment but **remains visible** (never deleted — audit trail), and is excluded from the score calculation on recalculation.
- Comment field is optional for Confirm, `ASSUMPTION`: **required** for False alarm (reviewers should justify overriding an AI flag — not in the PDF explicitly, but directly serves judging criterion "accuracy numbers are reported honestly" by creating an auditable reason trail).
- Caller role: Flags tab is **read-only** — no Confirm/False-alarm/comment controls rendered at all (not just disabled) — a caller should not be able to adjudicate their own compliance flags.

**Navigation to related screens:** "← Back to Calls" preserves the Call List's prior filter/scroll state (via React Router state, not a full navigation reset).

**States:**
- Loading: skeleton for header + player + transcript.
- Empty flags: "No violations detected for this call" (green-tinted `EmptyState`, still shows transcript/sentiment).
- Processing not yet complete: whole page shows a "Still analyzing this call…" `StatusChip`-driven state with poll-and-refresh, no player/transcript shown yet.
- Error: audio file missing/corrupt → `AudioPlayer` shows an inline error but transcript/flags still render (partial degradation, not a full-page failure).
- Permission state: Caller viewing someone else's call is simply unreachable — backend returns 403, frontend shows a generic "Call not found" `ErrorState` (deliberately not "you don't have permission," to avoid leaking the existence of another caller's call ID — minor defense-in-depth, `ASSUMPTION` beyond PDF scope but cheap to do correctly).

**Responsive (768–1279px):** left column becomes a collapsible top section (player + score collapse to a slim sticky bar); below that, full-width tabs.

**Accessibility:** audio player keyboard-operable (space to play/pause when focused); flag cards are a `<ul>` of landmark-labelled regions; Confirm/False-alarm are real `<button>`s with clear accessible names ("Confirm violation R3 at 0:41"), not icon-only.

**Acceptance criteria:** clicking any flag's Play button seeks the shared `<audio>` element to within 1 second of `violation.start_time`; confirming a flag persists across a page reload; a caller identity can never see review controls, verified by component-level test asserting `ReviewPanel` is not rendered (not merely hidden via CSS) when `role==='caller'`.

#### 3.5.4 Dashboard (`/dashboard`)

**Purpose & primary user:** Manager and Head primarily; Caller sees a narrower, self-scoped version (their own breach history only) — `ASSUMPTION`: PDF doesn't explicitly describe a caller-scoped dashboard, but §2's filtering rule ("a caller sees only their own calls, flags and scores") implies the dashboard should obey the same scoping rather than being hidden entirely; hiding it outright would be an unexplained inconsistency with every other screen.

**Layout:**

```
┌─────────────────────────────────────────────────────────────┐
│ Page header: "Dashboard" + scope subtext (as in Call List)    │
│ FilterBar: [Date range] [Office ▾ (head only)]                │
├───────────────┬───────────────┬───────────────┬──────────────┤
│ MetricCard     │ MetricCard     │ MetricCard     │ MetricCard   │
│ Total calls    │ Avg score      │ Critical       │ WER (3 calls)│
│ 142            │ 76.4           │ breaches: 4     │ 14.2%        │
├───────────────┴───────────────┴────────────────┴──────────────┤
│ BreachBarChart — "Breaches by rule"  (R1..R6 on x-axis)         │
├─────────────────────────────┬───────────────────────────────────┤
│ BreachBarChart                │ RiskyCallerList                  │
│ "Breaches by office"          │ Top 5 riskiest callers            │
│ (head/manager only)           │ 1. Imran K. — 3 critical (Mgr: ..)│
├─────────────────────────────┴───────────────────────────────────┤
│ Precision/Recall table (per rule, on the 20 labelled calls)      │
│ Rule | Precision | Recall | F1 | Support | Note                 │
│ R3   | 0.90      | 1.00   |0.95| 2       | "low sample size" ⚠  │
└─────────────────────────────────────────────────────────────┘
```

**Data and source:** `GET /api/analytics/summary`, `GET /api/analytics/breaches-by-rule`, `GET /api/analytics/breaches-by-office`, `GET /api/analytics/risky-callers`, `GET /api/analytics/evaluation` (precision/recall/WER — computed only against the 20 ground-truth calls, clearly labelled as such, never conflated with the ~2,000 synthetic metadata-only calls).

**Interactions:** date-range and office filters refetch all charts together (shared query key); clicking a bar in "Breaches by rule" navigates to Call List pre-filtered to that rule+severity; clicking a risky caller navigates to Call List pre-filtered to that caller.

**States:** loading (skeleton cards/charts), empty (new/reset DB before seeding — "Run the seed script to populate the dashboard"), **insufficient-sample warning**: any precision/recall cell with `support < 3` (`ASSUMPTION`: a reasonable statistical-honesty threshold given each rule only guarantees ≥2 occurrences per PDF) is visually flagged with a ⚠ icon and tooltip "Low sample size — interpret with caution," directly satisfying judging criterion "accuracy numbers are reported honestly."

**Responsive:** 4-up MetricCards become 2-up at <1024px, 1-up at <640px; charts stack vertically below 1024px.

**Accessibility:** charts have an adjacent visually-hidden data table (`<table class="sr-only">`) mirroring each chart's data, so the information isn't screen-reader-inaccessible; MetricCards use `<dl>/<dt>/<dd>` semantics.

**Acceptance criteria:** numbers on this page are independently reproducible by running `scripts/evaluate.py` against the same ground-truth file the frontend's API reads from — no hardcoded/mocked dashboard numbers.

### 3.6 Interactive behavior — cross-cutting rules

- **Form submission (Upload, Review comment):** client-side validation first (required fields, file types), then submit; submit button shows a loading spinner and is disabled during the request (prevents duplicate submits — directly serves "prevents accidental duplicate actions").
- **Filter/role changes:** always refetch via query-key change, never silent stale data; a subtle top-of-page progress bar (not a full-page spinner) indicates refetching so the UI doesn't feel like it reloaded.
- **Navigation state:** Call List filters persist in the URL querystring (shareable/bookmarkable, survives back-navigation from Call Review).
- **Selected records:** `callId` from the route is the single source of truth — no duplicate "selected call" state elsewhere.
- **Async operations:** every mutation (`uploadBatch`, `submitReview`, `reprocess`) shows one of: idle → pending (spinner on the triggering control only) → success (toast + optimistic UI) → error (toast with retry affordance, optimistic change rolled back).
- **Error communication:** toasts for transient/action errors; inline `ErrorState` for failed page-level data fetches, always with a "Retry" button that re-triggers the same query.
- **Duplicate-action prevention:** all mutation buttons disable immediately on click until the request settles; re-clicking "Confirm" on an already-confirmed flag is a no-op (button becomes non-interactive, shows "Confirmed by {reviewer}" instead).
- **Confirmation required for:** False-alarm decisions (overriding an AI-detected Critical/High flag) and batch-upload start when any row has unresolved validation warnings. Confirm is **not** required for routine navigation or re-reading data.
- **Functional without a live backend (PDF: "which interactions must remain functional without live backend"):** `ASSUMPTION` — read this as "the demo must not break if the LLM/transcription API is slow or rate-limited mid-demo," not "the whole app must work fully offline." Mitigation: the 20 real calls are **pre-processed and cached in the DB before demo day** (so Call Review/Dashboard never depend on live API calls during the actual demo); only the "5 unseen calls" step makes a live external call, and that step has a generous loading state plus a documented fallback (pre-recorded screen capture) noted in the README per RQ-19/§13.4.


---

## 4. Complete Backend Plan

### 4.1 Application structure

```
backend/
  app/
    main.py                 # FastAPI app, CORS, router mounting, startup checks
    config.py                # pydantic-settings Settings
    db.py                    # SQLAlchemy engine/session
    models.py                 # ORM models (§6)
    schemas.py                 # Pydantic request/response models
    deps.py                    # shared FastAPI dependencies (role context, db session)
    routers/
      uploads.py
      calls.py
      violations.py
      analytics.py
      meta.py                 # offices/users lookup for filter dropdowns
    services/
      upload_service.py
      pipeline.py              # orchestrates transcription→labelling→compliance→scoring
      transcription_service.py # Groq hosted Whisper STT client (+ manual Sarvam fallback)
      llm_service.py           # Groq openai/gpt-oss-120b client (speaker labelling + compliance)
      scoring_service.py
      evaluation_service.py
      access_control.py        # role/scope filtering logic, shared by all routers
    rules.py                    # R1–R6 constants, severities, deterministic R1 check
  scripts/
    seed_db.py
    evaluate.py
  tests/
```

### 4.2 Module responsibilities

- **`routers/*`**: thin — parse request, call a service, map result to a response schema, raise `HTTPException` on known failure modes. No business logic in routers.
- **`services/access_control.py`**: single source of truth for "given this role + user id, what `Call` rows / `User` rows are visible" — both the Call List and Dashboard and Analytics endpoints call into this, so filtering logic can't drift between screens.
- **`services/pipeline.py`**: the only place that sequences transcription → speaker-labelling → compliance/sentiment → scoring; idempotent (`reprocess_call` safely re-runs and overwrites prior results for that call, used for retries and the "5 unseen calls" demo step).
- **Validation**: Pydantic schemas validate all request bodies; CSV metadata validated row-by-row in `upload_service.py` with per-row error collection (not fail-the-whole-batch-on-first-bad-row).
- **Exception handling**: a small set of domain exceptions (`CallNotFoundError`, `DuplicateCallError`, `InvalidMetadataError`, `TranscriptionProviderError`, `LLMResponseValidationError`) caught by a FastAPI exception handler in `main.py` and mapped to consistent `{error: {code, message}}` JSON — never a raw 500 stack trace to the frontend.
- **Configuration**: see §2.4.

### 4.3 API endpoints

All responses are JSON. All list endpoints support pagination (`page`, `page_size`) and return `{items: [...], total: int, page: int, page_size: int}`. Role scoping (§8) is applied **inside every endpoint below that returns Call/Violation/analytics data** via `access_control.py` — it is not optional per-endpoint behavior.

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/api/meta/offices` | List offices (for filter dropdown) |
| `GET` | `/api/meta/users?role=caller\|manager` | List users for the identity picker, scoped by current role context where applicable |
| `POST` | `/api/uploads/batch` | Multipart: `audio_files[]`, `metadata_csv`. Creates `Call` rows, stores audio, kicks off processing |
| `GET` | `/api/uploads/batch/{batch_id}` | Poll batch status: per-call processing state |
| `GET` | `/api/calls` | List calls, filtered by role scope + query filters (`office_id`, `caller_id`, `severity`, `date_from`, `date_to`, `q` free-text search over quotes/transcript) |
| `GET` | `/api/calls/{call_id}` | Call detail: metadata, score, sentiment, agent_calm, status |
| `GET` | `/api/calls/{call_id}/transcript` | Ordered `TranscriptSegment` list |
| `GET` | `/api/calls/{call_id}/violations` | `Violation` list for the call, each with review status |
| `POST` | `/api/calls/{call_id}/reprocess` | Re-run the pipeline for one call (retry or re-score) |
| `GET` | `/api/media/{call_id}` | Streams the audio file (range-request support for seeking) |
| `POST` | `/api/violations/{violation_id}/review` | Body: `{decision: 'confirmed'|'false_alarm', comment: string}`. Creates a `Review` row, triggers score recalculation |
| `GET` | `/api/analytics/summary` | Total calls, avg score, critical-breach count, WER (role-scoped) |
| `GET` | `/api/analytics/breaches-by-rule` | Counts per rule code (role-scoped) |
| `GET` | `/api/analytics/breaches-by-office` | Counts per office (head/manager only — see §8) |
| `GET` | `/api/analytics/risky-callers` | Top 5 callers by weighted critical/high breach count (role-scoped) |
| `GET` | `/api/analytics/evaluation` | Precision/recall/F1/support per rule, computed against `ground_truth.json`, plus WER on the 3 hand-typed transcripts |

### 4.4 Representative request/response schemas

**`POST /api/uploads/batch`** (multipart form)
Request: `audio_files: File[]`, `metadata_csv: File` (columns: `call_id, caller_id, office_id, account_type, start_time, language`)
Response `202 Accepted`:
```json
{
  "batch_id": "b-20261028-01",
  "accepted": [{"call_id": "CALL-2041", "status": "queued"}],
  "rejected": [{"filename": "call_07.wav", "reason": "no matching call_id in CSV"}]
}
```

**`GET /api/calls`** response item shape:
```json
{
  "call_id": "CALL-2041",
  "caller": {"id": "u-014", "name": "Pooja Rane"},
  "office": {"id": "o-01", "name": "Jogeshwari"},
  "start_time": "2026-10-12T20:10:00+05:30",
  "duration_seconds": 134,
  "language": "hi",
  "account_type": "personal_loan",
  "score": 58,
  "top_severity": "critical",
  "status": "ready"
}
```

**`GET /api/calls/{id}/violations`** response item shape:
```json
{
  "violation_id": "v-0091",
  "rule_code": "R3",
  "rule_name": "No threats or intimidation",
  "severity": "critical",
  "segment_id": "s-0041",
  "start_time_seconds": 41.0,
  "quote": "hum aapke ghar bhi aayenge",
  "explanation": "Agent threatens an in-person visit framed as intimidation.",
  "confidence": 0.93,
  "review": {"decision": "pending", "reviewer": null, "comment": null, "reviewed_at": null}
}
```

**`POST /api/violations/{id}/review`** request/response:
```json
// request
{"decision": "confirmed", "comment": "Clear R3 breach, escalate to agent coaching."}
// response
{
  "violation_id": "v-0091",
  "review": {"decision": "confirmed", "reviewer": "Rohan Mehta", "comment": "...", "reviewed_at": "2026-10-28T11:02:00+05:30"},
  "call": {"call_id": "CALL-2041", "score": 41}
}
```

**`GET /api/analytics/evaluation`** response shape:
```json
{
  "per_rule": [
    {"rule_code": "R3", "precision": 0.90, "recall": 1.00, "f1": 0.95, "support": 2, "low_sample_size": true}
  ],
  "word_error_rate": {"value": 0.142, "based_on_calls": 3},
  "notes": "Precision/recall computed only on the 20 hand-labelled calls. Support below 3 is flagged as low-sample-size."
}
```

**Error shape (all 4xx/5xx):**
```json
{"error": {"code": "CALL_NOT_FOUND", "message": "Call CALL-9999 does not exist or is not visible to this role."}}
```

### 4.5 Partial failures, duplicates, invalid metadata, processing status

- **Duplicate uploads**: `call_id` is unique; re-upload of an existing `call_id` returns it in `rejected` with reason `"duplicate call_id"`, no data overwritten unless the caller explicitly hits `reprocess`.
- **Invalid metadata rows**: validated independently per CSV row; a malformed row (missing required column, unparseable timestamp, unknown `office_id`/`caller_id`) is rejected with a specific reason, the rest of the batch proceeds.
- **Partial batch failure**: each `Call` has a `status` enum: `queued → transcribing → analyzing → ready` or `→ failed` at any stage, with `failure_reason` text. The batch-status endpoint reports all of them; the frontend never treats "batch submitted" as "batch succeeded."
- **Processing status** is the same enum surfaced consistently in Upload (`BatchUploadTable`), Call List (`StatusChip`), and Call Review (full-page processing state) — one enum, defined once in `models.py`, imported everywhere.


---

## 5. AI and Audio-Processing Pipeline

### 5.1 Lifecycle overview

```
Uploaded audio ──▶ [0] Python deterministic check (Call.start_time) ──▶ R1 Violation (if outside 8 AM–7 PM)
                 │
                 ├──▶ [1] Groq Hosted STT (whisper-large-v3-turbo) ──▶ raw transcript + word-level timestamps
                 │                                                          │
                 │                                                          ▼
                 ├──▶ [2] Groq LLM (openai/gpt-oss-120b) ──▶ TranscriptSegment rows
                 │         (Agent/Borrower per turn, preserving timestamps) │
                 │                                                          ▼
                 ├──▶ [3] Groq LLM (openai/gpt-oss-120b) ──▶ Violation rows (R2–R6) + Call.sentiment
                 │         (single structured-JSON call,                     + Call.agent_stayed_calm
                 │          given labelled transcript + rule table)
                 │                                                          │
                 │                                                          ▼
                 └──▶ [4] Python scoring_service (deterministic) ──▶ Call.score
                                                                            │
                                                                            ▼
                                                                      Persisted, status=ready
```

Step [1] additionally includes the **deterministic R1 check** (call-time-window), which uses `Call.start_time` metadata only — no LLM involved — run in parallel with step [2]/[3] since it has no transcript dependency.

### 5.2 Responsibilities by component

- **Transcription service (Groq hosted `whisper-large-v3-turbo`)**: audio-in, text-out with word/segment-level timestamps via Groq's hosted `/openai/v1/audio/transcriptions` endpoint. Hosted STT only; no local speech-to-text models or Ollama. Does **not** attempt speaker diarization reliably across all four languages — this prototype delegates speaker attribution to the LLM (step 2) using conversational structure and cues. (Sarvam AI is maintained as a documented manual hosted fallback, never auto-activated).
- **LLM (speaker labelling, step 2 — Groq `openai/gpt-oss-120b`)**: given the raw transcript with timestamps from Groq Whisper, outputs a strict JSON array of `{speaker: "Agent"|"Borrower", start, end, text}`, using turn-taking patterns, self-identification cues ("main Pooja bol rahi hoon, Shaha Finlease se" = Agent's opening), and conversational context (who asks about the loan vs. who explains their situation). Timestamps from STT are preserved unchanged.
- **LLM (compliance + sentiment, step 3 — Groq `openai/gpt-oss-120b`)**: given the full labelled transcript **plus the exact rule table from §1.4 in the prompt** (per PDF's explicit instruction), outputs structured JSON violations (R2–R6 only) + sentiment + agent-calm judgment. This is one call per transcript, not one call per rule (conserves tokens against the 8,000 TPM limit, and lets the LLM reason about conversational context — e.g. distinguishing a borrower's own mention of "police" from the agent threatening police).
- **Deterministic Python (`rules.py`, `scoring_service.py`)**: R1 (time-window check) is fully deterministic using `Call.start_time` metadata (0 LLM tokens). Furthermore, all LLM outputs are strictly validated by deterministic Python: verbatim quote substring checks, segment foreign key existence checks, and severity weight mapping strictly from `rules.py` (the LLM cannot alter severity levels).

### 5.3 Structured JSON schema expected from the LLM (step 3)

Requested via a strict system prompt + JSON schema (using the LLM provider's structured-output/function-calling mode, not hoping the model returns clean JSON in prose):

```json
{
  "violations": [
    {
      "rule_code": "R2|R3|R4|R5|R6",
      "segment_id": "string, must match a real TranscriptSegment id",
      "quote": "string, must be a verbatim substring of that segment's text",
      "explanation": "string, 1 sentence",
      "confidence": "number 0-1"
    }
  ],
  "sentiment": {
    "borrower_overall": "positive|neutral|negative",
    "agent_stayed_calm": true
  }
}
```

R1 is **not** requested from the LLM — it's computed deterministically by `pipeline.py` before the LLM call. The R1 `Violation` row has:
- `segment_id = NULL` — R1 is a metadata fact, not tied to any spoken line. **`segment_id` is nullable on the `violation` table specifically to support R1.**
- `start_time_seconds = 0.0` — the violation is the call start itself.
- `quote = "Call started at HH:MM IST, outside the permitted 8 AM–7 PM window."` — a descriptive string, not a transcript excerpt.
- `confidence = 1.0` — deterministic, always certain.

In the Call Review frontend, R1's "▶ Play" button seeks the audio to 0:00 (the start of the call). This is correct — the violation is the call happening at all, not a specific spoken moment.

### 5.4 Validation, retry, rate limit pacing, and strict failure handling

- **Groq Free-Tier Rate Limits & Pacing Strategy**:
  - `openai/gpt-oss-120b` has a free-tier rate limit of **8,000 Tokens Per Minute (TPM)** and 30 Requests Per Minute (RPM).
  - A typical call transcript (~1,000–1,500 tokens) combined with the system prompt, rules table, and JSON schema consumes ~2,500–3,500 input tokens per compliance request.
  - Sending more than 2 calls concurrently or back-to-back within 60 seconds will trigger HTTP 429 (`rate_limit_exceeded`).
  - **Pacing enforcement**: Ingestion of the 20 batch calls must be executed sequentially with an explicit inter-call delay (≥20–25 seconds) or triggered on-demand, ensuring token consumption stays comfortably below the 8,000 TPM ceiling.
- **Strict Non-Substitution & Failure Reporting**:
  - If `openai/gpt-oss-120b` returns HTTP 429 (rate limit exceeded) or HTTP 503 (service unavailable):
    1. The client performs 1 retry with exponential backoff (delay 10s).
    2. If the request fails a second time, the call is marked `Call.status = 'failed'` with `failure_reason = 'LLM quota exceeded or model unavailable: openai/gpt-oss-120b (HTTP 429/503)'`.
    3. **Under NO circumstances will the system silently substitute another LLM (such as `llama-3.3-70b-versatile` or any local model).** The failure is displayed explicitly in the UI and logged. Any switch to an alternative model must be an explicit manual configuration decision approved by the user.
- **Transcription Failure Handling**:
  - If Groq Whisper STT fails: `Call.status = 'failed'`, `failure_reason = 'Transcription failed: whisper-large-v3-turbo'`. The system will NOT automatically fall back to another service or download local models. A manual alternative (`TRANSCRIPTION_PROVIDER=sarvam`) is documented for optional user configuration.
- **Schema validation**: response parsed against a Pydantic model (`LLMComplianceOutput`); malformed JSON → one retry with a corrective follow-up message ("Your last response wasn't valid JSON matching the schema, here is the error: ..."); second failure → `Call.status = 'failed'`, `failure_reason = 'LLM response validation failed'`, surfaced in the UI, **not** silently defaulted to "no violations" (that would be dishonest, directly violating judging criterion 4).
- **Quote verification**: every `quote` is checked to be an actual substring of the referenced segment's `text` (case/whitespace-normalized). A hallucinated quote that doesn't match is **dropped** from the violation list and logged as a warning — never shown to a manager as if it were verbatim, since the whole point of RQ-04 is "exact quote."
- **`segment_id` verification**: must reference a real segment from step 2's output; otherwise dropped with a warning, same reasoning.
- **Retry budget**: max 2 attempts per step per call (`ASSUMPTION`: generous enough to survive transient API blips, bounded enough that a batch of 20 doesn't hang indefinitely before a demo).

### 5.5 Timestamp accuracy, speaker attribution, multilingual handling

- Timestamps come from the STT provider's word/segment timing (sub-second granularity where the provider supports it), carried through unchanged into `TranscriptSegment.start_time`/`end_time` — the LLM speaker-labelling pass reassigns *speaker*, not *timing*, so RQ-05's "exact moment" accuracy depends on the STT provider's timing quality, not the LLM.
- Multilingual/code-mixed input: Groq hosted `whisper-large-v3-turbo` supports multilingual speech across Hindi, English, Kannada, and Marathi; `Call.language` stores the dominant detected language for display, while the transcript itself is kept in its original mixed script rather than force-translated (translating would corrupt "exact quote" fidelity for R2–R5, which depend on original phrasing).
- Linking violations to segments: every `Violation.segment_id` is a foreign key to a real `TranscriptSegment`, so the Call Review page's "click flag → highlight transcript line → seek audio" chain (RQ-05) is a single join, not fuzzy text matching at render time.

### 5.6 Ensuring analysis of actual audio, not hardcoded results

- `pipeline.process_call(call_id)` always re-derives from the stored audio file path — there is no code path that returns a fixed/sample violation set for a given `call_id`. `scripts/seed_db.py` generates ~2,000 **metadata-only** calls explicitly marked `has_audio=false` with `status='metadata_only'` — these are **never** passed through the AI pipeline and **never** shown with fabricated transcripts/violations (Call Review for a metadata-only call shows an honest "No recording available for this call" state, not fake data). This is the single most important integrity guarantee in the system, directly serving judging criterion 4 (honest accuracy reporting) and RQ-20.

### 5.7 AI-generated vs. manager-confirmed violations

- Every `Violation` starts with `review.decision = 'pending'` (AI-detected, unconfirmed).
- `review.decision` becomes `'confirmed'` or `'false_alarm'` only via `POST /api/violations/{id}/review`, performed by a manager/head identity — never auto-confirmed.
- The scoring formula (§7.4) and dashboard counts **distinguish all three states explicitly** in every aggregate query — "flagged," "confirmed," and "false alarm" are never merged into one number without labelling which is which.

### 5.8 Credentials and external dependencies

- `GROQ_API_KEY` read from environment on the backend only, via `config.py`'s `Settings`, never hardcoded, never exposed to the frontend, never logged (a logging filter strips any env var matching `*_API_KEY` from request/error logs).
- Verified Groq Free-Tier quotas and limitations documented in README:
  - `openai/gpt-oss-120b`: 30 RPM, 8,000 TPM, 1,000 RPD, 200,000 TPD (zero cost, no credit card required).
  - `whisper-large-v3-turbo`: 20 RPM, 2,000 RPD, 7,200 audio seconds/hour, 25 MB max file size (zero cost, no credit card required).
  - Provider outages or rate limit rejections surface honestly as `Call.status='failed'` with `reprocess` as the recovery path.

---

## 6. Database Design

### 6.1 Schema (SQLAlchemy models, PostgreSQL-compatible types)

```sql
-- Office
CREATE TABLE office (
  id            TEXT PRIMARY KEY,          -- e.g. 'o-01'
  name          TEXT NOT NULL UNIQUE,       -- 'Jogeshwari' | 'Lower Parel' | 'Bangalore'
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- User (caller, manager, or head)
CREATE TABLE app_user (
  id            TEXT PRIMARY KEY,           -- e.g. 'u-014'
  name          TEXT NOT NULL,
  role          TEXT NOT NULL CHECK (role IN ('caller','manager','head')),
  office_id     TEXT NOT NULL REFERENCES office(id),
  manager_id    TEXT REFERENCES app_user(id),  -- NULL for managers/head; set for callers
  created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_app_user_manager ON app_user(manager_id);
CREATE INDEX idx_app_user_office  ON app_user(office_id);

-- Call
CREATE TABLE call (
  id                TEXT PRIMARY KEY,         -- e.g. 'CALL-2041'
  caller_id         TEXT NOT NULL REFERENCES app_user(id),
  office_id         TEXT NOT NULL REFERENCES office(id),
  account_type      TEXT NOT NULL CHECK (account_type IN ('personal_loan','credit_card')),
  start_time        TIMESTAMPTZ NOT NULL,
  duration_seconds  INTEGER,
  language          TEXT,                      -- dominant detected language code
  has_audio         BOOLEAN NOT NULL DEFAULT false,
  audio_path        TEXT,                       -- NULL if has_audio=false
  status            TEXT NOT NULL DEFAULT 'metadata_only'
                      CHECK (status IN ('metadata_only','queued','transcribing','analyzing','ready','failed')),
  failure_reason    TEXT,
  score             INTEGER CHECK (score BETWEEN 0 AND 100),
  sentiment          TEXT CHECK (sentiment IN ('positive','neutral','negative')),
  agent_stayed_calm BOOLEAN,
  is_ground_truth   BOOLEAN NOT NULL DEFAULT false,   -- true for the 20 hand-labelled calls
  batch_id          TEXT,
  created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX idx_call_caller    ON call(caller_id);
CREATE INDEX idx_call_office    ON call(office_id);
CREATE INDEX idx_call_status    ON call(status);
CREATE INDEX idx_call_score     ON call(score);
CREATE INDEX idx_call_start     ON call(start_time);

-- TranscriptSegment
CREATE TABLE transcript_segment (
  id           TEXT PRIMARY KEY,              -- e.g. 's-0041'
  call_id      TEXT NOT NULL REFERENCES call(id) ON DELETE CASCADE,
  seq          INTEGER NOT NULL,               -- ordering within the call
  speaker      TEXT NOT NULL CHECK (speaker IN ('agent','borrower')),
  start_time   NUMERIC(8,2) NOT NULL,          -- seconds
  end_time     NUMERIC(8,2) NOT NULL,
  text         TEXT NOT NULL,
  sentiment    TEXT CHECK (sentiment IN ('positive','neutral','negative')),  -- per-segment, optional detail
  UNIQUE (call_id, seq)
);
CREATE INDEX idx_segment_call ON transcript_segment(call_id);

-- Rule (lookup table, 6 fixed rows)
CREATE TABLE rule (
  code        TEXT PRIMARY KEY,              -- 'R1'..'R6'
  name        TEXT NOT NULL,
  description TEXT NOT NULL,
  default_severity TEXT NOT NULL CHECK (default_severity IN ('critical','high','medium','low'))
);

-- Violation
CREATE TABLE violation (
  id                   TEXT PRIMARY KEY,       -- e.g. 'v-CALL-0002-0001'
  call_id              TEXT NOT NULL REFERENCES call(id) ON DELETE CASCADE,
  rule_code            TEXT NOT NULL REFERENCES rule(code),
  segment_id           TEXT REFERENCES transcript_segment(id),  -- NULL for R1 (metadata-based, no transcript segment)
  severity             TEXT NOT NULL CHECK (severity IN ('critical','high','medium','low')),
  start_time_seconds   REAL,                   -- denormalized for fast API response; 0.0 for R1
  quote                TEXT NOT NULL,           -- verbatim from transcript; for R1: descriptive string about call time
  explanation          TEXT,
  confidence           REAL CHECK (confidence BETWEEN 0.0 AND 1.0),  -- 1.0 for R1 (deterministic)
  created_at           TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX idx_violation_call ON violation(call_id);
CREATE INDEX idx_violation_rule ON violation(rule_code);

-- DESIGN NOTE: segment_id is NULLABLE. R1 violations have segment_id=NULL because R1
-- is evaluated from Call.start_time metadata, not from any spoken transcript segment.
-- start_time_seconds is denormalized here (not requiring a segment join) so that the
-- API can return it for R1 violations without a segment. For R1: start_time_seconds=0.0.

-- Review
CREATE TABLE review (
  id              TEXT PRIMARY KEY,
  violation_id    TEXT NOT NULL UNIQUE REFERENCES violation(id) ON DELETE CASCADE,
  decision        TEXT NOT NULL CHECK (decision IN ('pending','confirmed','false_alarm')) DEFAULT 'pending',
  reviewer_id     TEXT REFERENCES app_user(id),
  comment         TEXT,
  reviewed_at     TIMESTAMPTZ
);
```

`UNIQUE (violation_id)` on `review` encodes "one review per violation" (re-reviewing updates the same row, preserving a single current decision — history of decision changes is out of scope for the 2–3 day budget; `ASSUMPTION`, noted as a README limitation).

### 6.2 Audio file storage

- Files stored on local disk under `backend/data/audio/{call_id}.wav`, referenced by `Call.audio_path` (relative path, not absolute, for portability across dev machines/containers).
- Served via `GET /api/media/{call_id}` with HTTP Range support (`Response` with `Accept-Ranges`/`Content-Range` headers) so the browser `<audio>` element can seek without downloading the whole file first — required for RQ-05's instant-seek interaction on potentially 1–2 minute files.
- `ASSUMPTION`: no object storage (S3 etc.) — unnecessary infrastructure for 20 files in a 2–3 day prototype; documented as a "swap for S3 in production" note in the README.

### 6.3 Initialization, seeding, reset

- `alembic upgrade head` creates the schema from migrations (checked into `backend/alembic/versions/`).
- `scripts/seed_db.py`:
  1. Wipes all tables (safe, demo-only script — guarded by an env check so it can't run against a non-`DATABASE_URL`-local DB by accident).
  2. Inserts 3 offices, 60 callers + 7 managers + 1 head (with realistic manager→office, caller→manager assignments satisfying "a manager's team" semantics used by §8).
  3. Inserts the fixed 6 `rule` rows (R1–R6, exact text from §1.4).
  4. Generates ~2,000 `Call` rows with `has_audio=false, status='metadata_only'`, randomized but plausible start times/durations/languages/account types, distributed across callers.
  5. Loads the 20 real recordings from `data/recordings/call_*.wav` + their metadata CSV, sets `has_audio=true, is_ground_truth=true`, and either runs the full pipeline immediately or marks them `queued` for the normal upload-processing path (`ASSUMPTION`: run the pipeline **once at seed time** and cache results, so dashboard/demo load is instant and not dependent on live API calls during the actual presentation — consistent with §3.6's "functional without live backend" interpretation).
  6. Loads `data/ground_truth.json` (hand labels for the 20 calls) for the evaluation endpoint to read — kept as a **separate file**, never merged into the `violation` table, so AI-detected and ground-truth labels can't be confused.
- One command: `make seed` wraps `alembic upgrade head && python scripts/seed_db.py`.

### 6.4 Coexistence of 2,000 metadata-only calls with 20 real recordings

Governed entirely by `Call.has_audio` + `Call.status`:
- `has_audio=false, status='metadata_only'` (the ~2,000): appear in Call List and most dashboard counts (e.g. "total calls handled"), but Call Review shows an explicit "No recording available" state; **excluded** from any score/sentiment/violation aggregate, since they have none.
- `has_audio=true, is_ground_truth=true` (the 20): full pipeline output, plus they're the only calls eligible for the `/api/analytics/evaluation` precision/recall computation (joined against `ground_truth.json`).
- `has_audio=true, is_ground_truth=false` (the 5 unseen demo-day calls, uploaded live): full pipeline output, included in Call List/Dashboard breach counts, but **not** included in precision/recall (no ground truth exists for them) — `ASSUMPTION`: the PDF says these are run through "the same workflow," not that they're scored for accuracy, since no one has hand-labelled them.

This three-way split is the backbone of keeping dashboard numbers honest (judging criterion 4) — every analytics query filters explicitly by these flags rather than assuming "all calls have full data."


---

## 7. Compliance Rules and Scoring

### 7.1 Rules R1–R6, implemented exactly as specified

Already detailed in §1.4 and §5; restated here for completeness with evaluation method:

| Code | Rule | Evaluation method | Default severity |
|---|---|---|---|
| R1 | Calls only 8 AM–7 PM | **Deterministic**: `call.start_time.time()` outside `[08:00, 19:00)` → violation | High |
| R2 | No abusive/foul language | **LLM**, transcript-based | Critical |
| R3 | No threats/intimidation | **LLM**, transcript-based | Critical |
| R4 | No shaming/third-party disclosure | **LLM**, transcript-based | Critical |
| R5 | No OTP/PIN/CVV/password requests | **LLM**, transcript-based | High |
| R6 | Mandatory opening (name, company, purpose) | **LLM**, transcript-based, scoped to only the first 1–2 agent segments | Medium |

Severity is stored per-violation (not just read from the `rule` lookup table's default) so a future refinement could let the LLM argue for a non-default severity in edge cases — but `ASSUMPTION`: for this prototype, `compliance_service.py` **always assigns the rule's default severity**; allowing the LLM to deviate from the PDF's fixed severity table would risk contradicting an explicit requirement, so this is deliberately not exposed as an LLM decision.

### 7.2 Representation of each violation

As per `Violation` schema (§6.1) and API shape (§4.4): `severity`, `start_time_seconds` (via `segment_id` join), `quote` (verbatim, validated per §5.4), `explanation` (one sentence, for the manager's context), `confidence` (0–1, from the LLM's self-reported confidence, surfaced in the UI as a secondary signal — **not** used to auto-filter what's shown; even low-confidence flags are shown to the manager, who is the actual adjudicator).

### 7.3 Deterministic vs. contextual evaluation

- R1 uses **only** `Call.start_time` metadata — no transcript access needed, runs instantly, cannot be "talked around" by phrasing (a judging-criterion-relevant design choice: Critical/High rules should not be gameable by clever LLM prompting on the input side, though R1 isn't gameable by definition since it's metadata).
- R2–R6 require contextual judgment that a keyword list would miss (PDF's own stated reasoning) — e.g., R3 needs to distinguish the *agent* threatening police from a *borrower* mentioning police; R4 needs to recognize "talking to a family member who answered the phone" as a breach pattern even without a single trigger keyword. This is why the LLM receives the **full labelled transcript**, not isolated lines — context across the whole call matters for correct rule application.

### 7.4 Scoring formula

`ASSUMPTION` — the PDF requires "every call gets a 0 to 100 compliance score" and "the score updates" on manager review, but does not specify a formula. This is the single most clearly-flagged recommendation in the plan, chosen for transparency and defensibility in a 10-minute demo (a judge can mentally verify it):

```
base_score = 100

For each violation where review.decision != 'false_alarm':
    deduction = severity_weight(violation.severity)
    base_score -= deduction

severity_weight:
    critical → 30
    high     → 15
    medium   → 7
    low      → 3

score = max(0, min(100, base_score))
```

- **Provisional (pending) flags count toward the score** at full weight — `ASSUMPTION`: an un-reviewed Critical flag should visibly tank a score so it surfaces at the top of the Call List sorted-by-score view (directly serving "point the manager to the exact moment something went wrong"); the alternative (only confirmed violations affect score) would hide risk behind a review backlog, which seems to work against the system's stated purpose.
- **Confirming** a flag: **NO change to score** — the deduction was already applied when the violation was created (pending violations count at full weight). Confirmation is purely for audit trail. This is a critical consistency rule: the score is the same before and after confirmation.
- **False alarm**: that violation's deduction is **removed**, score recalculates upward. This is the ONE state transition that visibly changes the score, demonstrated live in the demo story.
- **Score recalculation** is triggered synchronously within `POST /api/violations/{id}/review`. The new `Call.score` is persisted in the same DB transaction as the Review row, and returned in the same API response — the frontend never needs a second round-trip to show the updated ScoreGauge.
- **Score consistency across all views:** `Call.score` is the single source of truth. All endpoints (call list, call detail, analytics/summary, risky-callers) read from `Call.score`. Recalculation updates this field, so all views are instantly consistent without cache invalidation.
- **Repeated violations** — `ASSUMPTION` (amended; the original rule was "each occurrence is a separate `Violation` row and deducts independently, rather than deduplicating by rule code"): distinct incidents still deduct independently — three separate R3 threats in one call are worse than one — but *fragments of one incident* deduct once. Whisper splits one sentence across several transcript segments at arbitrary points and the compliance LLM may report each fragment (or the same segment twice), so counting every row made the score depend on segmentation rather than on what the agent said. Every evidence row is still stored and shown; only the deduction is shared. See "Incident grouping" below.

#### Incident grouping (implemented in `scoring_service.evaluate_violations`)

The formula is unchanged (100 minus severity weights, clamped to [0, 100]); this only decides which stored rows carry a deduction. Rows of the same call and rule form one *incident* when either:

1. **Exact duplicate** — they cite the same transcript segment (identical findings, same rule + segment + quote, are additionally not persisted twice by `pipeline.py`); or
2. **Adjacent fragments** — they cite *consecutive* segments (`seq` differs by exactly 1), both segments have the **same speaker**, the gap between one segment's `end_time` and the next one's `start_time` is **at most `MAX_INCIDENT_GAP_SECONDS` = 1.0 s** (overlap counts as no gap), and an incident spans **at most `MAX_INCIDENT_SEGMENTS` = 3** segments. Longer runs are split greedily left to right (4 consecutive → 3 + 1, so 2 deductions).

`ASSUMPTION` — **both thresholds are conservative starting values, not validated.** No timestamped Whisper transcripts were available to measure real inter-segment gaps, so 1.0 s and 3 segments were chosen to merge less, not more. Revisit them with measured data.

**Never merged:** findings under *different* rule codes (R2 abuse + R3 threat, R3 + R4, etc. all count independently, even on the same segment); R1; rows with no transcript segment; and any row whose segment lacks a usable `seq`, speaker or timestamps (those fall back to counting separately; they can still be merged as exact duplicates, which needs only the segment id). R3 and R4 findings on the same segment are *flagged to the manager* (`overlapping_rule_codes`) but both still count — whether they describe one act or two is the manager's call (mark one a false alarm). **Final policy:** when both rules are independently supported by the evidence, both deductions apply; they are never merged. A manager who judges they describe one act marks one finding a false alarm. This does not fix incorrect rule classification, which is a separate issue.

**Review states.** A row is eligible to count unless it is a `false_alarm`, or it is flagged `needs_speaker_review` and still `pending`. An incident deducts **once if at least one row is eligible** (the heaviest eligible row carries it, ties broken by segment order then id); if none is eligible it deducts nothing. Grouping is computed from all rows regardless of review state, so an incident's identity never changes when a row is reviewed — only which row carries the deduction. Dismissing the carrying row therefore hands the deduction to another fragment; dismissing every fragment removes it. Confirming a row does not change eligibility, except that confirming a `needs_speaker_review` row makes it eligible (this is the one case where confirming changes the score).

**Visibility.** `GET /api/calls/{id}/violations` returns, per row, `counts_toward_score`, `incident_group_id` (null when the row is alone) and `overlapping_rule_codes`, computed by the same function as the score so they cannot disagree. The call-review page notes which flags are fragments of one incident and which row carries the deduction.

**Known limitations.** Two distinct but adjacent threats ("police…" and "jail…" said back-to-back) merge into one deduction when Whisper splits them into consecutive segments. Conversely, if Whisper puts two incidents in one segment they still count once, as before. Unreliable speaker labels can prevent a merge (the safe direction). Grouping removes saturation at 0, so remaining LLM run-to-run variance shows up more clearly in the score; it does not reduce that variance. Changing the thresholds requires re-scoring existing calls.

**Running the tests.** Backend (from `backend/`): `python -m pytest -q` — scoring policy in `tests/unit/test_scoring_service.py`, API fields in `tests/integration/test_calls_api.py`, pipeline persistence in `tests/integration/test_pipeline.py`. Frontend (from `frontend/`): `npx vitest run`, `npx tsc --noEmit`, `npm run build`.
- **Score boundaries**: clamped to [0, 100]; a call with many critical violations simply floors at 0 rather than going negative.
- **Recalculation trigger**: any `POST /api/violations/{id}/review` call recomputes and persists `Call.score` synchronously within the same request/transaction — the API response in §4.4 returns the new score immediately, so the frontend never needs a second round-trip to show the updated `ScoreGauge`.

**Explicitly flagged as a recommendation, not a requirement:** the exact weight values (30/15/7/3) and the "pending counts at full weight" policy. The README must state this plainly so a reviewer doesn't mistake it for a spec requirement (directly serves RQ-19 and judging criterion 4's spirit of honesty).

---

## 8. Roles and Access Filtering

### 8.1 Role definitions and scope

| Role | `Call` visibility | `User`/filter visibility | Analytics visibility |
|---|---|---|---|
| **Caller** | Only calls where `call.caller_id == active_user_id` | None (no office/caller filter controls shown) | Only their own aggregate numbers (no office/caller breakdown charts) |
| **Manager** | Calls where `call.caller_id IN (SELECT id FROM app_user WHERE manager_id == active_user_id)` | Caller filter limited to their direct reports | Office-scoped to their own office; no cross-office comparison |
| **Head of Collections** | All calls, any office | All offices/callers in filters | Full cross-office breakdown, all charts |

### 8.2 Identifying each caller's manager and office

Both relationships live directly on `app_user`: `app_user.manager_id` (self-referencing FK) and `app_user.office_id`. `access_control.py`'s core function:

```python
def visible_caller_ids(role: str, active_user_id: str) -> set[str] | None:
    """Returns None for 'no restriction' (head), or an explicit set of caller_ids otherwise."""
    if role == "head":
        return None
    if role == "manager":
        return {u.id for u in db.query(AppUser).filter(AppUser.manager_id == active_user_id)}
    if role == "caller":
        return {active_user_id}
```

Every Call/Violation/Analytics query calls this once and applies `.filter(Call.caller_id.in_(ids))` when `ids is not None` — a single, testable chokepoint (§10.3 covers its unit tests directly).

### 8.3 No-login role switcher as prototype mechanism, not authentication

- `X-Role` and `X-User-Id` are **plain HTTP headers set by the frontend**, not derived from any session/token/cookie — this is explicitly a demo convenience, not security.
- `deps.py`'s `get_role_context()` dependency reads these headers, validates `X-User-Id` actually exists and actually has the claimed `X-Role` (prevents a trivially malformed request, but **not** a deliberately spoofed one — there is no secret involved).
- The README **must** state in plain language: "Role selection is for demo purposes and is not authentication. A production version requires real login (see Bonus features, explicitly out of scope per the assignment)." This satisfies both RQ-19 and the PDF's own framing of real login as bonus-only.

### 8.4 Enforcement in backend queries, not just frontend

Every relevant endpoint's filtering table, confirming backend-side enforcement (the actual mechanism, not merely "the UI hides it"):

| Endpoint | Enforcement |
|---|---|
| `GET /api/calls` | `visible_caller_ids()` applied as a SQL `WHERE caller_id IN (...)` before any other filter |
| `GET /api/calls/{id}` | 403 (rendered as 404-style "not found" per §3.5.3's defense-in-depth choice) if `call.caller_id` not in `visible_caller_ids()` |
| `GET /api/calls/{id}/transcript`, `/violations` | Same check, inherited from the parent call's visibility |
| `POST /api/violations/{id}/review` | 403 if the violation's call isn't visible to this role; **additionally**, `role` must be `manager` or `head` — a caller identity gets 403 even for their own call's violations (read-only for callers, per §3.5.3) |
| `GET /api/analytics/*` | All aggregate queries apply the same `visible_caller_ids()` filter before aggregating; `breaches-by-office` additionally requires `role in ('manager','head')` |

This table is the actual acceptance test matrix for RQ-01/judging-criterion-2 — §10.4 turns each row into an integration test.

---

## 9. Analytics and Evaluation

### 9.1 Dashboard metrics and chart data contracts

Already specified as API shapes in §4.4; summarized by chart:

- **Summary cards**: `total_calls`, `avg_score`, `critical_breach_count`, `word_error_rate` — all role-scoped per §8.
- **Breaches by rule**: `[{rule_code, count, critical_count, high_count, ...}]` — bar chart, x-axis = R1..R6.
- **Breaches by office**: same shape keyed by `office_id`/`office_name` — manager/head only.
- **Top-5 riskiest callers**: see §9.3.
- **Evaluation table**: precision/recall/F1/support per rule, see §9.4.

### 9.2 Aggregation by caller, office, rule

All three are plain SQL `GROUP BY` queries over `violation JOIN call JOIN app_user/office`, filtered by `visible_caller_ids()` first (§8.4) — no separate code path per role, just a different filter set applied to the same aggregation query, which keeps the three role views numerically consistent with each other (a manager's "breaches by rule" total for their team must be a subset of the head's total — tested directly in §10.4).

### 9.3 Top-5-riskiest-callers calculation

```
risk_score(caller) = Σ over caller's calls, violations where decision != 'false_alarm':
    severity_weight(violation.severity)   -- same weights as §7.4

Rank callers by risk_score descending, take top 5.
Tie-break: higher count of 'critical' violations wins; then alphabetical by name (stable, deterministic for demo reproducibility).
```

`ASSUMPTION`: reusing the scoring weights from §7.4 keeps "risky caller" and "low call score" conceptually the same signal, which is easier to explain live in a demo than a second, different formula. Explicitly flagged in README as a recommendation.

### 9.4 Precision, recall, false positives/negatives, WER

- **Ground truth source**: `data/ground_truth.json`, hand-labelled per the 20 real calls, format:
  ```json
  {"CALL-2041": {"violations": [{"rule_code": "R3", "present": true}, ...]}}
  ```
  one entry per rule per call (so "clean" calls are represented as all-`false`, not just absent).
- **Per-rule computation** (binary presence/absence of each rule **per call**, not per individual violation instance — `ASSUMPTION`: counting "did R3 occur at all in this call" is both what a hand-labeller can realistically judge and what §1.7's "every rule must appear at least twice" constraint is counting against):
  - True Positive: system flagged rule X on a call where ground truth says rule X is present.
  - False Positive: system flagged rule X where ground truth says absent.
  - False Negative: ground truth says present, system didn't flag it.
  - True Negative: both agree absent.
  - `precision = TP / (TP + FP)`, `recall = TP / (TP + FN)`, `F1 = 2PR/(P+R)`, `support = TP + FN` (count of ground-truth-positive calls for that rule).
- **Word Error Rate**: computed via standard Levenshtein-distance-based WER (`jiwer` library) between each of the 3 hand-typed reference transcripts and the system's transcript for that same call, averaged.
- **Honest handling of undefined/insufficient metrics** (judging criterion 4, directly):
  - If `TP + FP == 0` (rule never flagged by the system), precision is undefined — displayed as `"—"` with a tooltip "Not flagged in this sample," **never silently shown as 0 or 1**.
  - `support < 3` → `low_sample_size: true` flag, shown as a ⚠ in the UI (§3.5.4).
  - The evaluation endpoint's `notes` field (§4.4) states explicitly that these numbers come from only 20 calls and should not be read as production-grade statistical confidence — this sentence is copied verbatim into the README's "Accuracy numbers" section.

### 9.5 Processing the 5 unseen calls

Uploaded through the **exact same** `/api/uploads/batch` → `pipeline.process_call` path as any other call (no special-casing, which is itself part of what's being demonstrated — "the system, not a canned demo"). They get `is_ground_truth=false`, so they appear in Call List/Dashboard breach counts and the live demo flow, but are **excluded** from the precision/recall table (no ground truth exists for them) — consistent with §6.4.


---

## 10. Data Preparation and Test Strategy

### 10.1 Creating the 20 real recordings and ground-truth labels

- `call_scripts.md` (already provided, 5 sample scripts) establishes the **pattern** to extend to 20: short (1–2 min) two-person role-plays, Agent/Borrower, mixing Hindi/English/Marathi/Kannada naturally, made-up names/amounts.
- Required coverage, derived directly from §1.7:
  - Every rule R1–R6 appears in **at least 2** of the 20 calls (R1 is metadata-only — doesn't need to appear *in dialogue*, just needs 2 calls whose `start_time` metadata is outside 8 AM–7 PM).
  - **At least 6 calls are clean** (no rule breaches at all).
  - Remaining ~12 calls distributed across the other 5 transcript-based rules, with natural overlap (a single heated call can plausibly contain R2+R3+R4 together, as the sample `call_02` already demonstrates).
- A **coverage-check script** (`scripts/check_label_coverage.py`) reads `data/ground_truth.json` and asserts: each rule code appears ≥2 times across all calls, ≥6 calls have zero `present:true` entries, and all 20 `call_id`s referenced have a corresponding `.wav` file — run as a pre-flight check before relying on the evaluation numbers, not as an afterthought.
- Each recording: two people, one Agent one Borrower, real voices on phone/laptop mic, **no TTS**, saved as `call_01.wav`…`call_20.wav` per the naming convention already established in `call_scripts.md`.
- 3 of the 20 are additionally **hand-typed** (full manual transcript) to serve as the WER reference set (§9.4).

### 10.2 Metadata CSV format, file naming, language coverage, audio-to-call matching

```csv
call_id,filename,caller_id,office_id,account_type,start_time,language
CALL-2041,call_01.wav,u-014,o-01,personal_loan,2026-10-12T20:10:00+05:30,hi
```

- `filename` is the explicit join key between the CSV row and the uploaded audio file (avoids relying on filename parsing conventions alone, which is fragile — `ASSUMPTION`: more robust than assuming `call_01.wav` always maps to the first CSV row).
- Language coverage across the 20: a mix of pure Hindi, pure English, and explicitly code-mixed calls, matching the PDF's stated reality ("often mixed in the same sentence") — at minimum one call per office and a spread across both account types.

### 10.3 Unit tests

| Area | What's tested |
|---|---|
| `rules.py` / R1 check | Call at 7:59 AM → no violation; 8:00 AM exactly → no violation (boundary inclusive per "between 8 AM and 7 PM"); 7:00 PM exactly → boundary behavior explicitly tested and documented (`ASSUMPTION`: 7 PM exclusive, i.e. a call starting exactly at 19:00:00 is the last compliant minute, not already a breach — this boundary choice is called out in a code comment since the PDF's wording is ambiguous at the exact minute) |
| `scoring_service.py` | Known violation sets → expected score; false-alarm removal recalculates correctly; score clamps at 0 and 100; empty-violations call scores 100 |
| `access_control.visible_caller_ids` | Each of the 3 roles returns the expected set given a fixed seeded org chart |
| `upload_service` CSV validation | Missing column, bad timestamp, unknown office_id, duplicate call_id — each rejected with the right reason, valid rows still accepted |
| `llm_service` response validation | Hallucinated quote dropped; invalid segment_id dropped; malformed JSON triggers retry path (mocked LLM client) |
| `evaluation_service` | Precision/recall/F1 computed correctly against a small fixture ground-truth + fixture violations, including the "undefined precision" case |

### 10.4 Integration/API tests

- Each row of §8.4's enforcement table as a dedicated test: request as caller/manager/head, assert the returned `Call`/`Violation`/analytics rows match the expected visible set exactly (not a superset).
- `POST /api/uploads/batch` with a mix of valid/invalid rows → assert correct `accepted`/`rejected` split.
- `POST /api/violations/{id}/review` → assert `Review` row created, `Call.score` recalculated and persisted, and a second call for the same violation **updates** (not duplicates) the review.
- Duplicate `call_id` upload → 409-equivalent rejection in the batch response.
- Manager vs head "breaches by rule" consistency: a manager's total for their own office must equal the head's office-filtered total for that same office (cross-role numeric consistency check).

### 10.5 Frontend component/unit tests (Vitest + React Testing Library)

- `ReviewPanel` is **not rendered** when `role === 'caller'` (not just visually hidden — asserted via `queryByRole` returning null).
- `AudioPlayer.seekTo()` is called with the correct value when a `FlagList` "Play" button is clicked (mocked audio element).
- `SeverityBadge`/`SentimentBadge` render correct text+color class for each enum value (exhaustive over the 4 severities / 3 sentiments).
- `DataTable` empty/loading/error states render the right sub-component.
- Role switch updates `RoleContext` and triggers the expected query-key change (verified via a mocked `useCalls` call count).

### 10.6 End-to-end tests (Playwright)

Mapped directly to the PDF's demo story (§1.10) and judging criteria — these are the tests that matter most for "the story works without errors":

1. **Clean-run E2E**: seed DB → open Call List as Head → sort by score → open the worst call → see flags → click a Critical flag's Play button → assert audio `currentTime` near the violation's timestamp → Confirm the flag → assert score updates on screen → navigate to Dashboard → assert the confirmed-flag count reflects the change.
2. **Role-filtering E2E**: switch to a specific caller identity → assert Call List shows only that caller's calls (count matches a known seeded fixture) → attempt to directly navigate to another caller's `call_id` URL → assert "not found," not their data.
3. **Upload E2E**: upload a small fixture batch (2–3 files + CSV, one intentionally mismatched) → assert the mismatched row is flagged, the valid rows process to `ready`, and both become openable from Call List.
4. **False-alarm E2E**: open a call with a flag, mark it False alarm with a required comment → assert score increases → assert the violation still appears (greyed, not removed) → reload the page → assert the state persisted.

### 10.7 Failure-path tests

- Transcription provider returns an error (mocked) → `Call.status='failed'`, UI shows retry, `reprocess` succeeds once the mock is fixed.
- LLM returns malformed JSON twice → `Call.status='failed'` with the correct `failure_reason`, no silent fallback to empty violations presented as "clean."
- Audio file missing from disk (simulated) → Call Review degrades gracefully (§3.5.3's partial-degradation state), transcript/flags still usable.

---

## 11. Repository Structure

```
shaha-finlease-compliance/
├── README.md
├── MASTER_IMPLEMENTATION_PLAN.md
├── .env.example
├── .gitignore
├── docker-compose.yml
├── Makefile                          # make seed / make dev / make test
│
├── backend/
│   ├── pyproject.toml
│   ├── alembic.ini
│   ├── alembic/
│   │   ├── env.py
│   │   └── versions/                  # migration files
│   ├── app/
│   │   ├── main.py
│   │   ├── config.py
│   │   ├── db.py
│   │   ├── models.py
│   │   ├── schemas.py
│   │   ├── deps.py
│   │   ├── rules.py
│   │   ├── routers/
│   │   │   ├── uploads.py
│   │   │   ├── calls.py
│   │   │   ├── violations.py
│   │   │   ├── analytics.py
│   │   │   └── meta.py
│   │   └── services/
│   │       ├── upload_service.py
│   │       ├── pipeline.py
│   │       ├── transcription_service.py   # Groq hosted Whisper STT (+ manual Sarvam fallback)
│   │       ├── llm_service.py             # Groq openai/gpt-oss-120b (speaker labelling + compliance)
│   │       ├── scoring_service.py
│   │       ├── evaluation_service.py
│   │       └── access_control.py
│   ├── scripts/
│   │   ├── seed_db.py
│   │   ├── evaluate.py
│   │   └── check_label_coverage.py
│   ├── data/
│   │   ├── recordings/                 # call_01.wav .. call_20.wav (20 real + 5 unseen added on demo day)
│   │   ├── metadata.csv
│   │   └── ground_truth.json
│   └── tests/
│       ├── unit/
│       ├── integration/
│       └── fixtures/
│
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.ts
│   ├── src/
│   │   ├── main.tsx
│   │   ├── App.tsx                     # routes
│   │   ├── context/RoleContext.tsx
│   │   ├── api/                         # typed fetch wrappers per router in §4.3
│   │   ├── hooks/                        # useCalls, useCall, useDashboard, useSubmitReview, useUploadBatch
│   │   ├── components/
│   │   │   ├── layout/
│   │   │   ├── ui/
│   │   │   ├── audio/
│   │   │   ├── call-review/
│   │   │   ├── upload/
│   │   │   └── dashboard/
│   │   ├── pages/
│   │   │   ├── UploadPage.tsx
│   │   │   ├── CallListPage.tsx
│   │   │   ├── CallReviewPage.tsx
│   │   │   └── DashboardPage.tsx
│   │   └── styles/tokens.css
│   └── tests/                            # Vitest unit tests
│
└── e2e/
    └── playwright/                        # tests from §10.6
```

### 11.1 Environment variables and secrets

`.env.example`:
```bash
# Database
DATABASE_URL=sqlite:///./data/app.db

# Hosted Transcription Provider: "groq" (default) | "sarvam" (manual fallback)
TRANSCRIPTION_PROVIDER=groq

# Groq Hosted Transcription Model (Free tier: 20 RPM, 2000 RPD, 7200 audio-sec/hr)
GROQ_TRANSCRIPTION_MODEL=whisper-large-v3-turbo

# GroqCloud API key (Backend only — free tier, no credit card required)
# Get at: https://console.groq.com
GROQ_API_KEY=

# LLM model on Groq (Sole primary LLM for R2–R6, sentiment, and speaker labelling)
# Free tier: 30 RPM, 8,000 TPM, 1,000 RPD — requires sequential pacing (≥20s delay)
GROQ_LLM_MODEL=openai/gpt-oss-120b

# Sarvam AI (Optional manual hosted transcription alternative with trial credits)
# Used ONLY if TRANSCRIPTION_PROVIDER=sarvam is manually selected. Not auto-enabled.
# Get at: https://www.sarvam.ai
SARVAM_API_KEY=

# Frontend CORS origin
CORS_ORIGIN=http://localhost:5173

# Guard: seed script only runs if DATABASE_URL contains this string
SEED_ALLOWED_URL_SUBSTRING=app.db
```

`.gitignore` includes: `.env`, `backend/data/audio/*.wav` (real recordings are a deliverable submitted separately per RQ-19, not necessarily committed into Git history if the repo is public — `ASSUMPTION`, flagged for the team to confirm based on submission instructions), `node_modules/`, `__pycache__/`, `*.db`.

### 11.2 One-command startup

```
make seed   # alembic upgrade head && seed ~2000 calls + 20 real recordings + ground truth
make dev    # docker-compose up (api on :8000, web on :5173)
```

README documents both as the **only** two commands needed from a fresh clone (plus copying `.env.example` → `.env` and filling in API keys).

---

## 12. Ordered Phase-Wise Implementation Roadmap

### Overview

| Phase | Name | End state |
|---|---|---|
| 0 | Document audit and decisions | Plan locked, API accounts created, recordings scheduled |
| 1 | Repository and environment setup | Both servers start, health check passes |
| 2 | Database and seed data | ~2,000 call rows, 20 real call metadata seeded |
| 3 | Backend APIs (metadata, calls, role filtering) | Call List API works with role filtering enforced in SQL |
| 4 | Frontend foundation | Call List renders real data, role switcher works |
| 5 | Audio upload and metadata ingestion | 20 WAV files uploadable, processing status tracked |
| 6 | Transcription and speaker attribution | Transcript segments stored with Agent/Borrower labels |
| 7 | Compliance detection and scoring | R1–R6 detected, scores computed |
| 8 | Call review page and manager workflow | Click flag → play from that moment; Confirm/False alarm works |
| 9 | Dashboard and evaluation | Precision/recall/WER shown honestly |
| 10 | End-to-end validation and delivery | README complete, all tests pass, demo rehearsed |

---

### Phase 0 — Document Audit and Technical Decisions

**Objective:** Lock all architectural decisions before writing any code.

**Tasks:**
1. Read all 4 project documents (complete ✓).
2. Verify GroqCloud model availability and free-tier quotas: `whisper-large-v3-turbo` (20 RPM, 2,000 RPD, 7,200 audio-sec/hr) and `openai/gpt-oss-120b` (30 RPM, 8,000 TPM, 1,000 RPD) (complete ✓).
3. Verify Sarvam AI manual fallback credits (₹100–₹1,000 non-expiring trial signup credits, ₹30/hr; opt-in only) (complete ✓).
4. Confirm hosted-only STT architecture: remove `faster-whisper` and verify no local ASR or Ollama models are used (complete ✓).
5. Design call scripts for calls 06–20 (assign to recording team).
6. Create `data/ground_truth.json` skeleton (20 calls × 6 rules, all `false` initially).
7. Plan recording sessions: R1 ≥2 calls (metadata only), R2–R6 ≥2 calls each, ≥6 clean calls.

**Acceptance criteria:**
- [ ] All 4 documents read and requirements extracted.
- [ ] GroqCloud account created, API key obtained, Free-Tier 8,000 TPM limit acknowledged.
- [ ] Ground truth skeleton file created.
- [ ] Recording plan covers all required coverage.

**Move to Phase 1 when:** Ground truth skeleton exists, all scripts designed, API accounts created.

---

### Phase 1 — Repository and Environment Setup

**Objective:** Monorepo with both servers starting and passing a health check.

**Assignment requirements covered:** RQ-17 (API keys never in repo), RQ-18 (one-command startup).

**Files to create:**
- `.gitignore`, `.env.example`, `docker-compose.yml`, `Makefile`
- `backend/pyproject.toml`, `backend/app/main.py`, `backend/app/config.py`, `backend/app/db.py`
- `frontend/package.json`, `frontend/vite.config.ts`, `frontend/tailwind.config.ts`, `frontend/src/main.tsx`

**Key tasks:**

Backend:
1. `pip install fastapi uvicorn sqlalchemy alembic pydantic-settings groq jiwer python-multipart httpx`
2. `main.py` with `GET /api/health → {"status": "ok"}` and CORS for `CORS_ORIGIN`.
3. `config.py` with `pydantic-settings Settings` reading `.env`.
4. `db.py` with SQLAlchemy engine + `get_db` dependency.
5. `alembic init alembic` and configure `env.py`.

Frontend:
1. `npm create vite@latest . -- --template react-ts`
2. `npm install tailwindcss @tanstack/react-query react-router-dom recharts`
3. `tokens.css` with design system tokens from §3.2.
4. `App.tsx` with React Router 6 routes for 4 screens (placeholder pages).

Docker:
1. `Dockerfile` for backend (uvicorn, port 8000) and frontend (vite dev, port 5173).
2. `docker-compose.yml` with api + web services, volume for `data/`.

**Commands:**
```bash
curl http://localhost:8000/api/health   # → {"status": "ok"}
# Navigate to http://localhost:5173/   # → loads without errors
docker-compose up --build               # both services start
```

**Acceptance criteria:**
- [ ] `GET /api/health` returns 200 `{"status": "ok"}`.
- [ ] `http://localhost:5173/` loads without console errors.
- [ ] `docker-compose up` starts both services.
- [ ] `.env` is gitignored; `.env.example` is committed with all variables documented.
- [ ] No API keys in any committed file.

---

### Phase 2 — Database and Seed Data

**Objective:** Working database with 2,000+ metadata-only calls, 68 users, 3 offices, 6 rules.

**Assignment requirements covered:** RQ-12 (7 entities), RQ-13 (~2,000 calls), RQ-14 (20 ground-truth calls).

**Files to create:**
- `backend/app/models.py` — all ORM models
- `backend/app/rules.py` — R1–R6 constants and R1 check function
- `backend/alembic/versions/001_initial_schema.py`
- `backend/scripts/seed_db.py`
- `backend/scripts/check_label_coverage.py`
- `backend/data/metadata.csv`, `backend/data/ground_truth.json`

**Key tasks:**
1. Define all ORM models in `models.py` exactly matching the schema in §6.1 (with nullable `segment_id` on `violation`).
2. `alembic revision --autogenerate -m "initial_schema"` and `alembic upgrade head`.
3. `seed_db.py`: wipe-with-guard → 3 offices → 1 head + 7 managers + 60 callers → 6 rules → ~1,980 metadata-only calls → 20 real-call rows (has_audio=1, is_ground_truth=1, status=queued).
4. `check_label_coverage.py`: assert each rule ≥2 `present:true` in ground_truth.json, ≥6 calls have all-false, all 20 call_ids have a `.wav` file.

**Commands:**
```bash
cd backend
alembic upgrade head
python scripts/seed_db.py
# Expected output: "Seeded 3 offices, 68 users, 6 rules, ~1980 metadata calls, 20 real calls"
python -c "from app.db import SessionLocal; from app.models import Call; db=SessionLocal(); print(db.query(Call).count())"
# Expected: ~2000
```

**Acceptance criteria:**
- [ ] `alembic upgrade head` succeeds.
- [ ] `seed_db.py` completes; `SELECT COUNT(*) FROM call` ≈ 2,000.
- [ ] `SELECT COUNT(*) FROM call WHERE is_ground_truth=1` = 20.
- [ ] `SELECT COUNT(*) FROM app_user WHERE role='caller'` = 60.
- [ ] `SELECT COUNT(*) FROM rule` = 6.
- [ ] Severity constraint: inserting violation with `severity='invalid'` fails.
- [ ] `seed_db.py` can be run twice and produces identical row counts (idempotent).

---

### Phase 3 — Backend APIs (Metadata, Calls, Role Filtering)

**Objective:** All non-AI REST endpoints working with correct role-based filtering enforced in SQL.

**Assignment requirements covered:** RQ-01 (role views), RQ-11 (4 screens' data), RQ-12 (entities).

**Files to create:**
- `backend/app/deps.py` — `get_role_context()`
- `backend/app/services/access_control.py` — `visible_caller_ids()`
- `backend/app/services/scoring_service.py` — `compute_score()`
- `backend/app/routers/meta.py`, `calls.py`, `violations.py`, `analytics.py` (stubs), `media.py`
- `backend/tests/unit/test_access_control.py`
- `backend/tests/integration/test_calls_api.py`

**Key tasks:**
1. `deps.py`: `get_role_context()` reads `X-Role`/`X-User-Id` headers, validates user exists + role matches DB.
2. `access_control.py`: `visible_caller_ids(role, active_user_id, db) → set[str] | None`.
3. `calls.py`: `GET /api/calls` (paginated, filtered, role-scoped), `GET /api/calls/{id}` (403-as-404 if not visible), `/transcript`, `/violations`, `POST /reprocess`.
4. `violations.py`: `POST /api/violations/{id}/review` — creates/updates Review row, calls `compute_score()`, returns new score.
5. `media.py`: `GET /api/media/{call_id}` with HTTP Range support.

**Acceptance criteria:**
- [ ] `GET /api/calls` with head role returns ~2,000 calls.
- [ ] Same endpoint with a specific caller ID returns only that caller's calls.
- [ ] Direct access to another caller's call_id returns 404 (not 403).
- [ ] `POST /api/violations/{id}/review` with role=caller returns 403.
- [ ] All list responses have `{items, total, page, page_size}` envelope.
- [ ] `GET /api/media/{call_id}` returns 206 Partial Content for range requests.
- [ ] All unit and integration tests pass.

---

### Phase 4 — Frontend Foundation

**Objective:** Four-screen SPA rendering real data. Role switcher changes visible data.

**Assignment requirements covered:** RQ-01 (role views), RQ-11 (4 screens).

**Key files:**
- `frontend/src/context/RoleContext.tsx`
- `frontend/src/api/calls.ts`, `meta.ts`
- `frontend/src/components/layout/AppShell.tsx`, `RoleSwitcher.tsx`
- `frontend/src/components/ui/` — all shared components (SeverityBadge, ScoreGauge, DataTable, StatusChip, etc.)
- `frontend/src/pages/CallListPage.tsx` — fully functional
- Stub pages: UploadPage, DashboardPage, CallReviewPage

**Key tasks:**
1. `RoleContext.tsx`: `{role, activeUserId}`, persisted to localStorage, injects `X-Role`/`X-User-Id` into every API request.
2. `RoleSwitcher.tsx`: 3-way segmented control + identity dropdown (fetches `GET /api/meta/users?role=...`).
3. `CallListPage.tsx`: `useCalls()` TanStack Query hook, `DataTable` with role-adjusted columns, `FilterBar`, default sort by score ascending, row click → `/calls/:callId`.

**Acceptance criteria:**
- [ ] `/calls` loads and shows real call data.
- [ ] Switching role identity changes the row count (verified by network tab `total` field).
- [ ] Default sort is ascending by score (worst calls first).
- [ ] RoleSwitcher state survives page refresh.
- [ ] Loading / empty / error states render correctly.
- [ ] No console errors.

---

### Phase 5 — Audio Upload and Metadata Ingestion

**Objective:** Upload screen accepts 20 WAV/MP3 files + CSV, validates, submits, shows per-file status.

**Assignment requirements covered:** RQ-02 (batch upload).

**Key tasks:**
1. `upload_service.py`: CSV validation (required columns, ISO-8601 timestamp, known office/caller IDs), audio validation (extension, <100 MB), filename↔CSV matching, `Call` row creation.
2. `POST /api/uploads/batch`: multipart, returns 202 with `{batch_id, accepted, rejected}`.
3. Duplicate `call_id` → rejected with `"duplicate_call_id"`, no overwrite.
4. `GET /api/uploads/batch/{batch_id}` for polling.
5. `UploadPage.tsx`: two dropzones + validation + `BatchUploadTable` + polling loop.

**Acceptance criteria:**
- [ ] 20 valid WAV files + correct CSV → 20 accepted.
- [ ] One mismatched audio file → rejected, others proceed.
- [ ] Re-uploading same `call_id` → rejected (not silently duplicated).
- [ ] CSV row with bad timestamp → that row rejected, others proceed.
- [ ] File > 100 MB → rejected.
- [ ] Audio files stored in `backend/data/audio/`.

---

### Phase 6 — Transcription and Speaker Attribution

**Objective:** Real audio files produce timestamped TranscriptSegment rows with Agent/Borrower labels. WER computed.

**Assignment requirements covered:** RQ-03 (transcription + WER), RQ-04 (timestamps), RQ-05 (play from flag moment), RQ-16 (hosted transcription only).

**Prerequisites:** GroqCloud API key configured. At least 3 real recordings available.

**Key tasks:**
1. `transcription_service.py`: hosted Groq Whisper API (`whisper-large-v3-turbo`) implementation; manual Sarvam AI hosted fallback interface (zero local models).
2. `llm_service.py` — speaker labelling: given raw segments, returns `[{speaker, start, end, text}]` using GroqCloud `openai/gpt-oss-120b`.
3. `pipeline.py` — Steps 0 (R1 check) + 1 (Groq STT) + 2 (Groq LLM speaker label); creates TranscriptSegment rows.
4. Retry & quota policy: 1 retry with backoff; `status='failed'` with explicit `failure_reason` on 2nd failure (never silent clean result; strictly NO silent LLM model substitution).
5. `evaluate.py` WER section: `jiwer.wer(reference, hypothesis)` for 3 reference transcripts.

**Acceptance criteria:**
- [ ] `GET /api/calls/CALL-0001/transcript` returns ≥5 segments with speaker labels.
- [ ] First segment labelled 'agent' (agent opens the call per all provided scripts).
- [ ] `start_time` and `end_time` are non-zero floats (real STT timestamps).
- [ ] WER computed and printed for all 3 reference calls (value is between 5% and 50% — not 0% or 100%).
- [ ] STT API failure → `call.status='failed'`, `failure_reason='transcription_failed'`.
- [ ] STT uses Groq hosted API exclusively (`whisper-large-v3-turbo`); zero local models or Ollama loaded.

---

### Phase 7 — Compliance Detection and Scoring

**Objective:** All 6 rules detected, violations stored with exact quotes and timestamps, scores computed.

**Assignment requirements covered:** RQ-04 (flags), RQ-07 (score), RQ-09 (R1–R6), RQ-10 (4 severities).

**Key tasks:**
1. `llm_service.analyse_compliance()`: uses GroqCloud `openai/gpt-oss-120b` exclusively. Prompt from §5.3 (R2–R6 only). Post-processing validates quotes + segment_ids. Severity always from `rules.py`. Sequential request pacing enforced to respect 8,000 TPM limit. If quota exceeded, fails explicitly with `failure_reason='LLM quota exceeded or model unavailable: openai/gpt-oss-120b'` (strictly no silent model substitution).
2. `pipeline.py` — Steps 3 (compliance+sentiment) + 4 (scoring). Creates Violation rows, updates Call.sentiment/agent_stayed_calm/score/status.
3. `scoring_service.compute_score()`: deterministic Python, uses `SEVERITY_WEIGHTS` from rules.py.
4. `POST /api/violations/{id}/review` fully implemented: creates/updates Review row, recalculates score atomically, returns new score.

**Unit tests — required cases:**
- R1 boundary: 07:59 → no violation; 08:00 → no violation; 19:00 → violation.
- Score: 0 violations → 100; 1 critical → 70; 1 critical + false_alarm → 100; 4 criticals → 0 (clamped).
- Confirm does NOT change score.
- Hallucinated quote dropped; unknown segment_id dropped.
- Severity always from rules.py, never from LLM response.

**Acceptance criteria:**
- [ ] call_02: R3 + R4 violations detected (per call_scripts.md ground truth).
- [ ] call_03: R5 + R6 violations detected.
- [ ] call_04: R4 violation detected.
- [ ] call_01, call_05: 0 violations (clean calls).
- [ ] Violation `start_time_seconds` within ±2 seconds of actual spoken moment.
- [ ] Score computed correctly from formula.
- [ ] False alarm removes deduction and score persists on reload.
- [ ] Confirm does not change score.
- [ ] LLM malformed JSON twice → `status='failed'` (never silent clean result).
- [ ] LLM rate limit (HTTP 429) or unavailability triggers explicit failure with `failure_reason` stating 'openai/gpt-oss-120b' (no silent model substitution).

---

### Phase 8 — Call Review Page and Manager Workflow

**Objective:** Call Review screen fully functional. Manager can play audio, jump to flagged moments, confirm/reject flags, see score update. Must pass the demo story.

**Assignment requirements covered:** RQ-04, RQ-05, RQ-06, RQ-07.

**Key components:**
- `AudioPlayer.tsx`: wraps `<audio src="/api/media/{callId}">`, exposes `seekTo(seconds)`, play/pause, scrubber.
- `TranscriptPanel.tsx`: highlights active segment under playhead, auto-scroll, click-to-seek.
- `FlagList.tsx`: one card per violation, "▶ Play" → `seekTo(violation.start_time_seconds)`.
- `ReviewPanel.tsx`: rendered ONLY when `role !== 'caller'`; Confirm (optional comment) + False alarm (REQUIRED comment); optimistic update → ScoreGauge animates.
- `CallReviewPage.tsx`: 2-column layout, tabs (Transcript | Flags), states (loading / processing / audio-missing / error).

**Frontend tests — required:**
- `ReviewPanel` not in DOM (not just hidden) when `role='caller'`.
- False alarm submit disabled until comment non-empty.
- "▶ Play" calls `audio.currentTime = violation.start_time_seconds`.
- After Confirm submit: buttons replaced with "Confirmed by X at T" text.

**Acceptance criteria:**
- [ ] **RQ-05 core:** Click "▶ Play" on R3 flag → `audio.currentTime` within ±1s of `violation.start_time_seconds`.
- [ ] Confirm persists across page reload.
- [ ] False alarm: score increases immediately (ScoreGauge animates); violation stays visible (greyed).
- [ ] Caller role: no Confirm/False alarm buttons in DOM at all.
- [ ] R1 flag: "▶ Play" seeks to 0:00; quote reads "Call started at HH:MM IST..."
- [ ] Score gauge updates without page reload after review action.
- [ ] Audio file missing: AudioPlayer shows error inline; transcript/flags still render.

---

### Phase 9 — Dashboard and Evaluation

**Objective:** Dashboard fully functional with honest, independently verifiable numbers.

**Assignment requirements covered:** RQ-08 (dashboard + accuracy), RQ-03 (WER reported).

**Prerequisites:** All 20 real calls processed; `ground_truth.json` completely filled in.

**Key tasks:**
1. `evaluation_service.py`: per-rule precision/recall/F1/support against `ground_truth.json` (is_ground_truth=1 calls only). WER via `jiwer`. Undefined precision displayed as `null`.
2. `analytics.py` — all endpoints: summary, breaches-by-rule, breaches-by-office (manager/head only), risky-callers (top 5 by weighted risk score), evaluation.
3. `DashboardPage.tsx`: 4 MetricCards, 2 BreachBarCharts, RiskyCallerList, evaluation table with ⚠ for support<3.
4. `scripts/evaluate.py` must produce identical numbers to what the dashboard API returns.

**Honesty rules (non-negotiable):**
- Numbers computed from actual data, not hardcoded.
- Undefined precision (rule never flagged) → `"\u2014"` not 0 or 1.
- `support < 3` → ⚠ with tooltip "Low sample size — interpret with caution."
- Metadata-only calls excluded from score/violation aggregates.
- WER labelled "based on 3 calls."

**Acceptance criteria:**
- [ ] Dashboard loads with real data (no zeros or placeholders).
- [ ] `GET /api/analytics/evaluation` numbers exactly match `python scripts/evaluate.py` output.
- [ ] Rules with `support < 3` show ⚠ badge.
- [ ] Rule never flagged → precision shown as "—" not 0.
- [ ] Caller role: dashboard scoped to own calls only; breaches-by-office chart hidden.
- [ ] Manager/head numeric consistency: manager's breach total ≤ head's total for same office.

---

### Phase 10 — End-to-End Validation and Delivery

**Objective:** All mandatory features working end-to-end. README complete. Tests passing. Demo rehearsed.

**Assignment requirements covered:** All RQ-01 through RQ-20.

**Tasks:**
1. Run complete `make seed` from a clean machine. Time it (must complete <5 minutes excl. first docker pull).
2. Execute the full demo story (see §13.1) manually end-to-end. Note any errors.
3. Implement all Playwright E2E tests:
   - `demo-story.spec.ts`: full upload → open call → play flag → confirm → dashboard update → role switch flow.
   - `role-filtering.spec.ts`: caller sees only own calls; direct URL to other caller's call → "not found."
   - `upload.spec.ts`: 2 valid + 1 mismatched → mismatched flagged, valid process.
   - `false-alarm.spec.ts`: mark false alarm → score increases → violation stays visible → persists on reload.
4. Run `python scripts/check_label_coverage.py` — all assertions pass.
5. Secret scan: `git grep -r "GROQ_API_KEY" backend/ --include="*.py"` returns only `config.py` and no test files.
6. Write `README.md` per §13.2 structure.
7. Rehearse demo: 12 steps in §13.1, under 10 minutes.
8. Prepare 5 unseen calls through upload pipeline.

**Final acceptance checklist — all must pass before submission:**
- [ ] `make seed && make dev` from fresh clone starts in <5 minutes.
- [ ] `GET /api/health` returns 200.
- [ ] All 20 real calls have `status='ready'` after seeding.
- [ ] `python scripts/check_label_coverage.py` passes all assertions.
- [ ] All 12 demo story steps execute without errors.
- [ ] Role filtering verified via network tab (not just UI).
- [ ] All 8 mandatory features (Feature 1–8 in assignment) confirmed working.
- [ ] Precision/recall in README matches `scripts/evaluate.py` output exactly.
- [ ] No API keys in any committed file.
- [ ] `make test` passes all backend pytest + frontend vitest.
- [ ] `make e2e` passes all Playwright E2E specs.
- [ ] README contains all required sections (§13.2).
- [ ] Demo rehearsed and under 10 minutes.
- [ ] 5 unseen calls prepared and tested through upload pipeline.

---

## 13. Final Acceptance Checklist and Demo Script

### 13.1 Demo Story (10 minutes, exact sequence)

| Step | Action | What the judge sees |
|---|---|---|
| 1 | Switch to Head of Collections | All 3 offices, all calls visible in Call List |
| 2 | Upload 2–3 new calls + metadata CSV | Files validate; BatchUploadTable shows live processing status |
| 3 | Go to Call List, sort by score ascending | Worst calls (score 0–30) at top |
| 4 | Open CALL-0002 (Imran Khan, call_02 script) | 2-column layout: audio player left, transcript+flags right |
| 5 | Click Flags tab — see R3 Critical flag | Quote: "agar kal tak paisa nahi aaya to main police bhej dunga" |
| 6 | Click "▶ Play" on the R3 flag | Audio jumps to 0:41, plays the threat in the original voice |
| 7 | Click "Confirm" — type a comment | Button changes to "Confirmed by [manager] at [time]" |
| 8 | Click "False alarm" on a lower-severity flag (with comment) | Score increases visibly; violation stays visible but greyed |
| 9 | Navigate to Dashboard | Breach chart, top-5 riskiest, evaluation table with WER |
| 10 | Switch to Caller view (Imran Khan identity) | Call list shows ONLY Imran's calls |
| 11 | Attempt to navigate to another caller's call URL directly | "Call not found" screen (not their data) |
| 12 | Upload 5 unseen calls | Process live; appear in Call List with violations detected |

### 13.2 README Required Sections

1. **Project overview** — 3 sentences: what it does, who it's for, the tech stack.
2. **Setup** — exact steps: clone, copy `.env.example`, fill GroqCloud API key, `make seed`, `make dev`.
3. **API key requirements** — GroqCloud (free tier, no credit card required for `openai/gpt-oss-120b` and `whisper-large-v3-turbo`; 8,000 TPM limit noted), Sarvam AI (optional manual fallback, non-expiring trial signup credits). No local models or Ollama used.
4. **Assumptions** — numbered list, each with justification (see Appendix A).
5. **Per-rule accuracy numbers** — table with precision/recall/F1/support, plus: "computed on 20 hand-labelled calls; support below 3 flagged as low sample size."
6. **WER** — value + "based on 3 calls; not a general-purpose benchmark."
7. **Known limitations** — scoring formula is a design decision; single review per violation; no authentication; Groq Free Tier 8,000 TPM limit requires sequential rate pacing (~20s delay between calls) during batch processing.
8. **Ethical statement** — "All audio is synthetic role-play recorded by the team. No real customer data was used or recorded."
9. **Role system disclaimer** — "Role selection is a demo mechanism, not authentication. A production version requires real login (listed as a bonus feature in the assignment)."

### 13.3 Judging Criteria — Pass/Fail Gates

| Criterion | Pass condition |
|---|---|
| Demo story works without errors | All 12 steps in §13.1 complete without JS errors or API failures |
| Each role view shows only what it should | Network tab confirms backend filtered, not just UI |
| Critical breaches caught without flooding | R3 and R4 detected in call_02; call_01 clean (0 violations) |
| Accuracy numbers reported honestly | WER includes "based on 3 calls"; ⚠ for low support; undefined shown as "—" |
| Flags point to the exact moment | Audio seeks to within ±2 seconds of violation timestamp |
| Code clean enough to explain | Each module's purpose explainable in 1–2 sentences |

---

## Appendix A — Key Assumptions

| # | Assumption | Justification |
|---|---|---|
| A1 | Severity always assigned from `rules.py`, never from LLM | Assignment specifies exact fixed severities; LLM deviation would contradict a spec requirement |
| A2 | R1 boundary: [08:00, 19:00) — 19:00:00 exactly is a breach | "Between 8 AM and 7 PM" is ambiguous at the exact 7 PM minute; documented in code comment |
| A3 | Pending violations count at full weight toward score | Unreviewed Critical flags must surface at the top of the call list to direct manager attention |
| A4 | Confirming a flag does NOT change the score | Confirmation is for audit trail; the deduction was already applied when the violation was created |
| A5 | One Review row per violation (UNIQUE constraint; updates in-place) | Review history is out of scope for 2–3 day budget; noted as README limitation |
| A6 | R1 violation has `segment_id = NULL` | R1 is metadata-only; there is no transcript segment it belongs to |
| A7 | Comment required for False alarm, optional for Confirm | Overriding an AI-detected Critical/High flag requires justification for audit trail |
| A8 | Ground truth: binary presence/absence per rule per call | This is what a hand-labeller can realistically and consistently judge for 20 calls |
| A9 | Pre-process 20 real calls at seed time | Demo must be fast and independent of live API calls; ensures dashboard shows real results |
| A10 | `openai/gpt-oss-120b` used as sole primary LLM; batch calls are rate-paced | Free tier (8,000 TPM, 30 RPM, 1,000 RPD) provides $0 cost access; batch processing applies ~20s delay between calls to respect 8k TPM limit. No silent fallback model is permitted. |
| A11 | WER averaged over 3 calls and labelled with that caveat | 3 calls is the assignment's stated minimum; result not implied to be a general benchmark |
| A12 | SPA (React/Vite) instead of Streamlit | Streamlit's rerun model prevents the audio-seek-on-flag-click interaction (RQ-05) |
| A13 | No object storage; audio files on local disk | 20 files trivial for local disk; "swap for S3 in production" noted in README |
| A14 | Hosted-only STT architecture (no faster-whisper or local models) | Assignment explicitly specifies hosted transcription API. Groq `whisper-large-v3-turbo` is sole primary hosted STT; Sarvam AI is documented as manual hosted fallback. No local models or Ollama are downloaded or run. |
| A15 | Precision/recall includes ALL violation states (pending + confirmed + false_alarm) | "System flagged" and "manager confirmed" are independent dimensions; conflating them would misrepresent the AI's actual performance |
| A16 | Dashboard scoped caller view shows self-aggregate (not hidden entirely) | PDF says "a caller sees only their own calls, flags and scores" — implies dashboard obeys same scoping, not that it's hidden |

---

*End of MASTER_IMPLEMENTATION_PLAN.md*
*All 16 sections and appendix complete. No placeholder headings.*