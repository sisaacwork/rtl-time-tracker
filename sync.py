"""
sync.py  —  Bidirectional sync between OneDrive and GitHub
============================================================
Compares the modification time of each Excel tracker on OneDrive against
the last commit time for that file on GitHub, then syncs in the right direction:

  OneDrive newer  →  upload to GitHub   (captures manual Excel edits)
  GitHub   newer  →  download to local  (captures web-app saves)

Run manually:
    python3 sync.py

Schedule hourly on weekdays via cron (run `crontab -e` and add):
    0 * * * 1-5 cd /Users/sisaacwork/Documents/GitHub/rtl-time-tracker && GITHUB_TOKEN="ghp_your_token_here" python3 sync.py >> rtl_sync.log 2>&1
"""

import os
import base64
import requests
from pathlib import Path
from datetime import datetime, timezone

# ── Configuration ──────────────────────────────────────────────────────────────

GITHUB_TOKEN  = os.environ.get("GITHUB_TOKEN", "")
GITHUB_OWNER  = "sisaacwork"
GITHUB_REPO   = "rtl-time-tracker"
GITHUB_BRANCH = "main"

# Folder on your Mac where OneDrive syncs the Excel tracker files.
ONEDRIVE_PATH = os.environ.get(
    "ONEDRIVE_PATH",
    "/Users/sisaacwork/Library/CloudStorage/OneDrive-CouncilonTallBuildingsandUrbanHabitat"
    "/Staff - Research/Time Tracking/2026"
)

TRACKER_FILES = [
    "DSafarik_2026TimeTracking.xlsx",
    "IWork_2026TimeTracking.xlsx",
    "SUrsini_2026TimeTracking.xlsx",
    "WMiranda_2026TimeTracking.xlsx",
]

# ── GitHub helpers ──────────────────────────────────────────────────────────────

def _headers() -> dict:
    return {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept":        "application/vnd.github.v3+json",
    }


def github_last_modified(filename: str) -> datetime | None:
    """
    Return the UTC datetime of the last commit that touched this file,
    or None if the file doesn't exist on GitHub yet.
    """
    url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/commits?path={filename}&per_page=1&sha={GITHUB_BRANCH}"
    )
    resp = requests.get(url, headers=_headers())
    if resp.status_code != 200 or not resp.json():
        return None
    ts = resp.json()[0]["commit"]["committer"]["date"]   # e.g. "2026-04-07T14:23:00Z"
    return datetime.fromisoformat(ts.replace("Z", "+00:00"))


def github_download(filename: str, dest: Path) -> tuple:
    """Download a file from GitHub and write it to dest."""
    url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/contents/{filename}?ref={GITHUB_BRANCH}"
    )
    resp = requests.get(url, headers=_headers())
    if resp.status_code != 200:
        return False, f"GitHub error {resp.status_code}"

    raw = base64.b64decode(resp.json()["content"].replace("\n", ""))
    dest.write_bytes(raw)
    return True, f"Downloaded to {dest}"


def github_upload(filename: str, source: Path) -> tuple:
    """Upload a local file to GitHub, replacing the existing version."""
    url = (
        f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}"
        f"/contents/{filename}"
    )

    # Need the current SHA to update an existing file
    r   = requests.get(url, headers=_headers())
    sha = r.json().get("sha") if r.status_code == 200 else None

    payload = {
        "message": (
            f"Sync from OneDrive: {filename} "
            f"[{datetime.utcnow().strftime('%Y-%m-%d %H:%M')} UTC]"
        ),
        "content": base64.b64encode(source.read_bytes()).decode(),
        "branch":  GITHUB_BRANCH,
    }
    if sha:
        payload["sha"] = sha

    resp = requests.put(url, headers=_headers(), json=payload)
    if resp.status_code in (200, 201):
        return True, f"Uploaded to GitHub"
    return False, f"GitHub error {resp.status_code}: {resp.text[:200]}"


# ── Main sync logic ─────────────────────────────────────────────────────────────

def sync_file(filename: str, onedrive_folder: Path) -> str:
    """
    Compare OneDrive vs GitHub timestamps for one file and sync accordingly.
    Returns a short status string for logging.
    """
    local = onedrive_folder / filename

    # ── Get OneDrive modification time (convert to UTC-aware) ─────────────────
    if local.exists():
        local_mtime = datetime.fromtimestamp(
            local.stat().st_mtime, tz=timezone.utc
        )
    else:
        local_mtime = None

    # ── Get GitHub last-commit time ────────────────────────────────────────────
    github_mtime = github_last_modified(filename)

    # ── Decide direction ───────────────────────────────────────────────────────
    if local_mtime is None and github_mtime is None:
        return "SKIP — file not found in either location"

    if github_mtime is None:
        # File exists locally but not on GitHub — upload it
        ok, msg = github_upload(filename, local)
        return f"{'UP  ' if ok else 'ERR '} OneDrive → GitHub  ({msg})"

    if local_mtime is None:
        # File exists on GitHub but not locally — download it
        ok, msg = github_download(filename, local)
        return f"{'DOWN' if ok else 'ERR '} GitHub → OneDrive  ({msg})"

    diff = (local_mtime - github_mtime).total_seconds()

    if diff > 60:
        # OneDrive copy is more than 60 s newer — upload to GitHub
        ok, msg = github_upload(filename, local)
        return f"{'UP  ' if ok else 'ERR '} OneDrive → GitHub  (OneDrive +{diff/60:.1f} min newer)"

    elif diff < -60:
        # GitHub copy is more than 60 s newer — download to OneDrive
        ok, msg = github_download(filename, local)
        return f"{'DOWN' if ok else 'ERR '} GitHub → OneDrive  (GitHub +{-diff/60:.1f} min newer)"

    else:
        return f"OK   In sync (difference {diff:.0f} s)"


def main():
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
    print(f"[{timestamp}] RTL sync (bidirectional)")

    if not GITHUB_TOKEN:
        print("ERROR: GITHUB_TOKEN is not set.")
        print("       In Terminal, run:  export GITHUB_TOKEN='ghp_your_token_here'")
        raise SystemExit(1)

    folder = Path(ONEDRIVE_PATH)
    if not folder.exists():
        print(f"ERROR: OneDrive folder not found: {folder}")
        print("       Check that OneDrive is running and the path is correct.")
        raise SystemExit(1)

    for filename in TRACKER_FILES:
        status = sync_file(filename, folder)
        print(f"  {filename:<40} {status}")

    print("Done.")


if __name__ == "__main__":
    main()
