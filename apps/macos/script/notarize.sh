#!/usr/bin/env bash
set -euo pipefail

APP_NAME="oida"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
PACKAGE_DIR="$DIST_DIR/package"
APP_BUNDLE="$PACKAGE_DIR/$APP_NAME.app"
SIGNED_ARCHIVE="$DIST_DIR/$APP_NAME-macos-signed.zip"
NOTARIZED_ARCHIVE="$DIST_DIR/$APP_NAME-macos-notarized.zip"
NOTARY_PROFILE="${NOTARY_PROFILE:-oida-notary}"
NOTARY_TIMEOUT="${NOTARY_TIMEOUT:-30m}"

if [[ ! -f "$SIGNED_ARCHIVE" ]]; then
  "$ROOT_DIR/script/package_signed.sh" >/dev/null
fi

if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "app bundle not found: $APP_BUNDLE" >&2
  exit 1
fi

codesign --verify --strict --verbose=4 "$APP_BUNDLE"

auth_args=()
if [[ -n "${NOTARY_KEY:-}" && -n "${NOTARY_KEY_ID:-}" ]]; then
  auth_args+=(--key "$NOTARY_KEY" --key-id "$NOTARY_KEY_ID")
  if [[ -n "${NOTARY_ISSUER:-}" ]]; then
    auth_args+=(--issuer "$NOTARY_ISSUER")
  fi
else
  auth_args+=(--keychain-profile "$NOTARY_PROFILE")
fi

xcrun notarytool submit "$SIGNED_ARCHIVE" "${auth_args[@]}" --wait --timeout "$NOTARY_TIMEOUT"
xcrun stapler staple "$APP_BUNDLE"
xcrun stapler validate "$APP_BUNDLE"
spctl -a -vv -t execute "$APP_BUNDLE"

rm -f "$NOTARIZED_ARCHIVE"
(
  cd "$PACKAGE_DIR"
  ditto -c -k --keepParent "$APP_NAME.app" "$NOTARIZED_ARCHIVE"
)

echo "$NOTARIZED_ARCHIVE"
