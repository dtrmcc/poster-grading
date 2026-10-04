# Poster Grading — Design

Status: v0.5 (stage 3 in progress), 2026-10-04. Owner: Chen Qinghua (NIE/NTU).

## 1. Purpose

A local, single-user tool that makes grading student work faster and more consistent
without taking the judgement away from the instructor. First use: ALS10A Assignment 1
(academic poster + in-class presentation), groups TG03 and TG06. Built so that new
assignments are added by writing a config file, not by changing code.

## 2. Principles

1. **The instructor decides.** AI only suggests. Every mark and every sentence of
   student feedback is confirmed or edited by the instructor before it is final.
   AI suggestions and final decisions are both stored, so they can be compared later.
2. **Identifiable student data never leaves the Mac.** Only de-identified content is
   sent to external AI (Together AI). This is the compliance boundary under NTU/NIE
   policy and is enforced in code, not left to habit (see §4).
3. **Assignments are configuration.** Rubric, weights, mark allocation, grade bands and
   feedback structure live in `config/assignments/*.yaml`.
4. **Local first, portable.** Runs on the instructor's Mac via Docker Compose. The same
   code can be deployed to a VPS later without changes.

## 3. Architecture

```
 Browser (wide screen, 127.0.0.1 only)
   │
   ▼
 frontend  (React + Vite)          backend (FastAPI, Python)
   └── /api ─────────────────────▶  ├─ rubric + scoring        (config/assignments/*.yaml)
                                    ├─ importer                (poster PDFs, presentation notes)
                                    ├─ de-identification gate  ──▶ local model (Ollama on Mac)
                                    ├─ AI gateway  ─────────────▶ Together AI   (de-identified only)
                                    ├─ reference checker  ──────▶ Crossref / OpenAlex (citation metadata only)
                                    ├─ feedback + PDF export   (WeasyPrint or headless Chrome)
                                    └─ storage: SQLite + files in ./data   ──▶ B2 encrypted backup (stage 4)
```

All ports bind to `127.0.0.1`. `./data` is git-ignored and never leaves the machine
except as an encrypted backup.

## 4. The de-identification boundary

Every external AI call goes through **one** module (`ai_gateway`). It refuses to send
anything that has not passed the de-identification gate.

1. **Roster-based replacement.** The roster (full name, English/Chinese names, given
   names, matriculation number) is built at import time from submission filenames.
   Every occurrence is replaced with a stable pseudonym (`S01`, `S02`, …). Matric
   numbers are also caught by pattern (`U\d{7}[A-Z]`).
2. **Local model sweep.** A local model scans the replaced text for remaining
   identifiers (other names, schools, personal details) and flags them for review.
3. **Images.** Poster pages are rendered locally (long side 1600 px). Every place a
   student's name (any variant, any classmate), matric number, e-mail or phone number
   occurs in the PDF text layer is painted solid black on the raster. The instructor sees
   the boxes in the review window and can draw more (photos, logos, image-only text);
   automatic boxes cannot be removed. Image-only PDFs have no text layer: the image is
   unticked by default until boxes are drawn. Boxes are stored per student
   (`redaction` table, normalised coordinates) and recomputed when the poster changes.
4. **Send confirmation.** With `REQUIRE_SEND_CONFIRMATION=true` the exact outbound
   payload is shown and must be approved. Can be relaxed to "only when the local sweep
   flags something".
   Implemented as a two-step API: *preview* builds and stores the exact payload
   (`ai_request` row, SHA-256 of the messages); *send* transmits that stored payload
   unchanged and refuses if it was altered. The preview also lists any name words that
   survive de-identification in any letter case.
5. **Send log.** Each external call is logged locally: timestamp, task, provider, model,
   payload hash and the payload itself.
6. **Re-identification happens locally.** Responses refer to `S07`; names are restored
   only on the Mac.

The pseudonym map is stored only in the local database.

## 5. Inputs

