import os
import logging
from collections import defaultdict
from datetime import datetime, timezone

import requests
from flask import Flask, jsonify, request, render_template, redirect, url_for
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# --- Configuration (server-side only; never sent to the browser) -----------
AIRTABLE_TOKEN = os.environ.get("AIRTABLE_TOKEN")
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appOEp20VC1b60wv4")
CHECKOUTS_TABLE = os.environ.get("CHECKOUTS_TABLE", "Check-Outs")
CHECKINS_TABLE = os.environ.get("CHECKINS_TABLE", "Check-Ins")
PARTICIPANTS_TABLE = os.environ.get("PARTICIPANTS_TABLE", "Participants")
PARTICIPANT_MATCH_FIELD = os.environ.get("PARTICIPANT_MATCH_FIELD", "Bib Number")
CODE_FIELD_NAME = os.environ.get("CODE_FIELD_NAME", "Code")
FULL_NAME_FIELD_NAME = os.environ.get("FULL_NAME_FIELD_NAME", "Full Name")
STATUS_FIELD_NAME = os.environ.get("STATUS_FIELD_NAME", "Status")
LIVE_STATUS_FIELD_NAME = os.environ.get("LIVE_STATUS_FIELD_NAME", "Live Status")
ALLOWED_LIVE_STATUS = os.environ.get("ALLOWED_LIVE_STATUS", "ACTIVE / AIRBORNE")
CHECKOUT_TIMESTAMP_FIELD = os.environ.get("CHECKOUT_TIMESTAMP_FIELD", "Timestamp")
PHONE_FIELD_NAME = os.environ.get("PHONE_FIELD_NAME", "Phone (from Participant)")
CHECKINOUT_URL_FIELD_NAME = os.environ.get("CHECKINOUT_URL_FIELD_NAME", "Custom_CheckInOut_URL")
QR_IMAGE_FIELD_NAME = os.environ.get("QR_IMAGE_FIELD_NAME", "QR_Code_Check-In_Out_Image")
FALLBACK_STATUS_CHOICES = ["Needs Transport", "Injury", "Checked Out"]

API_BASE = "https://api.airtable.com/v0"


def is_live_status_allowed(value):
    """Compares case-insensitively and ignores whitespace differences
    (e.g. "ACTIVE/AIRBORNE" and "active / airborne" both match)."""
    normalize = lambda s: (s or "").strip().upper().replace(" ", "")
    return normalize(value) == normalize(ALLOWED_LIVE_STATUS)


def airtable_headers():
    return {
        "Authorization": f"Bearer {AIRTABLE_TOKEN}",
        "Content-Type": "application/json",
    }


def escape_formula_value(value):
    return value.replace("\\", "\\\\").replace('"', '\\"')


@app.route("/")
def index():
    return render_template("index.html", entry_mode="code", flow="checkout")


@app.route("/bib")
def bib_entry():
    return render_template("index.html", entry_mode="bib", flow="checkout")


@app.route("/checkin")
def checkin_index():
    return render_template("index.html", entry_mode="code", flow="checkin")


@app.route("/checkin/bib")
def checkin_bib_entry():
    return render_template("index.html", entry_mode="bib", flow="checkin")


@app.route("/go")
def smart_entry():
    """Single entry point for a QR code / shared link: looks up the
    participant's current Live Status and forwards to the check-out form
    if they're ACTIVE / AIRBORNE, or the check-in form otherwise.

    Accepts ?code=XXXX or ?bib=XXXX (mirroring the other entry points).
    If the participant can't be found (or Airtable can't be reached), it
    defaults to the check-out form — the existing lookup there will show
    the appropriate "not found" error rather than this route failing.
    """
    code = (request.args.get("code") or "").strip()
    bib = (request.args.get("bib") or "").strip()

    if not code and not bib:
        return redirect(url_for("index"))

    result = None
    try:
        if code:
            result = find_participant_by_field(CODE_FIELD_NAME, code)
        else:
            result = find_participant_by_field(PARTICIPANT_MATCH_FIELD, bib)
    except Exception:  # noqa: BLE001 - routing best-effort; let the target page handle errors
        app.logger.exception("Smart entry lookup failed; defaulting to check-out")

    flow = "checkin" if (result and not is_live_status_allowed(result.get("liveStatus"))) else "checkout"

    if bib:
        target = "checkin_bib_entry" if flow == "checkin" else "bib_entry"
        return redirect(url_for(target, bib=bib))
    else:
        target = "checkin_index" if flow == "checkin" else "index"
        return redirect(url_for(target, code=code))


