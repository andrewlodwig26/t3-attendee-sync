# T3 Attendee Sheet Sync

Automatically syncs approved event registrations from Luma to a shared Google Sheet. Deduplicates by email, preserves team-edited columns (Role, Status, Notes), and runs on GitHub Actions every weekday morning.

Replaces a manual CSV-export-and-sync workflow that took 20 minutes and heavy AI token usage with a zero-cost automated script.

## What it does

1. **Pulls approved guests** from the Luma API (handles pagination)
2. **Reads existing emails** from the Google Sheet to avoid duplicates
3. **Appends new guests** with registration data (name, company, title, etc.)
4. **Never overwrites** Role, Status, or Notes columns — those belong to the team
5. **Posts a Slack summary** with how many new guests were added

## Column mapping

| Sheet Column | Source | Script Writes? |
|---|---|---|
| Role | Team fills manually | **No** |
| Status | Team fills manually | **No** |
| Name | Luma: user_name | Yes |
| First Name | Luma: user_first_name | Yes |
| Last Name | Luma: user_last_name | Yes |
| Email | Luma: user_email | Yes (dedup key) |
| Registered | Luma: registered_at | Yes |
| Source | Luma: custom_source / utm_source | Yes |
| Company | Registration Q: "What company?" | Yes |
| Job Title | Registration Q: "Job title?" | Yes |
| Company Size | Registration Q: "How many employees?" | Yes |
| LatAm Interest | Registration Q: "Interested in LatAm hiring?" | Yes |
| LinkedIn | Registration Q: "LinkedIn" | Yes |
| Notes | Team fills manually | **No** |

## Setup

### Prerequisites
- Python 3.9+
- Luma API key
- Google Cloud service account with Sheets API enabled
- The Google Sheet shared with the service account email

### Environment variables

| Variable | Required | Description |
|---|---|---|
| `LUMA_API_KEY` | Yes | Luma API key |
| `LUMA_EVENT_ID` | Yes | Luma event ID |
| `GOOGLE_SHEET_ID` | Yes | The long ID from the Google Sheet URL |
| `GOOGLE_SHEETS_CREDENTIALS` | Yes | Service account JSON, base64-encoded |
| `SLACK_WEBHOOK_URL` | No | Slack webhook for notifications |

### Run locally

```bash
export LUMA_API_KEY="your-key"
export LUMA_EVENT_ID="evt-your-id"
export GOOGLE_SHEET_ID="your-sheet-id"
export GOOGLE_SHEETS_CREDENTIALS="base64-encoded-creds"
python3 attendee_sync.py
```

### Encode credentials for GitHub Secrets

```bash
base64 -i your-credentials.json | tr -d '\n'
```

Paste the output as the `GOOGLE_SHEETS_CREDENTIALS` secret.

## Author

Andrew Lodwig — Field Marketing Manager → aspiring RevOps builder. Portfolio project #2.
