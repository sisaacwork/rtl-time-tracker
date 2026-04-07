"""
sync_from_github.py
====================
Downloads the latest Excel tracker files from GitHub and saves them into your
local OneDrive folder so the SharePoint Master tracker stays in sync with any
changes made through the Streamlit web app.

SETUP — do this once
---------------------
1. Edit ONEDRIVE_PATH below to match where OneDrive syncs the Excel files on
   your Mac.  To find the right path:
     - Open Finder and navigate to the folder that holds the Excel trackers
     - Right-click > Get Info
     - Copy the path shown next to "Where:"

2. Set your GitHub token as an environment variable so this script can
   authenticate.  In Terminal:
       export GITHUB_TOKEN="ghp_your_token_here"
   (You'll create this token in the next step of the setup guide.)

3. Run manually to test:
       python3 sync_from_github.py

4. Schedule it to run automatically every hour on weekdays.
   Open Terminal and type:
       crontab -e
   Then add this line (it runs at the top of every hour, Mon-Fri):
       0 * * * 1-5 cd /Users/sisaacwork/Documents/GitHub/rtl-time-tracker && GITHUB_TOKEN="ghp_your_token_here" python3 sync_from_github.py >> rtl_sync.log 2>&1
   Save and exit (press Escape, then type :wq and press Enter).
"""

import os
import base64
import requests
from pathlib import Path
from datetime import datetime

# ── Configuration — edit ONEDRIVE_PATH before running ─────────────────────────

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER  = "sisaacwork"
GITHUB_REPO   = "rtl-time-tracker"

# Full path to the folder on your Mac where OneDrive syncs the Excel trackers.
# Replace the placeholder below with your actual path.
ONEDRIVE_PATH = os.environ.get(
    "ONEDRIVE_PATH",
    "/Users/sisaacwork/Library/CloudStorage/OneDrive-CouncilonTallBuildingsandUrbanHabitat/Staff - Research/Time Tracking/2026"
)

TRACKER_FILES = [
    "DSafarik_2026TimeTracking.xlsx",
    "IWork_2026TimeTracking.xlsx",
    "SUrsini_2026TimeTracking.xlsx",
    "WMiranda_2026TimeTracking.xlsx",
]

# ── Sync logic ────────────────────────────────────────────────────────────────

def download_file(filename: str, dest_folder: Path) -> tuple:
    """Download one file from GitHub and write it to dest_folder."""
    url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/contents/{filename}"
    )
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github.v3+json",
    }

    resp = requests.get(url, headers=headers)
    if resp.status_code != 200:
        return False, f"GitHub error {resp.status_code}"

    # GitHub returns file content as a base64 string
    raw_bytes = base64.b64decode(resp.json()["content"].replace("\n", ""))

    dest = dest_folder / filename
    with open(dest, "wb") as f:
        f.write(raw_bytes)

    return True, f"Written to {dest}"


def main():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{timestamp}] RTL sync: GitHub -> OneDrive")

    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN is not set.")
        print("       In Terminal, run:  export GITHUB_TOKEN='ghp_your_token_here'")
        raise SystemExit(1)

    if "REPLACE-THIS" in ONEDRIVE_PATH:
        print("ERROR: Please edit the ONEDRIVE_PATH variable in this script first.")
        raise SystemExit(1)

    dest = Path(ONEDRIVE_PATH)
    if not dest.exists():
        print(f"ERROR: Folder not found: {dest}")
        print("       Check that OneDrive is running and the path is correct.")
        raise SystemExit(1)

    all_ok = True
    for filename in TRACKER_FILES:
        ok, msg = download_file(filename, dest)
        print(f"  {'OK ' if ok else 'ERR'} {filename} — {msg}")
        if not ok:
            all_ok = False

    print("Done." if all_ok else "Finished with errors (see above).")


if __name__ == "__main__":
    main()