@app.route("/success")
def success():
    def to_float(value):
        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    return render_template(
        "success.html",
        flow=request.args.get("flow", "checkout"),
        name=request.args.get("name", ""),
        bib=request.args.get("bib", ""),
        status=request.args.get("status", ""),
        notes=request.args.get("notes", ""),
        accuracy=request.args.get("acc", ""),
        lat=to_float(request.args.get("lat")),
        lon=to_float(request.args.get("lon")),
    )


@app.route("/map")
def map_view():
    return render_template("map.html")


def _bib_sort_key(pilot):
    bib = pilot.get("bib")
    try:
        return (0, int(bib))
    except (TypeError, ValueError):
        return (1, str(bib or ""))


@app.route("/pilots")
def pilots_view():
    """Grid of every participant's Bib Number, Full Name, and their
    pre-made Check-In/Check-Out QR code image (a ready-to-use image URL
    already stored in Airtable, not generated here)."""
    try:
        records = fetch_all_records(
            PARTICIPANTS_TABLE,
            fields=[PARTICIPANT_MATCH_FIELD, FULL_NAME_FIELD_NAME, QR_IMAGE_FIELD_NAME, CHECKINOUT_URL_FIELD_NAME],
        )
    except requests.HTTPError as exc:
        detail = str(exc)
        if exc.response is not None:
            try:
                detail = exc.response.json().get("error", {}).get("message", detail)
            except ValueError:
                pass
        app.logger.error("Airtable request failed: %s", detail)
        return render_template("pilots.html", pilots=[], error=detail)
    except Exception as exc:  # noqa: BLE001
        app.logger.exception("Unexpected error loading pilots")
        return render_template("pilots.html", pilots=[], error=str(exc))

    pilots = []
    for rec in records:
        f = rec.get("fields", {})
        pilots.append({
            "bib": f.get(PARTICIPANT_MATCH_FIELD),
            "name": f.get(FULL_NAME_FIELD_NAME),
            "qr": _first_or_value(f.get(QR_IMAGE_FIELD_NAME)),
            "url": _first_or_value(f.get(CHECKINOUT_URL_FIELD_NAME)),
        })

    pilots.sort(key=_bib_sort_key)

    return render_template("pilots.html", pilots=pilots, error=None)


@app.route("/api/status-options")
def status_options():
    """Return the live choices for the Status single-select field, or a fallback list."""
    try:
        url = f"{API_BASE}/meta/bases/{AIRTABLE_BASE_ID}/tables"
        resp = requests.get(url, headers=airtable_headers(), timeout=10)
        resp.raise_for_status()
        data = resp.json()

        table = next((t for t in data.get("tables", []) if t["name"] == CHECKOUTS_TABLE), None)
        if not table:
            raise ValueError(f'Table "{CHECKOUTS_TABLE}" not found in schema')

        field = next((f for f in table.get("fields", []) if f["name"] == STATUS_FIELD_NAME), None)
        if not field or "choices" not in field.get("options", {}):
            raise ValueError(f'"{STATUS_FIELD_NAME}" is not a single select field with choices')

        choices = [c["name"] for c in field["options"]["choices"]]
        return jsonify({"choices": choices})

    except Exception as exc:  # noqa: BLE001 - deliberately broad; this endpoint must never hard-fail
        app.logger.warning("Falling back to default status options: %s", exc)
        return jsonify({"choices": FALLBACK_STATUS_CHOICES, "fallback": True})


