import os
import pathlib
import time
import sys
import msal
import requests
from typing import Dict, Any, Optional, List

# --------------------------------------------------------------------
SP_HOSTNAME  = "lnttsgroup.sharepoint.com"
SP_SITE_PATH = "/sites/CICD-Automation"
SVN_URL      = "svn://100.127.6.223/CREATE_SVN_REPO/trunk"
# --------------------------------------------------------------------

GRAPH_SCOPE = ["https://graph.microsoft.com/.default"]
GRAPH_BASE  = "https://graph.microsoft.com/v1.0"

# --------- ⚠️ SECURITY: Consider moving these to env vars ----------
TENANT_ID     = os.environ["GRAPH_TENANT_ID"]
CLIENT_ID     = os.environ["GRAPH_CLIENT_ID"]
CLIENT_SECRET = os.environ["GRAPH_CLIENT_SECRET"]
# -------------------------------------------------------------------

# Only sync this subfolder under the library (relative to library root)
START_PATH    = "CICD_Automation"

# Local download root
DOWNLOAD_ROOT = pathlib.Path("sharepoint_sync")

# Streaming chunk size (bytes)
CHUNK_SIZE = 4 * 1024 * 1024  # 4 MB

def log(msg: str) -> None:
    print(msg, flush=True)

def clean_name(name: str) -> str:
    # Minimal sanitization for Windows paths; harmless elsewhere.
    return "".join(c for c in name if c not in '<>:"/\\|?*')

def get_token() -> str:
    """Acquire an app-only token via MSAL (client credentials)."""
    log("[Init] Acquiring token...")
    app = msal.ConfidentialClientApplication(
        CLIENT_ID,
        authority=f"https://login.microsoftonline.com/{TENANT_ID}",
        client_credential=CLIENT_SECRET
    )
    result = app.acquire_token_for_client(scopes=GRAPH_SCOPE)
    if "access_token" not in result:
        raise RuntimeError(f"Token acquisition failed: {result}")
    return result["access_token"]

def request_with_retry(method: str,
                       url: str,
                       headers: Dict[str, str],
                       params: Optional[Dict[str, str]] = None,
                       stream: bool = False,
                       timeout: tuple = (15, 120),
                       max_retries: int = 6) -> requests.Response:
    """
    Generic HTTP request with exponential backoff for throttling/transient errors.
    Respects Retry-After when provided.
    """
    session = requests.Session()
    for attempt in range(max_retries):
        resp = session.request(method, url, headers=headers, params=params,
                               stream=stream, timeout=timeout)
        if resp.status_code in (429, 503, 504):
            retry_after = resp.headers.get("Retry-After")
            if retry_after and retry_after.isdigit():
                delay = int(retry_after)
            else:
                delay = min(60, 2 ** attempt)  # exponential fallback
            log(f"[Retry] {resp.status_code} on {url} — waiting {delay}s (attempt {attempt+1}/{max_retries})")
            time.sleep(delay)
            continue
        resp.raise_for_status()
        return resp
    resp.raise_for_status()
    return resp

