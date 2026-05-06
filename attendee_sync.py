"""
T3 Attendee Sheet Sync
----------------------
Pulls approved guests from Luma API and writes new attendees to the
team's Google Sheet. Deduplicates by email — never overwrites columns
the team edits manually (Role, Status, Notes).

Portfolio project #2. Replaces the Cowork sync-attendee-sheet skill
with a standalone script that runs on GitHub Actions at zero token cost.
"""

# ── IMPORTS ──────────────────────────────────────────────────────────
import requests
import gspread
import os
import json
import time
import base64
from datetime import datetime
from collections import defaultdict

# google-auth is used by gspread under the hood for service account auth
from google.oauth2.service_account import Credentials


# ── CONFIGURATION ────────────────────────────────────────────────────

LUMA_API_KEY = os.environ.get("LUMA_API_KEY", "")
LUMA_EVENT_ID = os.environ.get("LUMA_EVENT_ID", "")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
SLACK_WEBHOOK_URL = os.environ.get("SLACK_WEBHOOK_URL", "")

# The Google credentials JSON is stored as a base64-encoded GitHub Secret.
# This decodes it back into a dictionary the auth library can use.
GOOGLE_CREDS_B64 = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")

# Sheet details
TAB_NAME = "RSVP Data"

# Columns the script writes to (0-indexed positions in the sheet)
# Role=0, Status=1, Name=2, First=3, Last=4, Email=5, Registered=6,
# Source=7, Company=8, Job Title=9, Company Size=10, LatAm Interest=11,
# LinkedIn=12, Notes=13
#
# Script NEVER touches: Role (0), Status (1), Notes (13)
WRITE_COLUMNS = {
    "name": 2,
    "first_name": 3,
    "last_name": 4,
    "email": 5,
    "registered": 6,
    "source": 7,
    "company": 8,
    "job_title": 9,
    "company_size": 10,
    "latam_interest": 11,
    "linkedin": 12,
}

# Luma registration question labels → our field names
# These match the questions on the T3 Live NYC Luma registration form.
QUESTION_MAP = {
    "What company do you work for?": "company",
    "What's your job title?": "job_title",
    "How many employees are at your company?": "company_size",
    "Are you interested in learning more about hiring software developers in LatAm?": "latam_interest",
    "LinkedIn": "linkedin",
}


# ── FUNCTION 1: GET APPROVED GUESTS FROM LUMA ────────────────────────
# Reuses the same Luma API pattern from the pacing bot.
# Returns a list of guest dictionaries with the fields we need.

def get_luma_guests():
    """Pull all approved guests from Luma and extract relevant fields."""

    url = "https://public-api.luma.com/v1/event/get-guests"
    headers = {"x-luma-api-key": LUMA_API_KEY}

    all_guests = []
    cursor = None

    while True:
        params = {
            "event_id": LUMA_EVENT_ID,
            "pagination_limit": 25,
        }
        if cursor:
            params["pagination_cursor"] = cursor

        response = requests.get(url, headers=headers, params=params)

        # Rate limit handling — wait once, then stop if still blocked
        if response.status_code == 429:
            print("⏳ Rate limited — waiting 90 seconds...")
            time.sleep(90)
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 429:
                print("❌ Still rate limited. Stopping.")
                return []

        if response.status_code != 200:
            print(f"ERROR: Luma API returned {response.status_code}")
            print(f"Response: {response.text}")
            return []

        data = response.json()
        entries = data.get("entries", [])

        for guest in entries:
            if guest.get("approval_status") != "approved":
                continue

            # Parse registration answers into a flat dictionary
            answers = {}
            for ans in guest.get("registration_answers", []):
                label = ans.get("label", "")
                value = ans.get("answer", "")
                # Match against our question map
                for question, field in QUESTION_MAP.items():
                    if question.lower() in label.lower():
                        answers[field] = value
                        break

            # Build the guest record matching our sheet columns
            registered_raw = guest.get("registered_at", "")
            registered_fmt = ""
            if registered_raw:
                try:
                    dt = datetime.fromisoformat(registered_raw.replace("Z", "+00:00"))
                    registered_fmt = dt.strftime("%Y-%m-%d")
                except ValueError:
                    registered_fmt = registered_raw.split("T")[0]

            all_guests.append({
                "name": guest.get("user_name", ""),
                "first_name": guest.get("user_first_name", ""),
                "last_name": guest.get("user_last_name", ""),
                "email": guest.get("user_email", ""),
                "registered": registered_fmt,
                "source": guest.get("custom_source") or guest.get("utm_source") or "",
                "company": answers.get("company", ""),
                "job_title": answers.get("job_title", ""),
                "company_size": answers.get("company_size", ""),
                "latam_interest": answers.get("latam_interest", ""),
                "linkedin": answers.get("linkedin", ""),
            })

        cursor = data.get("pagination_cursor")
        if not cursor or len(entries) == 0:
            break

    print(f"✓ Luma: {len(all_guests)} approved guests")
    return all_guests


