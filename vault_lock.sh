#!/bin/bash
# ============================================================================
# vault_lock.sh — encrypt the concierge vault at rest (gpg symmetric).
#
# WHY:
#   vault.json holds all API keys as PLAINTEXT on disk (world could read at
#   644). Permissions lockdown helps, but true at-rest protection means the
#   file stays encrypted and only vault-env decrypts it in memory for the
#   current shell. This is the second layer: keep a gpg-encrypted canonical
#   backup + optionally swap the live file for the encrypted one.
#
# DESIGN:
#   - v{,a}[password-file] not used: prompts for a passphrase (UMASK-style).
#   - Writes vault.json.gpg (symmetric, AES256 by default in gpg2).
#   - NEVER prints key material; only status.
#
# USAGE:
#   ~/bin/vault_lock.sh lock      # create/refresh vault.json.gpg
#   ~/bin/vault_lock.sh unlock    # decrypt vault.json.gpg back to vault.json
#   ~/bin/vault_lock.sh verify    # decrypt to stdout-check only (no write)
# ============================================================================
set -euo pipefail

VAULT="$HOME/.concierge/vault.json"
GPG="$HOME/.concierge/vault.json.gpg"
# keychain passphrase file (0600, separate from the vault). Both files are
# needed to recover keys — the vault alone is unreadable without it.
KEYCHAIN="$HOME/.concierge/.vault_keychain"

_ensure_keychain() { # create a fresh random passphrase (0600) if absent
  if [ ! -s "$KEYCHAIN" ]; then
    python3 -c "import secrets;print(secrets.token_urlsafe(32))" > "$KEYCHAIN"
    chmod 600 "$KEYCHAIN"
    echo "[vault_lock] created keychain $KEYCHAIN (0600)"
  fi
}

lock() {
  _ensure_keychain
  gpg --batch --yes --pinentry-mode loopback --passphrase-file "$KEYCHAIN" \
    --symmetric --output "$GPG" "$VAULT" && {
    chmod 600 "$GPG"
    echo "[vault_lock] wrote $GPG (encrypted at rest)"
  }
}

unlock() {
  [ -s "$KEYCHAIN" ] || { echo "no keychain; cannot decrypt"; exit 1; }
  gpg --batch --yes --pinentry-mode loopback --passphrase-file "$KEYCHAIN" \
    --decrypt --output "$VAULT" "$GPG" && {
    chmod 600 "$VAULT"
    echo "[vault_lock] restored $VAULT from $GPG"
  }
}

verify() {
  [ -s "$KEYCHAIN" ] || { echo "no keychain; cannot decrypt"; exit 1; }
  gpg --batch --yes --pinentry-mode loopback --passphrase-file "$KEYCHAIN" \
    --decrypt "$GPG" | python3 -c \
    "import sys,json;json.load(sys.stdin);print('[vault_lock] decrypt verified: valid JSON round-trip')"
}

case "${1:-}" in
  lock)   lock ;;
  unlock) unlock ;;
  verify) verify ;;
  *) echo "usage: $0 {lock|unlock|verify}"; exit 1 ;;
esac