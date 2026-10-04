# Poster Grading

A local, AI-assisted grading and feedback tool. The instructor grades; AI suggests marks and
drafts feedback from **de-identified** content. Student data stays on this machine.

See [docs/DESIGN.md](docs/DESIGN.md) for the full design.

## Quick start (Mac, Docker)

Requirements: Docker (OrbStack or Docker Desktop).

```bash
cp .env.example .env        # then replace every __REPLACE_ME__ value
docker compose up --build
```

Open <http://127.0.0.1:5173>. The backend API is at <http://127.0.0.1:8000/api/health>.

## Grading workflow (current)

1. **Import posters** (top right): choose the group, drop that group's PDF submissions.
   Non-submission files such as `Rubric.pdf` are skipped.
2. **Presentation notes**: upload the group's note (Word), check the suggested student for
   each block, then *Apply to students*.
3. Move between students with **← / →** (when not typing) or the student menu.
4. Notes, comments and marks save automatically ("Saved" in the header).
5. **✦ AI suggest marks** (needs `TOGETHER_API_KEY` and `TOGETHER_MODEL_TEXT` in `.env`;
   `TOGETHER_MODEL_VISION` adds the poster image for the design criterion): review the
   de-identified text and the redacted poster image (drag to black out more), send, then
   *Use* individual AI marks or *Use all AI marks*. Your own marks are always the ones that count.
6. **Feedback** tab (after all marks are entered): *✦ Draft with AI*, edit both sections, then
   *Approve & export PDF*. Every export is archived as a new version in
   `data/exports/<assignment>/<group>/`.
7. **Export** menu: marks CSV and a zip of the latest feedback PDFs (current group filter).

## Run tests

```bash
docker compose run --rm backend pytest -q
```

## Add a new assignment

Copy `config/assignments/als10a-2026-poster.yaml`, change the id, dimensions, weights and
descriptors, and restart the backend. The config is validated on startup (weights must sum to
100, max marks must match the weights, etc.).

## Data and privacy

- `./data` holds the database, submissions and exported feedback. It is git-ignored.
- `.env` holds API keys. It is git-ignored; only `.env.example` is committed.
- Never commit student files. `*.pdf`, `*.docx` and database files are ignored by default.
- External AI calls go through a single gateway that only sends de-identified content.