def fetch_all_records(table_name, fields=None):
    """Fetches every record from a table, following Airtable's pagination
    (100 records per page) until there's no offset left."""
    records = []
    url = f"{API_BASE}/{AIRTABLE_BASE_ID}/{table_name}"
    base_params = {}
    if fields:
        base_params["fields[]"] = fields

    offset = None
    while True:
        params = dict(base_params)
        if offset:
            params["offset"] = offset
        resp = requests.get(url, headers=airtable_headers(), params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
    return records


_MIN_DATETIME = datetime.min.replace(tzinfo=timezone.utc)


def parse_timestamp(value):
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed
    except ValueError:
        return None


def _timestamp_sort_key(value):
    # Sorts by parsed datetime when possible; falls back to the raw string
    # (still a reasonable ordering for same-format timestamps) otherwise.
    return (parse_timestamp(value) or _MIN_DATETIME, value or "")


def parse_date_boundary(value, end_of_day=False):
    """Parses a plain 'YYYY-MM-DD' (or full ISO) query param into a UTC
    datetime boundary. end_of_day=True rounds up to 23:59:59.999999."""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    if end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _first_or_value(value):
    """Airtable lookup fields (like "Phone (from Participant)") come back
    as a list even when there's only one linked record; unwrap it."""
    if isinstance(value, list):
        return value[0] if value else None
    return value


@app.route("/api/checkout-locations")
def checkout_locations():
    """Returns the latest Check-Outs row per participant (by Timestamp),
    joined with Participants for display name/bib number. Optional ?start=
    and ?end= (YYYY-MM-DD) query params restrict which rows are considered
    before picking the latest one per participant."""
    try:
        start_dt = parse_date_boundary(request.args.get("start"))
        end_dt = parse_date_boundary(request.args.get("end"), end_of_day=True)

        checkout_records = fetch_all_records(
            CHECKOUTS_TABLE,
            fields=["Participant", "Latitude", "Longitude", STATUS_FIELD_NAME, "Notes",
                    CHECKOUT_TIMESTAMP_FIELD, PHONE_FIELD_NAME],
        )
        participant_records = fetch_all_records(
            PARTICIPANTS_TABLE,
            fields=[PARTICIPANT_MATCH_FIELD, FULL_NAME_FIELD_NAME],
        )

        participants_by_id = {}
        for rec in participant_records:
            f = rec.get("fields", {})
            participants_by_id[rec["id"]] = {
                "bibNumber": f.get(PARTICIPANT_MATCH_FIELD),
                "fullName": f.get(FULL_NAME_FIELD_NAME),
            }

        grouped = defaultdict(list)
        for rec in checkout_records:
            f = rec.get("fields", {})

            if start_dt or end_dt:
                ts = parse_timestamp(f.get(CHECKOUT_TIMESTAMP_FIELD))
                if ts is None:
                    continue  # can't tell if it's in range; exclude when filtering
                if start_dt and ts < start_dt:
                    continue
                if end_dt and ts > end_dt:
                    continue

            links = f.get("Participant") or []
            if not links:
                continue
            grouped[links[0]].append(f)

        result = []
        for participant_id, rows in grouped.items():
            latest = max(rows, key=lambda f: _timestamp_sort_key(f.get(CHECKOUT_TIMESTAMP_FIELD)))

            lat = latest.get("Latitude")
            lon = latest.get("Longitude")
            if lat is None or lon is None:
                continue

            info = participants_by_id.get(participant_id, {})
            result.append({
                "participantId": participant_id,
                "fullName": info.get("fullName"),
                "bibNumber": info.get("bibNumber"),
                "phone": _first_or_value(latest.get(PHONE_FIELD_NAME)),
                "latitude": lat,
                "longitude": lon,
                "status": latest.get(STATUS_FIELD_NAME),
                "notes": latest.get("Notes"),
                "timestamp": latest.get(CHECKOUT_TIMESTAMP_FIELD),
            })

        return jsonify({"locations": result})

    except requests.HTTPError as exc:
        detail = str(exc)
        status_code = 500
        if exc.response is not None:
            status_code = exc.response.status_code
            try:
                detail = exc.response.json().get("error", {}).get("message", detail)
            except ValueError:
                pass
        app.logger.error("Airtable request failed: %s", detail)
        return jsonify({"error": detail}), status_code

    except Exception as exc:  # noqa: BLE001
        app.logger.exception("Unexpected error building checkout locations")
        return jsonify({"error": str(exc)}), 500


def find_participant_by_field(field_name, value):
    formula = f'{{{field_name}}} = "{escape_formula_value(value)}"'
    url = f"{API_BASE}/{AIRTABLE_BASE_ID}/{PARTICIPANTS_TABLE}"

    resp = requests.get(
        url,
        headers=airtable_headers(),
        params={"filterByFormula": formula, "maxRecords": 1},
        timeout=10,
    )
    resp.raise_for_status()
    data = resp.json()

    records = data.get("records", [])
    if not records:
        return None

    record = records[0]
    fields = record.get("fields", {})
    return {
        "participantId": record["id"],
        "bibNumber": fields.get(PARTICIPANT_MATCH_FIELD),
        "fullName": fields.get(FULL_NAME_FIELD_NAME),
        "liveStatus": fields.get(LIVE_STATUS_FIELD_NAME),
    }


@app.route("/api/lookup-participant", methods=["POST"])
def lookup_participant():
    payload = request.get_json(force=True, silent=True) or {}
    code = (payload.get("code") or "").strip()
    bib = (payload.get("bib") or "").strip()
    flow = (payload.get("flow") or "checkout").strip().lower()

    if not code and not bib:
        return jsonify({"error": "A code or bib number is required."}), 400

    if code:
        field_name, value, label = CODE_FIELD_NAME, code, f'code "{code}"'
    else:
        field_name, value, label = PARTICIPANT_MATCH_FIELD, bib, f'bib number "{bib}"'

    try:
        result = find_participant_by_field(field_name, value)
        if not result:
            return jsonify({"error": f"No participant found for {label}."}), 404

        currently_active = is_live_status_allowed(result.get("liveStatus"))

        if flow == "checkin":
            if currently_active:
                return jsonify({"error": "Participant is already Active/Airborne."}), 409
        else:
            if not currently_active:
                return jsonify({"error": "Participant has already checked out."}), 409

        return jsonify(result)

    except requests.HTTPError as exc:
        detail = str(exc)
        status_code = 500
        if exc.response is not None:
            status_code = exc.response.status_code
            try:
                detail = exc.response.json().get("error", {}).get("message", detail)
            except ValueError:
                pass
        app.logger.error("Airtable request failed: %s", detail)
        return jsonify({"error": detail}), status_code

    except Exception as exc:  # noqa: BLE001
        app.logger.exception("Unexpected error looking up participant")
        return jsonify({"error": str(exc)}), 500


def get_participant_fields(participant_id):
    url = f"{API_BASE}/{AIRTABLE_BASE_ID}/{PARTICIPANTS_TABLE}/{participant_id}"
    resp = requests.get(url, headers=airtable_headers(), timeout=10)
    resp.raise_for_status()
    return resp.json().get("fields", {})


@app.route("/api/checkin", methods=["POST"])
def checkin():
    payload = request.get_json(force=True, silent=True) or {}

    participant_id = (payload.get("participantId") or "").strip()

    if not participant_id:
        return jsonify({"error": "No participant selected. Look up a code first."}), 400

    try:
        # Re-check Live Status right before saving, in case time passed
        # since lookup (e.g. someone else already checked this person in).
        participant_fields = get_participant_fields(participant_id)
        live_status = participant_fields.get(LIVE_STATUS_FIELD_NAME)
        if is_live_status_allowed(live_status):
            return jsonify({"error": "Participant is already Active/Airborne."}), 409

        url = f"{API_BASE}/{AIRTABLE_BASE_ID}/{CHECKINS_TABLE}"
        fields = {"Participant": [participant_id]}
        resp = requests.post(
            url,
            headers=airtable_headers(),
            json={"fields": fields, "typecast": True},
            timeout=10,
        )
        resp.raise_for_status()
        return jsonify(resp.json())

    except requests.HTTPError as exc:
        detail = str(exc)
        status_code = 500
        if exc.response is not None:
            status_code = exc.response.status_code
            try:
                detail = exc.response.json().get("error", {}).get("message", detail)
            except ValueError:
                pass
        app.logger.error("Airtable request failed: %s", detail)
        return jsonify({"error": detail}), status_code

    except Exception as exc:  # noqa: BLE001
        app.logger.exception("Unexpected error saving check-in")
        return jsonify({"error": str(exc)}), 500


@app.route("/api/checkout", methods=["POST"])
def checkout():
    payload = request.get_json(force=True, silent=True) or {}

    participant_id = (payload.get("participantId") or "").strip()
    status_notes = (payload.get("statusNotes") or "").strip()
    notes = (payload.get("notes") or "").strip()
    latitude = payload.get("latitude")
    longitude = payload.get("longitude")

    if not participant_id:
        return jsonify({"error": "No participant selected. Look up a code first."}), 400
    if not status_notes:
        return jsonify({"error": "Status is required."}), 400
    if latitude is None or longitude is None:
        return jsonify({"error": "Location is required."}), 400

    try:
        # Re-check Live Status right before saving, in case time passed
        # since lookup (e.g. someone else already checked this person out).
        participant_fields = get_participant_fields(participant_id)
        live_status = participant_fields.get(LIVE_STATUS_FIELD_NAME)
        if not is_live_status_allowed(live_status):
            return jsonify({"error": "Participant has already checked out."}), 409

        url = f"{API_BASE}/{AIRTABLE_BASE_ID}/{CHECKOUTS_TABLE}"
        fields = {
            "Participant": [participant_id],
            "Latitude": latitude,
            "Longitude": longitude,
            "Status": status_notes,
            "Notes": notes,
        }
        resp = requests.post(
            url,
            headers=airtable_headers(),
            json={"fields": fields, "typecast": True},
            timeout=10,
        )
        resp.raise_for_status()
        return jsonify(resp.json())

    except requests.HTTPError as exc:
        detail = str(exc)
        status_code = 500
        if exc.response is not None:
            status_code = exc.response.status_code
            try:
                detail = exc.response.json().get("error", {}).get("message", detail)
            except ValueError:
                pass
        app.logger.error("Airtable request failed: %s", detail)
        return jsonify({"error": detail}), status_code

    except Exception as exc:  # noqa: BLE001
        app.logger.exception("Unexpected error saving check-out")
        return jsonify({"error": str(exc)}), 500


if __name__ == "__main__":
    if not AIRTABLE_TOKEN:
        app.logger.warning(
            "AIRTABLE_TOKEN is not set. Copy .env.example to .env and fill in your token."
        )
    app.run(debug=True, port=5000)
