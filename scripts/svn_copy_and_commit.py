import os
import sys
import shutil
import tempfile
import subprocess
from pathlib import Path

def run(cmd, cwd=None, check=True):
    """Run a command and return (returncode, stdout, stderr)."""
    print(f"==> RUN: {cmd} (cwd={cwd})")
    proc = subprocess.run(
        cmd,
        cwd=cwd,
        shell=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True
    )
    print(proc.stdout)
    if proc.stderr.strip():
        print(proc.stderr, file=sys.stderr)
    if check and proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {cmd}\n{proc.stderr}")
    return proc.returncode, proc.stdout, proc.stderr

def validate_env():
    required = ["SOURCE_PATH", "SVN_URL", "SVN_USERNAME", "SVN_PASSWORD", "COMMIT_MESSAGE"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise EnvironmentError(f"Missing environment variables: {', '.join(missing)}")
    source_path = Path(os.environ["SOURCE_PATH"])
    if not source_path.exists() or not source_path.is_dir():
        raise FileNotFoundError(f"SOURCE_PATH does not exist or is not a directory: {source_path}")
    return source_path, os.environ["SVN_URL"], os.environ["SVN_USERNAME"], os.environ["SVN_PASSWORD"], os.environ["COMMIT_MESSAGE"]

def svn_checkout(url, username, password, dest):
    # --non-interactive prevents prompts; --trust-server-cert for self-signed
    cmd = (
        f'svn checkout "{url}" "{dest}" '
        f'--username "{username}" --password "{password}" '
        f'--no-auth-cache --non-interactive --trust-server-cert'
    )
    run(cmd)

def copy_source_to_wc(source_dir: Path, wc_dir: Path):
    """
    Copy all files/directories from source_dir into wc_dir.
    Preserves folder structure. Skips .svn directories.
    """
    def ignore_svn(dirpath, names):
        # Do not touch the working copy's .svn, and skip any .svn inside source
        return {name for name in names if name.lower() == '.svn'}

    # Create target directory if missing
    wc_dir.mkdir(parents=True, exist_ok=True)

    # Copy each top-level entry from source into wc_dir
    for entry in source_dir.iterdir():
        src = entry
        dst = wc_dir / entry.name
        if entry.is_dir():
            # Copy directory tree; merge into existing dir
            if dst.exists():
                # Merge copy: copy files recursively
                for root, dirs, files in os.walk(src):
                    rel = Path(root).relative_to(src)
                    target_root = dst / rel
                    target_root.mkdir(parents=True, exist_ok=True)
                    # filter out .svn
                    dirs[:] = [d for d in dirs if d.lower() != '.svn']
                    for f in files:
                        if f.lower() == '.svn':
                            continue
                        s = Path(root) / f
                        t = target_root / f
                        shutil.copy2(s, t)
            else:
                shutil.copytree(src, dst, ignore=ignore_svn, dirs_exist_ok=True)
        else:
            shutil.copy2(src, dst)

def svn_stage_changes(wc_dir: Path):
    """
    Run svn add/delete to reflect filesystem changes.
    - Add all unversioned or missing entries using `svn add --force`.
    - Delete entries marked missing or scheduled for deletion.
    """
    # Add any new/modified files
    run('svn add --force --parents .', cwd=str(wc_dir))

    # Detect missing versioned files and delete them from SVN
    # 'svn status' outputs lines starting with:
    #   !  item is missing (removed from working copy)
    #   D  scheduled for deletion
    #   ?  unversioned
    # We'll delete the '!' and keep 'D' as is.
    rc, out, _ = run('svn status', cwd=str(wc_dir), check=False)
    missing_paths = []
    for line in out.splitlines():
        line = line.rstrip()
        if not line:
            continue
        flag = line[0]
        # path is last token; status format is "FLAG ...... PATH"
        path = line[1:].strip()
        if flag == '!':
            missing_paths.append(path)

    # Delete missing files from SVN (if any)
    for p in missing_paths:
        # Use --force to remove directories
        run(f'svn delete --force "{p}"', cwd=str(wc_dir))

def svn_commit(wc_dir: Path, msg: str, username: str, password: str):
    cmd = (
        f'svn commit -m "{msg}" '
        f'--username "{username}" --password "{password}" '
        f'--no-auth-cache --non-interactive --trust-server-cert'
    )
    run(cmd, cwd=str(wc_dir))

def main():
    source_dir, svn_url, svn_user, svn_pass, commit_msg = validate_env()

    # Create temporary working directory for checkout
    with tempfile.TemporaryDirectory(prefix="svn_sync_") as tmpdir:
        wc_dir = Path(tmpdir) / "wc"
        print(f"Working copy: {wc_dir}")
        svn_checkout(svn_url, svn_user, svn_pass, str(wc_dir))
        copy_source_to_wc(source_dir, wc_dir)
        svn_stage_changes(wc_dir)
        # Optional: show final status
        run('svn status', cwd=str(wc_dir), check=False)
        svn_commit(wc_dir, commit_msg, svn_user, svn_pass)

    print("Sync and commit completed successfully.")

if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(1)