# ── FUNCTION 2: CONNECT TO GOOGLE SHEETS ─────────────────────────────
# Uses a service account (robot Google account) to authenticate.
# The credentials come from a base64-encoded environment variable.

def connect_to_sheet():
    """Authenticate with Google and return the worksheet object."""

    if not GOOGLE_CREDS_B64:
        print("ERROR: No Google credentials found in GOOGLE_SHEETS_CREDENTIALS")
        return None

    # Decode the base64 credentials back to JSON
    creds_json = json.loads(base64.b64decode(GOOGLE_CREDS_B64))

    # Authenticate using the service account
    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    credentials = Credentials.from_service_account_info(creds_json, scopes=scopes)
    client = gspread.authorize(credentials)

    # Open the spreadsheet by ID and select the tab
    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    worksheet = spreadsheet.worksheet(TAB_NAME)

    print(f"✓ Connected to Google Sheet: {spreadsheet.title} → {TAB_NAME}")
    return worksheet


# ── FUNCTION 3: SYNC GUESTS TO SHEET ─────────────────────────────────
# The core logic: read existing emails, skip duplicates, append new rows.
# NEVER touches Role, Status, or Notes columns.

def sync_to_sheet(worksheet, guests):
    """Write new guests to the sheet, skipping any already present."""

    # Read all existing emails from column F (index 5, but gspread is 1-based = col 6)
    existing_emails_raw = worksheet.col_values(6)  # Column F = Email
    # Skip header row, normalize to lowercase for matching
    existing_emails = {e.strip().lower() for e in existing_emails_raw[1:] if e.strip()}

    print(f"  Existing rows: {len(existing_emails)}")

    # Filter to only new guests
    new_guests = [
        g for g in guests
        if g["email"].strip().lower() not in existing_emails
    ]

    if not new_guests:
        print("  No new guests to add.")
        return 0

    # Build rows to append — one list per guest, matching column order.
    # Leave Role (col A) and Status (col B) empty. Leave Notes (col N) empty.
    rows_to_add = []
    for g in new_guests:
        row = [
            "",                     # A: Role (team fills)
            "",                     # B: Status (team fills)
            g["name"],              # C: Name
            g["first_name"],        # D: First Name
            g["last_name"],         # E: Last Name
            g["email"],             # F: Email
            g["registered"],        # G: Registered
            g["source"],            # H: Source
            g["company"],           # I: Company
            g["job_title"],         # J: Job Title
            g["company_size"],      # K: Company Size
            g["latam_interest"],    # L: LatAm Interest
            g["linkedin"],          # M: LinkedIn
            "",                     # N: Notes (team fills)
        ]
        rows_to_add.append(row)

    # Append all new rows at once (batch write — fast and efficient)
    worksheet.append_rows(rows_to_add, value_input_option="USER_ENTERED")

    print(f"  ✓ Added {len(rows_to_add)} new guests")
    return len(rows_to_add)


# ── FUNCTION 4: POST TO SLACK ────────────────────────────────────────

def post_to_slack(new_count, total_count):
    """Send a summary to Slack."""

    today_str = datetime.now().strftime("%A, %B %d")
    message = (
        f"📋 *Attendee Sheet Updated | {today_str}*\n\n"
        f"New guests added: *{new_count}*\n"
        f"Total approved guests: *{total_count}*\n\n"
        f"📝 _Auto-synced from Luma. Review and tag ICP in the sheet._"
    )

    if not SLACK_WEBHOOK_URL:
        print(f"\n📋 SLACK MESSAGE (no webhook configured):\n")
        print(message)
        print()
        return

    response = requests.post(SLACK_WEBHOOK_URL, json={"text": message})
    if response.status_code == 200:
        print("✓ Posted to Slack")
    else:
        print(f"WARNING: Slack returned {response.status_code}")


# ── MAIN ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"📋 Attendee Sheet Sync")
    print(f"   Running at {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    # Step 1: Pull guests from Luma
    guests = get_luma_guests()
    if not guests:
        print("No guests found or API error. Exiting.")
        exit(1)

    # Step 2: Connect to Google Sheet
    worksheet = connect_to_sheet()
    if not worksheet:
        print("Could not connect to Google Sheet. Exiting.")
        exit(1)

    # Step 3: Sync new guests
    new_count = sync_to_sheet(worksheet, guests)

    # Step 4: Notify
    post_to_slack(new_count, len(guests))

    print("=" * 50)
    print("✓ Done")
