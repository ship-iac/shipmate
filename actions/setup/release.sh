# Sourced by the setup steps that download or verify a release file. A step sources it after
# `set -euo pipefail`, then calls `release_init <error title> <message prefix>`.

_release_title="shipmate setup failed"
_release_prefix=""

# Set the `::error::` title and the message prefix that `fail` and the EXIT handler print.
release_init() {
  _release_title="$1"
  _release_prefix="$2"
}

# Print one `::error::`, write it as the step's `refusal` output and exit 1. The output is cut
# at `REASON_CAP` (scripts/_shipmate.py), like run_annotated's.
fail() {
  local msg="${_release_prefix}$1"
  echo "::error title=${_release_title}::${msg}"
  printf 'refusal=%s\n' "${msg:0:4000}" >> "$GITHUB_OUTPUT"
  exit 1
}

# Builtins only: a refusal for a missing tool must not depend on that tool.
_release_has_refusal() {
  local line
  [ -f "$GITHUB_OUTPUT" ] || return 1
  while IFS= read -r line || [ -n "$line" ]; do
    [[ "$line" == refusal=* ]] && return 0
  done < "$GITHUB_OUTPUT"
  return 1
}

# Any nonzero exit that `fail` did not explain still writes a refusal. It reads the output file,
# not a variable, because a `fail` in a subshell sets no variable here. No ERR trap: under
# `set -e` it does not fire inside a function that a condition calls.
_release_exit() {
  local rc=$?
  if [ "$rc" -ne 0 ] && ! _release_has_refusal; then
    local msg="${_release_prefix}a command exited ${rc} under \"${_release_title}\"; the step log names it."
    echo "::error title=${_release_title}::${msg}"
    printf 'refusal=%s\n' "${msg:0:4000}" >> "$GITHUB_OUTPUT"
  fi
  exit "$rc"
}
trap _release_exit EXIT

# Refuse unless each runner prerequisite is installed: the four every download and verify needs,
# then any extras the caller names.
require() {
  local tool
  for tool in curl openssl base64 sha256sum "$@"; do
    command -v "$tool" >/dev/null || fail "${tool} is not installed on the runner."
  done
}

# fetch <url> <dest> <404 remedy> <other remedy>: download <url> to <dest>, else refuse naming
# the status, curl's exit and the URL that answered last (query stripped). curl retries network
# errors, 408/429/5xx and stalls (<1 KiB/s for 30 s), and starts no retry after 120 s; each
# attempt stops at 600 s, an 80 MB archive at 133 KB/s. --retry-all-errors needs curl 7.71+.
fetch() {
  local url="$1" dest="$2" rc=0 out code eff remedy
  out="$(curl --retry-all-errors -sSL --retry 10 --retry-max-time 120 --connect-timeout 10 \
    --max-time 600 --speed-limit 1024 --speed-time 30 \
    -o "$dest" -w '%{http_code} %{url_effective}' "$url")" || rc=$?
  read -r code eff <<<"$out"
  code="${code:-000}"
  if [ "$rc" -eq 0 ] && [ "$code" = 200 ]; then
    return 0
  fi
  if [ "$rc" -eq 2 ]; then
    fail "curl exited 2 on an option it does not know; shipmate needs curl 7.71 or later (CONTRACT.md §Runner prerequisites)."
  fi
  if [ "$code" = 404 ]; then remedy="$3"; else remedy="$4"; fi
  fail "${url} answered HTTP ${code} from ${eff%%\?*} (curl exit ${rc})${remedy:+; ${remedy}}."
}
