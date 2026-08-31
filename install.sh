#!/usr/bin/env bash
# collab bootstrap — creates a local .venv and installs collab into it.
# Never touches the system Python, never uses sudo.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="$ROOT/.venv"
MIN_MINOR=10

c_red=$'\033[31m'; c_grn=$'\033[32m'; c_ylw=$'\033[33m'; c_dim=$'\033[2m'; c_off=$'\033[0m'
if [ -n "${NO_COLOR:-}" ] || [ ! -t 1 ]; then c_red=; c_grn=; c_ylw=; c_dim=; c_off=; fi
ok()   { printf '%s[ok]%s   %s\n' "$c_grn" "$c_off" "$*"; }
warn() { printf '%s[warn]%s %s\n' "$c_ylw" "$c_off" "$*"; }
die()  { printf '%s[fail]%s %s\n' "$c_red" "$c_off" "$*" >&2; exit 1; }

# --- 1. find an interpreter >= 3.10 -----------------------------------------
version_ok() {
    "$1" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, '"$MIN_MINOR"') else 1)' 2>/dev/null
}

PY=""
for cand in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$cand" >/dev/null 2>&1 && version_ok "$cand"; then
        PY="$(command -v "$cand")"; break
    fi
done

# Fall back to pyenv before giving up.
if [ -z "$PY" ] && command -v pyenv >/dev/null 2>&1; then
    warn "no system python >= 3.$MIN_MINOR found; trying pyenv"
    cand="$(pyenv which python3 2>/dev/null || true)"
    if [ -n "$cand" ] && version_ok "$cand"; then
        PY="$cand"
    else
        printf '\n%s\n' "pyenv is installed but has no Python >= 3.$MIN_MINOR selected. Run:"
        printf '  %spyenv install 3.12 && pyenv local 3.12%s\n' "$c_dim" "$c_off"
        printf '%s\n' "then re-run ./install.sh"
        exit 1
    fi
fi

if [ -z "$PY" ]; then
    cat >&2 <<MSG

${c_red}No Python >= 3.$MIN_MINOR found.${c_off} collab needs it (the a2a-sdk requires it).

Install one of the following, then re-run ./install.sh:

  Debian/Ubuntu   sudo apt install python3.12 python3.12-venv
  Fedora          sudo dnf install python3.12
  macOS           brew install python@3.12
  Any platform    curl https://pyenv.run | bash && pyenv install 3.12 && pyenv local 3.12

MSG
    exit 1
fi

PY_VER="$("$PY" -c 'import sys; print("%d.%d.%d" % sys.version_info[:3])')"
ok "python $PY_VER  ${c_dim}($PY)${c_off}"

# --- 2. create the venv ------------------------------------------------------
if [ -d "$VENV" ] && [ ! -x "$VENV/bin/python" ]; then
    die ".venv exists but looks broken (no bin/python). Remove it and re-run: rm -rf '$VENV'"
fi

if [ ! -d "$VENV" ]; then
    "$PY" -m venv "$VENV" 2>/dev/null || die \
"Failed to create a venv. On Debian/Ubuntu you may need:  sudo apt install python3-venv"
    ok ".venv created"
else
    ok ".venv already present  ${c_dim}(reusing)${c_off}"
fi

# --- 3. install ---------------------------------------------------------------
"$VENV/bin/python" -m pip install --quiet --upgrade pip >/dev/null
printf '%s[..]%s   installing collab and dependencies\n' "$c_dim" "$c_off"
"$VENV/bin/python" -m pip install --quiet -e "$ROOT[dev]" || die "pip install failed (see output above)"
ok "collab installed into .venv"

# --- 4. optional tooling ------------------------------------------------------
if command -v ngrok >/dev/null 2>&1; then
    ok "ngrok detected  ${c_dim}($(ngrok version 2>/dev/null | head -1))${c_off} — sessions can be shared publicly"
else
    warn "ngrok not found — sessions will be local-only until you install it or use another tunnel"
    printf '       %shttps://ngrok.com/download   (alternatives: cloudflared, tailscale funnel)%s\n' "$c_dim" "$c_off"
fi

# --- 5. done ------------------------------------------------------------------
cat <<MSG

${c_grn}Done.${c_off} Everything lives in .venv — collab is not installed globally.

  Run it directly:      ${c_dim}.venv/bin/collab --help${c_off}
  Or activate first:    ${c_dim}source .venv/bin/activate && collab --help${c_off}

Start a session and get a link to share:
  ${c_dim}.venv/bin/collab host${c_off}

Join someone else's:
  ${c_dim}.venv/bin/collab join <url>#<invite>${c_off}
MSG
