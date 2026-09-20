"""Test the --profile argument handling in setup.sh in isolation.

setup.sh itself needs Docker/pip/network to run end-to-end, so these tests
extract just the self-contained pieces this PR touches (profile-name
parsing/validation, HERMES_HOME export, per-profile cron marker) and run
them under bash directly, without executing the rest of the installer.
"""
import os
import re
import subprocess
import sys

SETUP_SH = os.path.join(os.path.dirname(__file__), "setup.sh")

all_ok = True


def check(name, cond):
    global all_ok
    if not cond:
        print(f"FAIL: {name}")
        all_ok = False


def extract(text, start_marker, end_marker):
    start = text.index(start_marker)
    end = text.index(end_marker, start)
    return text[start:end]


with open(SETUP_SH) as f:
    SCRIPT = f.read()

PROFILE_BLOCK = extract(SCRIPT, 'PROFILE_NAME=""', 'VAULT_PATH="${VAULT_PATH:-${HERMES_HOME}/vault}"')
CRON_MARKER_BLOCK = extract(SCRIPT, "CRON_ENTRY=", '\n\nif crontab -l')

check("extracted profile block looks right",
      "export HERMES_HOME" in PROFILE_BLOCK and "PROFILE_NAME" in PROFILE_BLOCK)
check("extracted cron marker block looks right",
      "CRON_MARKER=" in CRON_MARKER_BLOCK)


def run_profile_block(args, extra_prelude=""):
    """Run the profile-parsing block with $@ set to `args`; return (rc, stdout, stderr)."""
    script = f"""#!/usr/bin/env bash
set -euo pipefail
FAIL=0
fail() {{ printf "FAIL_CALL: %s\\n" "$1" >&2; FAIL=$((FAIL + 1)); }}
HOME="{os.environ.get('HOME', '/home/testuser')}"
{extra_prelude}
{PROFILE_BLOCK}
echo "PROFILE_NAME=${{PROFILE_NAME}}"
echo "HERMES_HOME=${{HERMES_HOME}}"
python3 -c 'import os; print("HERMES_HOME_EXPORTED=" + os.environ.get("HERMES_HOME", "<unset>"))'
"""
    proc = subprocess.run(
        ["bash", "-c", script, "--"] + args,
        capture_output=True, text=True,
    )
    return proc.returncode, proc.stdout, proc.stderr


# ── No --profile: default HERMES_HOME, still exported ────────────────────

rc, out, err = run_profile_block([])
check("no --profile: exits 0", rc == 0)
check("no --profile: HERMES_HOME defaults to $HOME/.hermes",
      re.search(r"^HERMES_HOME=.*/\.hermes$", out, re.M) is not None)
check("no --profile: HERMES_HOME is exported to subprocesses",
      "HERMES_HOME_EXPORTED=<unset>" not in out)

# ── --profile <name> (space form) ─────────────────────────────────────────

rc, out, err = run_profile_block(["--profile", "coder"])
check("--profile coder: exits 0", rc == 0)
check("--profile coder: PROFILE_NAME parsed", "PROFILE_NAME=coder" in out)
check("--profile coder: HERMES_HOME under profiles/coder",
      re.search(r"^HERMES_HOME=.*/\.hermes/profiles/coder$", out, re.M) is not None)
check("--profile coder: HERMES_HOME exported to subprocess (regression: setup_db.py bug)",
      "HERMES_HOME_EXPORTED=<unset>" not in out
      and re.search(r"HERMES_HOME_EXPORTED=.*/profiles/coder$", out, re.M) is not None)

# ── --profile=<name> (equals form) ────────────────────────────────────────

rc, out, err = run_profile_block(["--profile=reviewer"])
check("--profile=reviewer: exits 0", rc == 0)
check("--profile=reviewer: PROFILE_NAME parsed", "PROFILE_NAME=reviewer" in out)
check("--profile=reviewer: HERMES_HOME under profiles/reviewer",
      re.search(r"^HERMES_HOME=.*/\.hermes/profiles/reviewer$", out, re.M) is not None)

# ── Invalid profile names are rejected (path-traversal hardening) ────────

for args in (["--profile"], ["--profile="], ["--profile", ""]):
    rc, out, err = run_profile_block(args)
    check(f"missing profile name rejected: {args!r}", rc != 0)

for bad in ["../../etc", "a/b", "a b", "coder;rm -rf /"]:
    rc, out, err = run_profile_block(["--profile", bad])
    check(f"invalid profile name rejected: {bad!r}", rc != 0 and "FAIL_CALL:" in err)

# ── Valid charset (letters, digits, _, -) is accepted ─────────────────────

rc, out, err = run_profile_block(["--profile", "my-agent_2"])
check("valid profile name with _ and - accepted", rc == 0 and "PROFILE_NAME=my-agent_2" in out)