def graph_get(url: str, token: str, params: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Perform a GET to Microsoft Graph returning JSON."""
    resp = request_with_retry("GET", url, headers={"Authorization": f"Bearer {token}"}, params=params)
    return resp.json()

def graph_stream_to_file(url: str, token: str, dest_path: pathlib.Path) -> None:
    """Stream file content to disk (handles large files safely)."""
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    with request_with_retry("GET", url, headers={"Authorization": f"Bearer {token}"}, stream=True) as resp:
        total = resp.headers.get("Content-Length")
        total_int = int(total) if total and total.isdigit() else None
        downloaded = 0
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=CHUNK_SIZE):
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total_int and downloaded % (10 * 1024 * 1024) < CHUNK_SIZE:
                        pct = (downloaded / total_int) * 100
                        log(f"    [Progress] {dest_path.name}: {pct:.1f}% ({downloaded}/{total_int} bytes)")

def get_site_id(token: str) -> str:
    """
    Resolve the site by server-relative path:
    GET /sites/{hostname}:/{server-relative-path}
    """
    log("[Init] Resolving site...")
    url = f"{GRAPH_BASE}/sites/{SP_HOSTNAME}:{SP_SITE_PATH}"
    data = graph_get(url, token)
    site_id = data["id"]
    log(f"[Init] Site ID: {site_id}")
    return site_id

def get_default_drive(token: str, site_id: str) -> Dict[str, Any]:
    """
    Get the default document library as a Drive:
    GET /sites/{site-id}/drive
    """
    url = f"{GRAPH_BASE}/sites/{site_id}/drive"
    drive = graph_get(url, token)
    log(f"[Drive] Using default library '{drive.get('name', 'Documents')}' (id: {drive['id']})")
    return drive

def get_item_by_path(token: str, drive_id: str, rel_path: str) -> Optional[Dict[str, Any]]:
    """Resolve an item (folder/file) by relative path. Returns None if not found."""
    if not rel_path:
        url = f"{GRAPH_BASE}/drives/{drive_id}/root"
    else:
        url = f"{GRAPH_BASE}/drives/{drive_id}/root:/{rel_path}"
    try:
        return graph_get(url, token)
    except requests.HTTPError as e:
        if e.response is not None and e.response.status_code == 404:
            return None
        raise

def list_children(token: str, drive_id: str, rel_path: Optional[str] = None) -> List[Dict[str, Any]]:
    """
    List children under root or a subfolder:
    - root:   /drives/{drive-id}/root/children
    - folder: /drives/{drive-id}/root:/{rel_path}:/children
    Handles @odata.nextLink pagination.
    """
    if rel_path:
        url = f"{GRAPH_BASE}/drives/{drive_id}/root:/{rel_path}:/children"
    else:
        url = f"{GRAPH_BASE}/drives/{drive_id}/root/children"

    items: List[Dict[str, Any]] = []
    next_url: str = url
    params: Optional[Dict[str, str]] = {"$top": "200"}
    while True:
        data = graph_get(next_url, token, params=params)
        items.extend(data.get("value", []))
        next_link = data.get("@odata.nextLink")
        if not next_link:
            break
        next_url, params = next_link, None
        time.sleep(0.05)
    return items

def download_item(token: str, drive_id: str, item: Dict[str, Any], base_path: pathlib.Path) -> None:
    """Download a single file item to base_path (streaming)."""
    name = clean_name(item["name"])
    dest = base_path / name
    url = f"{GRAPH_BASE}/drives/{drive_id}/items/{item['id']}/content"
    log(f"[DL] {item['name']} -> {dest}")
    graph_stream_to_file(url, token, dest)

def walk_and_download(token: str,
                      drive_id: str,
                      folder_path: str = "",
                      base_path: pathlib.Path = DOWNLOAD_ROOT) -> None:
    """Recursively traverse folders and download files, preserving structure."""
    children = list_children(token, drive_id, rel_path=folder_path or None)
    for it in children:
        if it.get("folder"):
            sub_name = clean_name(it["name"])
            new_rel = f"{folder_path}/{it['name']}" if folder_path else it["name"]
            new_base = base_path / sub_name
            log(f"[Dir] {new_rel}")
            walk_and_download(token, drive_id, new_rel, new_base)
        elif it.get("file"):
            download_item(token, drive_id, it, base_path)
        # ignore other facets

def main() -> None:
    token   = get_token()
    site_id = get_site_id(token)
    drive   = get_default_drive(token, site_id)
    drive_id = drive["id"]

    # Validate START_PATH exists and is a folder
    log(f"[Check] Validating path in library: '{START_PATH or '/'}'")
    item = get_item_by_path(token, drive_id, START_PATH) if START_PATH else get_item_by_path(token, drive_id, "")
    if not item:
        log(f"❌ Path '{START_PATH}' not found in library '{drive.get('name', 'Documents')}'.")
        sys.exit(2)
    if START_PATH and not item.get("folder"):
        log(f"❌ START_PATH '{START_PATH}' exists but is not a folder.")
        sys.exit(3)

    # Prepare local base
    base = DOWNLOAD_ROOT / (START_PATH.strip("/") if START_PATH else "")
    base.mkdir(parents=True, exist_ok=True)

    log(f"[Run] Downloading '{START_PATH or '/'}' to '{base}' (chunk={CHUNK_SIZE//(1024*1024)}MB)...")
    walk_and_download(token, drive_id, START_PATH, base)
    log(f"[OK] Download complete: '{START_PATH or '/'}' -> '{base}'")
    log(f"[INFO] SVN target URL (for workflow step): {SVN_URL}")

if __name__ == "__main__":
    main()
