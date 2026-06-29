#!/usr/bin/env bash
set -euo pipefail

APP_NAME="hmm"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DIST_DIR="$ROOT_DIR/dist"
PACKAGE_DIR="$DIST_DIR/package"
APP_BUNDLE="$PACKAGE_DIR/$APP_NAME.app"
SIGNED_ARCHIVE="$DIST_DIR/$APP_NAME-macos-signed.zip"

find_default_identity() {
  security find-identity -p codesigning -v \
    | sed -n 's/.*"\(Developer ID Application: .*\)"/\1/p' \
    | head -n 1
}

SIGNING_IDENTITY="${SIGNING_IDENTITY:-$(find_default_identity)}"
if [[ -z "$SIGNING_IDENTITY" ]]; then
  cat >&2 <<'EOF'
No Developer ID Application signing identity was found.

Install a Developer ID Application certificate in your login keychain, or set:
  SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)"
EOF
  exit 1
fi

"$ROOT_DIR/script/package_unsigned.sh" >/dev/null

if [[ ! -d "$APP_BUNDLE" ]]; then
  echo "app bundle not found: $APP_BUNDLE" >&2
  exit 1
fi

sign_args=(--force --timestamp --options runtime --sign "$SIGNING_IDENTITY")
if [[ -n "${HMM_MACOS_ENTITLEMENTS:-}" ]]; then
  sign_args+=(--entitlements "$HMM_MACOS_ENTITLEMENTS")
fi

codesign "${sign_args[@]}" "$APP_BUNDLE"
codesign --verify --strict --verbose=4 "$APP_BUNDLE"

rm -f "$SIGNED_ARCHIVE"
(
  cd "$PACKAGE_DIR"
  ditto -c -k --keepParent "$APP_NAME.app" "$SIGNED_ARCHIVE"
)

echo "$SIGNED_ARCHIVE"
