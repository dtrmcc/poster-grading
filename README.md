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
2. Move between students with **← / →** (when not typing) or the student menu.
3. Notes, comments and marks save automatically ("Saved" in the header).

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