### 5.1 Poster submissions
Imported through the browser ("Import posters": pick a group, drop the PDFs). Each PDF is
copied into `./data`, rendered to page images (long side 3200 px) and its text extracted.
Re-importing an identical file is a no-op; a changed file for the same student replaces the
poster and keeps the assessment. Pseudonyms are assigned once and never change.

PDFs exported from the LMS, one per student, filename pattern:

```
NIE <FULL NAME IN CAPS>[ (<OTHER NAME>)]_<student's own filename>_Submitted file_trn-oid---<id>.pdf
```

The importer parses the name from the prefix and the submission id from the suffix.
The student's own filename part sometimes contains a matric number; it is treated as
identifying data.

### 5.2 Presentation notes
One Word file per group (e.g. `TG03 Presentation Note.docx`), written live during the
presentations:

- Each **top-level list item (level 0) starts a new student**, in presentation order.
  Sub-items (levels 1–2) belong to that student. A few blocks start with plain
  paragraphs instead of a list item; the splitter must tolerate this.
- Students are **not named** in the file. Some blocks include a talk duration
  (e.g. `0327`, `3:49`), and absences are noted (e.g. "Medical leave").
- Notes mix delivery observations with remarks on the poster/content.

Import flow ("Presentation notes" button): upload the group's Word file → it is split
into ordered blocks → each block gets a *suggested* student, computed locally from words
shared with the student's poster text (IDF-weighted overlap, one-to-one greedy assignment;
no AI, nothing leaves the Mac) → the instructor confirms or changes each match, can tick
blocks to ignore (absences are pre-ticked), and applies. A student may receive more than
one block (joined). Applying never overwrites a note the instructor has edited unless
"overwrite" is ticked. The matching is saved and can be reopened; re-uploading replaces it.

## 6. Workflow (per student)

1. Open student → poster on the left, presentation note + own comments in the middle,
   scoring on the right.
2. Write short comments (poster).
3. **Suggest** → AI proposes marks per dimension, each with a one-line rationale and
   the evidence it relied on (de-identified inputs only).
4. Instructor accepts/edits each mark → total, percentage and letter grade update live.
   The final letter can be overridden with a written reason.
5. **Draft feedback** → AI writes two sections: *poster* (from comments + poster) and
   *presentation* (from the note). Rubric and per-dimension grades are attached.
6. Instructor edits and approves → PDF generated, archived in `./data/exports/`,
   downloadable. Batch export as a zip per group.

Status per student: `not started → suggested → marks confirmed → feedback approved → exported`.

## 7. Scoring (current assignment)

Defined in `config/assignments/als10a-2026-poster.yaml`.

| Dimension | Weight | Max marks | Evidence | Feedback section |
|---|---|---|---|---|
| Presentation delivery | 20% | 6 | presentation note | presentation |
| Understanding of the topic (from the poster: coherence, consistency, no contradictions) | 15% | 4.5 | poster, comments | poster |
| Integration of literature | 20% | 6 | poster, reference check, comments | poster |
| Citations and examples | 15% | 4.5 | poster, reference check, comments | poster |
| Analysis (rigour of reasoning; most posters are proposals) | 15% | 4.5 | poster, comments | poster |
| Poster design | 15% | 4.5 | poster image, comments | poster |

- Marks are entered per dimension in steps of 0.25; total out of 30 is submitted.
- Letter grade from total %: rounded half-up to an integer, then 5-point bands
  (A+ ≥95, A ≥90, A- ≥85, B+ ≥80 … D ≥50, F <50). Per-dimension letters are derived
  the same way and used only to show the matching rubric descriptor.
- Students see letter grades; marks are kept for the instructor's records
  (`show_marks_to_student: false`).

## 8. AI tasks and routing

Each task can be routed to `local`, `together` or `off` in `.env`.