# ── Cron marker is per-profile (regression: second profile install being
#    silently skipped because it matched the first profile's marker) ──────

def run_cron_marker(profile_name):
    script = f"""#!/usr/bin/env bash
set -euo pipefail
PROFILE_NAME="{profile_name}"
REPO_DIR="/tmp/repo"
HERMES_HOME="/tmp/hh"
{CRON_MARKER_BLOCK}
echo "CRON_MARKER=${{CRON_MARKER}}"
"""
    proc = subprocess.run(["bash", "-c", script], capture_output=True, text=True)
    check(f"cron marker block runs cleanly for profile={profile_name!r}", proc.returncode == 0)
    m = re.search(r"^CRON_MARKER=(.*)$", proc.stdout, re.M)
    return m.group(1) if m else None


default_marker = run_cron_marker("")
coder_marker = run_cron_marker("coder")
reviewer_marker = run_cron_marker("reviewer")

check("default install marker unchanged (upgrade compat)",
      default_marker == "# memory-os wiki watcher")
check("profile markers differ from the default marker",
      coder_marker not in (None, default_marker) and reviewer_marker not in (None, default_marker))
check("two different profiles get two different markers",
      coder_marker != reviewer_marker)

# ── Phase 1 bootstrap: a copied tree (no .git) must install ─────────────────
# Regression: the bootstrap used to key only on `.git`, so a tree extracted from
# an archive (GitHub "Download ZIP", a release tarball) fell through to
# `git clone` over a non-empty directory and aborted with
# `fatal: destination path ... already exists` (exit 128 under pipefail).
import tempfile
from pathlib import Path

BOOTSTRAP_BLOCK = extract(
    SCRIPT,
    'if [ -d "${REPO_DIR}/.git" ]',
    'banner "Phase 2: Pre-flight Checks"',
)


def run_bootstrap(repo_dir):
    """Run the Phase 1 bootstrap block with a stubbed git (never hits network)."""
    script = f"""#!/usr/bin/env bash
set -euo pipefail
PASS=0; FAIL=0
ok()   {{ printf "OK: %s\\n" "$1"; PASS=$((PASS + 1)); }}
fail() {{ printf "FAIL: %s\\n" "$1"; FAIL=$((FAIL + 1)); }}
info() {{ printf "INFO: %s\\n" "$1"; }}
git()  {{ printf "GIT_CALLED: %s\\n" "$*"; mkdir -p "$REPO_DIR"; }}
REPO_URL="https://example.invalid/memory-os.git"
REPO_DIR="{repo_dir}"
{BOOTSTRAP_BLOCK}
printf "REACHED_END\\n"
"""
    return subprocess.run(["bash", "-c", script], capture_output=True, text=True)


def make_tree(root, *, git=False, checkout=False, junk=False):
    p = Path(root)
    p.mkdir(parents=True, exist_ok=True)
    if git:
        (p / ".git").mkdir()
    if checkout:
        (p / "docker").mkdir()
        (p / "docker" / "docker-compose.yml").write_text("services: {}\n")
        (p / "setup.sh").write_text("#!/usr/bin/env bash\n")
    if junk:
        (p / "random.txt").write_text("not a checkout\n")
    return str(p)


with tempfile.TemporaryDirectory(prefix="memory-os-bootstrap-") as tmp:
    clone_case = make_tree(str(Path(tmp) / "clone"), git=True)
    r = run_bootstrap(clone_case)
    check("bootstrap: existing clone is reused, no git call",
          "Repo already exists" in r.stdout and "GIT_CALLED" not in r.stdout)

    copied_case = make_tree(str(Path(tmp) / "copied"), checkout=True)
    r = run_bootstrap(copied_case)
    check("bootstrap: copied tree without .git is accepted",
          "Using existing checkout" in r.stdout and "GIT_CALLED" not in r.stdout
          and r.returncode == 0)

    junk_case = make_tree(str(Path(tmp) / "junk"), junk=True)
    r = run_bootstrap(junk_case)
    check("bootstrap: non-checkout directory fails with a clear message",
          r.returncode != 0 and "is not a Memory OS checkout" in r.stdout
          and "GIT_CALLED" not in r.stdout)

    missing_case = str(Path(tmp) / "does-not-exist")
    r = run_bootstrap(missing_case)
    check("bootstrap: absent directory still clones",
          "GIT_CALLED" in r.stdout and "REACHED_END" in r.stdout)

if all_ok:
    print("=== ALL SETUP.SH PROFILE TESTS PASS ===")
    sys.exit(0)
else:
    print("=== SETUP.SH PROFILE TESTS FAILED ===")
    sys.exit(1)
