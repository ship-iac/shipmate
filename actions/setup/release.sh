# Sourced by the setup steps that download or verify a release file, and by build-matrix's
# `Verify changed tools`. A step sources it after
# `set -euo pipefail`, then calls `release_init <error title> <message prefix>`.

_release_title="shipmate setup failed"
_release_prefix=""
_release_gnupghome=""

# Set the `::error::` title and the message prefix that `fail` and the EXIT handler print.
release_init() {
  _release_title="$1"
  _release_prefix="$2"
}

# Print <message> as one `::error::` and write it as the step's `refusal` output, its lines joined
# into one, cut at `REASON_CAP` (scripts/_shipmate.py) like run_annotated's. In a subshell the
# annotation goes to stderr, which the runner also reads, so a command substitution cannot capture it.
_release_refuse() {
  local msg="${1//$'\n'/ }"
  if [ "$BASHPID" = "$$" ]; then
    echo "::error title=${_release_title}::${msg}"
  else
    echo "::error title=${_release_title}::${msg}" >&2
  fi
  printf 'refusal=%s\n' "${msg:0:4000}" >> "$GITHUB_OUTPUT"
}

# Refuse with the prefix and <reason>, then exit 1.
fail() {
  _release_refuse "${_release_prefix}$1"
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
    _release_refuse "${_release_prefix}a command exited ${rc} under \"${_release_title}\"; the step log names it."
  fi
  # Only verify_tofu's own keyring: never an operator's GNUPGHOME, and never the exit status.
  # Kill before removing the keyring the kill is keyed on.
  if [ -n "$_release_gnupghome" ]; then
    GNUPGHOME="$_release_gnupghome" gpgconf --kill gpg-agent || true
    rm -rf "$_release_gnupghome" || true
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

# fetch <url> <dest> <404 remedy> <other remedy>: download, else refuse naming the status, curl's
# exit and the answering URL (query stripped). No retry starts after 120 s; an attempt stops at
# 600 s, an 80 MB archive at 133 KB/s. --retry-all-errors needs curl 7.71+.
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

# A restored root that is a symlink or not a directory is removed, so its files download again.
release_dir() {
  if [ -L "$1" ] || [ ! -d "$1" ]; then rm -rf "${1:?}"; fi
  mkdir -p "$1"
}

# release_sums <tool> <version>, <tool> terramate or tofu: set `release_base` to the release's
# download URL and `release_files` to its checksum file and that file's signature.
release_sums() {
  if [ "$1" = terramate ]; then
    release_base="https://github.com/terramate-io/terramate/releases/download/v$2"
    release_files=(checksums.txt checksums.txt.sig)
  else
    release_base="https://github.com/opentofu/opentofu/releases/download/v$2"
    release_files=("tofu_$2_SHA256SUMS" "tofu_$2_SHA256SUMS.gpgsig")
  fi
}

# release_verify <tool> <version> <dir> <404 remedy> <other remedy>: replace the release's
# checksum file and signature in <dir> with fresh downloads and verify them, else refuse through
# `fail`. `{file}` in a remedy names the file. Call it as a statement: left of `||` or in an `if`
# condition, `set -e` would not stop it at a failed `rm`.
release_verify() {
  local f
  release_sums "$1" "$2"
  for f in "${release_files[@]}"; do
    rm -rf "${3:?}/${f:?}"
    fetch "$release_base/$f" "$3/$f" "${4//\{file\}/$f}" "${5//\{file\}/$f}"
  done
  "verify_$1" "$2" "$3" || fail "$reason"
}

# Return 1 with `reason` set unless each named file in <dir> is a regular file, not a symlink.
# shellcheck disable=SC2034 # The calling step reads `reason`.
_release_files() {
  local dir="$1" f
  shift
  for f in "$@"; do
    if [ ! -f "$dir/$f" ] || [ -L "$dir/$f" ]; then
      reason="$dir/$f is not a regular file."
      return 1
    fi
  done
}

# verify_terramate <version> <dir> and verify_tofu <version> <dir> check the release's checksum
# file and signature already in <dir> against the committed key. On success they write the Linux
# digests to the step's output; otherwise they return 1 with `reason` set. The caller sets the
# title and prefix of a refusal with `release_init`; release_verify downloads.
# shellcheck disable=SC2034 # The calling step reads `reason`.
verify_terramate() {
  local v="$1" dir="$2" rc=0 arch asset line lines=""
  # Outside <dir>, which the cache saves whole.
  local der="$RUNNER_TEMP/terramate-checksums.der"
  release_sums terramate "$v"
  _release_files "$dir" "${release_files[@]}" || return 1
  # cosign writes the signature base64-encoded; openssl reads it as DER.
  base64 -d "$dir/checksums.txt.sig" > "$der" || { reason="${release_base}/checksums.txt.sig is not base64."; return 1; }
  openssl dgst -sha256 -verify "$GITHUB_ACTION_PATH/../../keys/terramate-cosign.pub" \
    -signature "$der" "$dir/checksums.txt" >/dev/null || rc=$?
  if [ "$rc" -ne 0 ]; then
    reason="the signature on checksums.txt did not verify against the committed Terramate key (openssl exit ${rc})."
    return 1
  fi
  for arch in x86_64 arm64; do
    asset="terramate_${v}_linux_${arch}.tar.gz"
    line="$(awk -v a="$asset" '$2 == a {print $1 "  " $2}' "$dir/checksums.txt")"
    [ -n "$line" ] || { reason="checksums.txt has no line for ${asset}."; return 1; }
    lines+="${line}"$'\n'
  done
  printf 'terramate_sha256<<SHIPMATE_EOF\n%sSHIPMATE_EOF\n' "$lines" >> "$GITHUB_OUTPUT"
}

# shellcheck disable=SC2034 # The calling step reads `reason`.
verify_tofu() {
  local v="$1" dir="$2" rc=0 status got arch asset line lines="" sums
  # The primary key fingerprint of OpenTofu's release signer, compared whole.
  local signer=E3E6E43D84CB852EADB0051D0C0AF313E5FD9F80
  release_sums tofu "$v"
  sums="${release_files[0]}"
  # One keyring per step: a second call must not start a second agent the EXIT handler misses.
  if [ -z "$_release_gnupghome" ]; then
    _release_gnupghome="$(mktemp -d)" || fail "mktemp could not create a gpg keyring directory."
    GNUPGHOME="$_release_gnupghome" gpg --batch --import "$GITHUB_ACTION_PATH/../../keys/opentofu.asc" \
      || fail "gpg could not import the committed OpenTofu key."
  fi
  _release_files "$dir" "${release_files[@]}" || return 1
  status="$(GNUPGHOME="$_release_gnupghome" gpg --batch --status-fd 1 --verify "$dir/$sums.gpgsig" "$dir/$sums")" || rc=$?
  if [ "$rc" -ne 0 ]; then
    reason="the signature on ${sums} did not verify against the committed OpenTofu key (gpg exit ${rc})."
    return 1
  fi
  got="$(awk '$1 == "[GNUPG:]" && $2 == "VALIDSIG" {print $NF}' <<<"$status")"
  [ "$got" = "$signer" ] || { reason="${sums} is signed by ${got:-no key}, not the OpenTofu signer ${signer}."; return 1; }
  for arch in amd64 arm64; do
    asset="tofu_${v}_linux_${arch}.zip"
    line="$(awk -v a="$asset" '$2 == a {print $1 "  " $2}' "$dir/$sums")"
    [ -n "$line" ] || { reason="${sums} has no line for ${asset}."; return 1; }
    lines+="${line}"$'\n'
  done
  printf 'tofu_sha256<<SHIPMATE_EOF\n%sSHIPMATE_EOF\n' "$lines" >> "$GITHUB_OUTPUT"
}