| Task | Default | Notes |
|---|---|---|
| De-identification sweep | local | after roster-based replacement |
| Reference extraction (to structured fields) | local | errors are caught by the Crossref lookup |
| Reference verification | no LLM for lookup | Crossref/OpenAlex/URL check; LLM compares metadata |
| Mark suggestion | together | structured JSON output: mark, rationale, evidence |
| Poster design review | together (vision model) | redacted image + de-identified comments |
| Feedback drafting | together | English, student-facing tone |

Both providers use the OpenAI-compatible chat API, so switching is a base-URL change.

## 9. Data model (SQLite)

- `assignment` — id, config path, config hash
- `student` — id, assignment, group, full name, other names, matric no., pseudonym
- `submission` — student, file path, sha256, extracted text, page images
- `presentation_note` — student, block index, raw text, edited text
- `assessment` — student, presentation note, instructor comment, final marks (JSON
  `{dimension: "4.25"}`), override letter + reason. Status is derived
  (`not_started` / `in_progress` / `marked`).
- AI suggestions (per dimension: mark, rationale, evidence) will be stored alongside the final
  marks when mark suggestion is added, so AI-vs-final can be compared.
- `reference` — submission, raw string, parsed fields, verdict, matched record
- `feedback` — student, poster text, presentation text (instructor-edited), last AI draft of
  each, ai_request, updated_at
- `export` — student, version, pdf path (under DATA_DIR), sha256, letter, total, created_at.
  A student counts as *exported* when the latest export is newer than any later edit.
- `ai_request` (send log) — student, task, provider, model, payload (de-identified messages),
  payload_sha256, replacements, status (previewed/sent/failed), raw response, validated
  result, token usage, error, created_at, sent_at. The latest sent `suggest_marks`
  result is shown next to each dimension; AI never writes final marks itself.

## 10. Repository layout

```
backend/            FastAPI app (app/), tests (tests/), Dockerfile
frontend/           React + Vite app, Dockerfile
config/assignments/ one YAML per assignment
docs/               this document
data/               runtime data — git-ignored, never committed
docker-compose.yml  local deployment (127.0.0.1 only)
.env.example        settings template; copy to .env
```

## 11. Roadmap

- **Stage 0** — design, rubric config ✅
- **Stage 1** — repository skeleton, scoring engine with tests, three-column UI shell ✅
- **Stage 2** — MVP
  - 2.1 import posters (roster from filenames, pseudonyms, page rendering, text) ✅
  - 2.2 grading screen: poster viewer, student navigation, autosave ✅
  - 2.3 presentation-note import: split into blocks, local match suggestions, apply ✅
  - 2.4 AI mark suggestion via the de-identification gateway ✅
    (text only: poster design is suggested only from the instructor's comments until
    redacted poster images can be sent — stage 3)
  - 2.5 feedback drafting and editing; PDF export and archive ✅
    (Feedback tab: AI draft of two sections through the same preview/send gateway, never
    overwriting edited text; PDF built locally with PyMuPDF — grades by criterion with the
    matching descriptor, both feedback sections, signature, full rubric on its own page with
    the student's bands shaded; every export kept as a new version in
    `data/exports/<assignment>/<group>/`; zip of latest PDFs and marks CSV per group)
- **Stage 3**
  - 3.1 poster-design review from redacted poster images (vision model) ✅ — sent together
    with the text suggestion after one review; the image-based mark replaces the text-based
    one for criteria whose evidence includes `poster_image`
  - 3.2 reference checking
  - 3.3 local-model de-identification sweep
- **Stage 4** — run on the Mac as the daily tool; encrypted B2 backup/restore scripts
- Later — other assignment types (essays, reports), multi-marker support, VPS deployment

## 12. Open items

- Choose Together text and vision models (check current model list and pricing).
- Choose local model(s) for Ollama on the M4 / 32 GB MacBook Air.
- PDF engine: WeasyPrint (light) vs headless Chrome (higher fidelity) — decide in stage 2.
