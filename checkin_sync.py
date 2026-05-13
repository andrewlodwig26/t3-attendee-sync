"""
T3 Check-In Sync
----------------
Pulls check-in data from Luma API and writes "Attended" or
"Did Not Attend" to column B (Status) in the team's Google Sheet.
Matches guests to sheet rows by email address.

Designed to run post-event via manual GitHub Actions trigger.
"""

# ── IMPORTS ──────────────────────────────────────────────────────────
import requests
import gspread
import os
import json
import time
import base64
from datetime import datetime

from google.oauth2.service_account import Credentials

# ── CONFIGURATION ────────────────────────────────────────────────────

LUMA_API_KEY = os.environ.get("LUMA_API_KEY", "")
LUMA_EVENT_ID = os.environ.get("LUMA_EVENT_ID", "")
GOOGLE_SHEET_ID = os.environ.get("GOOGLE_SHEET_ID", "")
GOOGLE_CREDS_B64 = os.environ.get("GOOGLE_SHEETS_CREDENTIALS", "")

TAB_NAME = "RSVP Data"

# Column positions (1-based for gspread)
STATUS_COL = 2   # Column B
EMAIL_COL = 6    # Column F

# ── FUNCTION 1: GET CHECK-IN DATA FROM LUMA ──────────────────────────

def get_checkin_data():
    """Pull all approved guests and their check-in status from Luma."""

    url = "https://public-api.luma.com/v1/event/get-guests"
    headers = {"x-luma-api-key": LUMA_API_KEY}

    checkin_map = {}  # email → True/False
    cursor = None

    while True:
        params = {
            "event_id": LUMA_EVENT_ID,
            "pagination_limit": 25,
        }
        if cursor:
            params["pagination_cursor"] = cursor

        response = requests.get(url, headers=headers, params=params)

        if response.status_code == 429:
            print("⏳ Rate limited — waiting 90 seconds...")
            time.sleep(90)
            response = requests.get(url, headers=headers, params=params)
            if response.status_code == 429:
                print("❌ Still rate limited. Stopping.")
                return {}

        if response.status_code != 200:
            print(f"ERROR: Luma API returned {response.status_code}")
            print(f"Response: {response.text}")
            return {}

        data = response.json()
        entries = data.get("entries", [])

        for guest in entries:
            if guest.get("approval_status") != "approved":
                continue

            email = (guest.get("user_email") or "").strip().lower()
            if not email:
                continue

            # checked_in_at is non-null if the guest checked in
            checked_in = bool(guest.get("checked_in_at"))
            checkin_map[email] = checked_in

        cursor = data.get("next_cursor")
        if not cursor or len(entries) == 0:
            break

    attended = sum(1 for v in checkin_map.values() if v)
    print(f"✓ Luma: {len(checkin_map)} approved guests, {attended} checked in")
    return checkin_map

# ── FUNCTION 2: CONNECT TO GOOGLE SHEETS ─────────────────────────────

def connect_to_sheet():
    """Authenticate with Google and return the worksheet object."""

    if not GOOGLE_CREDS_B64:
        print("ERROR: No Google credentials found in GOOGLE_SHEETS_CREDENTIALS")
        return None

    creds_json = json.loads(base64.b64decode(GOOGLE_CREDS_B64))

    scopes = [
        "https://www.googleapis.com/auth/spreadsheets",
    ]
    credentials = Credentials.from_service_account_info(creds_json, scopes=scopes)
    client = gspread.authorize(credentials)

    spreadsheet = client.open_by_key(GOOGLE_SHEET_ID)
    worksheet = spreadsheet.worksheet(TAB_NAME)

    print(f"✓ Connected to Google Sheet: {spreadsheet.title} → {TAB_NAME}")
    return worksheet

# ── FUNCTION 3: WRITE CHECK-IN STATUS TO SHEET ───────────────────────

def sync_checkin_status(worksheet, checkin_map):
    """Match emails in the sheet to Luma check-in data and update column B."""

    # Read all emails from column F (1-based)
    all_emails = worksheet.col_values(EMAIL_COL)
    # Read existing statuses from column B
    all_statuses = worksheet.col_values(STATUS_COL)

    # Pad statuses list if shorter than emails list
    while len(all_statuses) < len(all_emails):
        all_statuses.append("")

    updates = []  # List of (row, value) to batch update
    matched = 0
    skipped_existing = 0
    not_found = 0

    # Start from row 2 (skip header)
    for i in range(1, len(all_emails)):
        email = all_emails[i].strip().lower()
        if not email:
            continue

        current_status = all_statuses[i].strip()

        # Skip rows that already have a status value
        if current_status:
            skipped_existing += 1
            continue

        if email in checkin_map:
            status = "Attended" if checkin_map[email] else "Did Not Attend"
            row_num = i + 1  # gspread is 1-based
            updates.append({
                "range": f"B{row_num}",
                "values": [[status]],
            })
            matched += 1
        else:
            not_found += 1

    if not updates:
        print("  No status updates needed.")
        return 0

    # Batch update all cells at once
    worksheet.batch_update(updates, value_input_option="USER_ENTERED")

    print(f"  ✓ Updated {matched} rows with check-in status")
    print(f"  Skipped {skipped_existing} rows (already had status)")
    if not_found > 0:
        print(f"  ⚠ {not_found} sheet emails not found in Luma data")

    return matched

# ── MAIN ─────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"✅ Check-In Sync")
    print(f"   Running at {datetime.now().strftime('%Y-%m-%d %H:%M')}")
    print("=" * 50)

    # Step 1: Pull check-in data from Luma
    checkin_map = get_checkin_data()
    if not checkin_map:
        print("No check-in data found or API error. Exiting.")
        exit(1)

    # Step 2: Connect to Google Sheet
    worksheet = connect_to_sheet()
    if not worksheet:
        print("Could not connect to Google Sheet. Exiting.")
        exit(1)

    # Step 3: Write check-in status to column B
    updated_count = sync_checkin_status(worksheet, checkin_map)

    print("=" * 50)
    print("✓ Done")
