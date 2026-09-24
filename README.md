# Site check-out logger

A small Flask app that looks up a participant by check-in code, confirms
their name and bib number, then captures a status, notes, and a
high-accuracy GPS fix from the browser and saves it to Airtable. The
Airtable token lives only on the server — it is never sent to the browser.

## Run it locally

```bash
cd airtable-checkin
python3 -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

cp .env.example .env
# then edit .env and paste in your real AIRTABLE_TOKEN

python app.py
```

Open http://localhost:5000 in your browser.

**Important:** Geolocation requires HTTPS in most browsers (localhost is
exempt for testing). Deploy behind HTTPS before using this on other devices.

## Routes

| Route | Purpose |
|---|---|
| `/` | Check-out, entering by code |
| `/bib` | Check-out, entering by bib number |
| `/checkin` | Check-in, entering by code |
| `/checkin/bib` | Check-in, entering by bib number |
| `/map` | Live map of each pilot's most recent Check-Out location |

The map's popups also show phone number, sourced from a `Phone (from
Participant)` lookup field on `Check-Outs` (configurable via
`PHONE_FIELD_NAME`). Use the From/To date fields on the map to restrict
which Check-Out rows are considered when picking each pilot's "latest"
location — useful for reviewing a specific day rather than always seeing
the most recent update. The map auto-refreshes every 30 seconds and keeps
your current pan/zoom position during background updates (it only
re-centers on the initial load, a manual "Refresh" click, or when you
change the date filter).

All four also accept `?code=XXXX` or `?bib=XXXX` in the URL to skip manual
entry and look up immediately (e.g. for QR codes on wristbands).

**Check-out** requires the participant's `Live Status` to equal
`ACTIVE / AIRBORNE` (configurable via `ALLOWED_LIVE_STATUS`), captures a
Status/Notes choice, free-text Notes, and a GPS fix, and saves to
`Check-Outs`.

**Check-in** requires the opposite — `Live Status` must **not** be
`ACTIVE / AIRBORNE` — and only saves the participant link to `Check-Ins`.
No location, status, or notes are collected for check-in.

## Your token needs these scopes

At https://airtable.com/create/tokens, on the token used in `.env`:

- `data.records:read`
- `data.records:write`
- `schema.bases:read` (for the live Status dropdown; without it the app
  silently falls back to the hardcoded list)
- Access: this one base only

## Deploying so your team can use it on their phones

Any host that runs Python works. A few easy options:

- **Render** (render.com) — connect the repo, set the run command to
  `gunicorn app:app`, add the `.env` values as environment variables in the
  dashboard.
- **Railway** (railway.app) — similar: connect repo, set env vars, it
  auto-detects the Procfile-style start command.
- **PythonAnywhere** — good for a simple always-on free-tier deployment.

Whichever you use, set `AIRTABLE_TOKEN` (and the other variables from
`.env.example`) as environment variables in that platform's dashboard —
never commit `.env` to a public repo.

## Deploying to Google Cloud Run

Cloud Run gives you HTTPS automatically (required for geolocation on real
devices) and scales to zero when nobody's using it.

**1. One-time setup:**

```bash
gcloud auth login
gcloud config set project YOUR_PROJECT_ID
gcloud services enable run.googleapis.com secretmanager.googleapis.com
```

**2. Store your token in Secret Manager** (keeps it out of plain env vars and
deploy logs):

```bash
echo -n "YOUR_REAL_AIRTABLE_TOKEN" | gcloud secrets create airtable-token --data-file=-
```

**3. Deploy** from inside the `airtable-checkin` folder (Cloud Run builds the
`Dockerfile` automatically — no separate build step needed):

```bash
cd airtable-checkin

gcloud run deploy airtable-checkin \
  --source . \
  --region us-central1 \
  --allow-unauthenticated \
  --set-secrets AIRTABLE_TOKEN=airtable-token:latest \
  --set-env-vars AIRTABLE_BASE_ID=appOEp20VC1b60wv4,CHECKOUTS_TABLE=Check-Outs,PARTICIPANTS_TABLE=Participants,PARTICIPANT_MATCH_FIELD="Bib Number",STATUS_FIELD_NAME=Status
```

The command prints a `*.run.app` HTTPS URL when it finishes — that's what
you share with people checking in.

**Updating later:** re-run the same `gcloud run deploy` command from step 3
any time you change the code; Cloud Run rebuilds and redeploys automatically.

**On `--allow-unauthenticated`:** this makes the URL reachable by anyone who
has it, with no Google sign-in required — necessary here since your team
won't have Google accounts tied to this project. It does mean the
`/api/checkout` endpoint is open to anyone with the link, not just people
using the form as intended. For a public race-day tool that's usually an
acceptable tradeoff, but if you want a lightweight guard, a simple shared
access code checked in `app.py` before writing to Airtable is a small
addition — ask if you'd like that added.

## Files

- `app.py` — Flask server; all Airtable calls happen here
- `templates/index.html` — the page the browser loads; talks only to this
  server's own `/api/status-options` and `/api/checkout` endpoints
- `.env.example` — copy to `.env` and fill in your real token (gitignored)
- `requirements.txt` — Python dependencies
- `Dockerfile` / `.dockerignore` — used by Cloud Run (or any container host)
  to build and run the app

