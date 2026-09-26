# Cloudbase Ops

A ground-crew web app for tracking paraglider pilots through a flying day:
who's currently airborne, where they landed, and how to reach them — all
backed by Airtable, with no app install required (it's just a web page).

## What it does

Each pilot carries one QR code (or short code / bib number) that works for
their entire day. Scanning or entering it:

1. **Looks up the pilot** in an Airtable `Participants` table by code or
   bib number.
2. **Checks their current status** (`Live Status`, e.g. `ACTIVE /
   AIRBORNE`) to decide automatically whether this should be a
   **check-in** (about to fly) or a **check-out** (just landed) — so the
   same QR code works both ways, no need for separate "in" and "out"
   codes.
3. **Check-out** captures a status (Needs Transport / Injury / Checked
   Out — pulled live from Airtable's own dropdown), free-text notes, and
   a high-accuracy GPS fix from the pilot's phone, then saves it to a
   `Check-Outs` table.
4. **Check-in** just confirms identity and logs the timestamp to a
   `Check-Ins` table — no location or status needed, since the pilot
   hasn't flown yet.

On top of that flow, ground crew get:

- **A live map** (`/map`) plotting every pilot's most recent landing
  location, color-coded by status, with phone numbers one tap away and a
  date filter for reviewing a specific day.
- **A pilot roster** (`/pilots`) — a printable grid of every participant's
  bib number, name, and personal QR code, for producing physical
  badges/cards before a flying day starts.
- **A smart entry link** (`/go`) suitable for printing on a QR code or
  wristband, which routes to the right form automatically based on the
  pilot's live status.

A companion **Telegram bot** (not included in this folder, but built to
work alongside it) sends each pilot their personal QR code/link on
request, and separately posts weather + cloudbase estimates for the flying
site.

## How it's built

- **Backend:** Flask (`app.py`). Holds the Airtable token server-side and
  exposes a small JSON API (`/api/lookup-participant`, `/api/checkin`,
  `/api/checkout`, `/api/checkout-locations`, `/api/status-options`) that
  the pages call. The token never reaches the browser.
- **Frontend:** plain HTML/CSS/vanilla JS templates — no build step, no
  framework. The map uses Leaflet + OpenStreetMap tiles (no API key
  needed).
- **Data store:** Airtable. Three tables: `Participants` (the roster),
  `Check-Outs` (landing records with location/status/notes), and
  `Check-Ins` (simple timestamped confirmations).
- **Hosting:** any platform that runs Python — instructions below cover
  Google Cloud Run, Render, Railway, and PythonAnywhere. Geolocation
  requires HTTPS on real devices, which all of these provide.

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
| `/go?code=X` or `/go?bib=X` | Smart entry: looks up the participant's `Live Status` and forwards to the check-out or check-in form automatically |
| `/map` | Live map of each pilot's most recent Check-Out location |
| `/pilots` | Grid of every participant with Bib Number, Full Name, and their QR code |

All four form routes also accept `?code=XXXX` or `?bib=XXXX` in the URL to
skip manual entry and look up immediately (e.g. for QR codes on
wristbands).

**Check-out** requires the participant's `Live Status` to equal
`ACTIVE / AIRBORNE` (configurable via `ALLOWED_LIVE_STATUS`), captures a
Status/Notes choice, free-text Notes, and a GPS fix, and saves to
`Check-Outs`.

**Check-in** requires the opposite — `Live Status` must **not** be
`ACTIVE / AIRBORNE` — and only saves the participant link to `Check-Ins`.
No location, status, or notes are collected for check-in.

**`/go`** is the route to put on a QR code or wristband: it checks the
participant's current `Live Status` once, then redirects to whichever form
(check-out or check-in) actually applies — so one link works both ways
depending on where the pilot is in their flight. If the participant can't
be found or Airtable is unreachable, it falls back to the check-out form,
which will then show its own "not found" error as usual.

**`/map`** shows phone number in each popup, sourced from a `Phone (from
Participant)` lookup field on `Check-Outs` (configurable via
`PHONE_FIELD_NAME`). Use the From/To date fields to restrict which
Check-Out rows are considered when picking each pilot's "latest" location —
useful for reviewing a specific day rather than always seeing the most
recent update. The map auto-refreshes every 30 seconds and keeps your
current pan/zoom position during background updates (it only re-centers on
the initial load, a manual "Refresh" click, or when you change the date
filter).

**`/pilots`** displays each participant's QR code image exactly as stored
in Airtable's `QR_Code_Check-In_Out_Image` field (a plain URL or an
attachment — either format is handled), and links it through to their
`Custom_CheckInOut_URL`. Sorted numerically by bib number. Includes a
Print button (uses browser print styles) for producing physical
cards/badges.

## Airtable setup

This app expects three tables in one base:

- **`Participants`** — one row per pilot. Needs at minimum: a bib number
  field, a full name field, a `Code` field (a short check-in code, e.g.
  for a QR code), and a `Live Status` single-select (or text) field whose
  value determines check-in vs. check-out eligibility. Optionally: a
  `Custom_CheckInOut_URL` field holding that pilot's personal `/go` link,
  and a `QR_Code_Check-In_Out_Image` field holding a QR code image (URL or
  attachment) for that link.
- **`Check-Outs`** — one row per landing. Needs `Participant` (linked to
  Participants), `Latitude`, `Longitude`, a `Status` single-select
  (its choices populate the check-out form's dropdown live), `Notes`
  (text), and a `Timestamp` field.
- **`Check-Ins`** — one row per check-in. Needs only `Participant` (linked
  to Participants).

All of these table and field names are configurable — see
`.env.example` — so they don't have to match exactly as long as you point
the corresponding environment variable at your real field name.

## Environment variables

| Variable | Default | Purpose |
|---|---|---|
| `AIRTABLE_TOKEN` | *(required)* | Personal access token; see scopes below |
| `AIRTABLE_BASE_ID` | `appOEp20VC1b60wv4` | Your Airtable base ID |
| `PARTICIPANTS_TABLE` | `Participants` | Roster table name |
| `CHECKOUTS_TABLE` | `Check-Outs` | Landing-records table name |
| `CHECKINS_TABLE` | `Check-Ins` | Check-in-records table name |
| `PARTICIPANT_MATCH_FIELD` | `Bib Number` | Bib number field on Participants |
| `CODE_FIELD_NAME` | `Code` | Short check-in code field on Participants |
| `FULL_NAME_FIELD_NAME` | `Full Name` | Name field on Participants |
| `LIVE_STATUS_FIELD_NAME` | `Live Status` | Field on Participants gating check-in/out |
| `ALLOWED_LIVE_STATUS` | `ACTIVE / AIRBORNE` | The value that means "currently flying" |
| `STATUS_FIELD_NAME` | `Status` | Status/Notes dropdown field on Check-Outs |
| `CHECKOUT_TIMESTAMP_FIELD` | `Timestamp` | Timestamp field on Check-Outs (used by the map) |
| `PHONE_FIELD_NAME` | `Phone (from Participant)` | Phone lookup field on Check-Outs (used by the map) |
| `CHECKINOUT_URL_FIELD_NAME` | `Custom_CheckInOut_URL` | Personal `/go` link field on Participants |
| `QR_IMAGE_FIELD_NAME` | `QR_Code_Check-In_Out_Image` | QR image field on Participants (used by `/pilots`) |

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
using the form as intended. For a public event this is usually an
acceptable tradeoff, but if you want a lightweight guard, a simple shared
access code checked in `app.py` before writing to Airtable is a small
addition — ask if you'd like that added.

## Files

- `app.py` — Flask server; all Airtable calls happen here
- `templates/index.html` — the check-in/check-out form (shared by all four
  form routes; talks only to this server's own API endpoints)
- `templates/success.html` — confirmation page after a save, with a map
  pin for check-outs
- `templates/map.html` — the live pilot-location map
- `templates/pilots.html` — the printable pilot/QR roster
- `.env.example` — copy to `.env` and fill in your real token (gitignored)
- `requirements.txt` — Python dependencies
- `Dockerfile` / `.dockerignore` — used by Cloud Run (or any container host)
  to build and run the app

