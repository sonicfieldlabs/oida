# macOS Signing and Notarization

This repo can build a local unsigned app without Apple credentials. Public
distribution outside the Mac App Store needs a Developer ID Application
certificate, hardened runtime signing, notarization, and ticket stapling.

## Current Local State

The inspected local artifact is:

```text
apps/macos/dist/hmm.app
```

Current signing state on this machine:

- `codesign -dvvv --entitlements :- apps/macos/dist/hmm.app` reports an ad hoc
  signature.
- `security find-identity -p codesigning -v` reports `0 valid identities found`.
- `spctl -a -vv apps/macos/dist/hmm.app` fails before notarization because the
  artifact is not Developer ID signed.

This is expected for the unsigned development archive.

## What You Need From Apple

1. Join the Apple Developer Program if the account is not already enrolled.
2. Use the Account Holder role to create a Developer ID Application
   certificate. Apple also offers Developer ID Installer certificates, but this
   repo ships a zipped `.app`, so the Application certificate is the required
   first credential.
3. Install the downloaded `.cer` in Keychain Access. It must appear under
   `My Certificates` with its private key.
4. Create notarization credentials:
   - Recommended local path: store credentials in Keychain with `notarytool`.
   - CI path: use an App Store Connect API key file (`.p8`), key id, and issuer
     id as CI secrets. Individual API keys omit issuer id.

## Store Notary Credentials Locally

Use a Keychain profile named `hmm-notary`:

```bash
xcrun notarytool store-credentials hmm-notary \
  --apple-id "you@example.com" \
  --team-id "TEAMID"
```

`notarytool` will prompt for an app-specific password if you do not pass
`--password`.

For API-key based authentication:

```bash
xcrun notarytool store-credentials hmm-notary \
  --key /secure/path/AuthKey_KEYID.p8 \
  --key-id KEYID \
  --issuer ISSUER-UUID
```

For Individual API Keys, omit `--issuer`.

## Build a Signed Archive

After installing the Developer ID Application certificate:

```bash
apps/macos/script/package_signed.sh
```

The script auto-detects the first `Developer ID Application:` identity. To pin a
specific identity:

```bash
SIGNING_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
  apps/macos/script/package_signed.sh
```

Output:

```text
apps/macos/dist/hmm-macos-signed.zip
```

The script signs with:

- `--options runtime`
- `--timestamp`
- Developer ID Application identity

No App Sandbox entitlement is applied. This app launches a local daemon and uses
ScreenCaptureKit/system audio APIs; sandboxing should be reviewed as a separate
product decision, not added casually for Developer ID distribution.

## Notarize and Staple

With a stored Keychain profile:

```bash
apps/macos/script/notarize.sh
```

With direct API key environment variables:

```bash
NOTARY_KEY=/secure/path/AuthKey_KEYID.p8 \
NOTARY_KEY_ID=KEYID \
NOTARY_ISSUER=ISSUER-UUID \
  apps/macos/script/notarize.sh
```

For Individual API Keys, leave `NOTARY_ISSUER` unset.

Output:

```text
apps/macos/dist/hmm-macos-notarized.zip
```

The script submits the signed zip with `xcrun notarytool submit --wait`, staples
the ticket to `hmm.app`, validates the staple, runs Gatekeeper assessment with
`spctl`, and creates a final zip from the stapled app.

## Validation Commands

```bash
security find-identity -p codesigning -v
codesign -dvvv --entitlements :- apps/macos/dist/package/hmm.app
codesign --verify --strict --verbose=4 apps/macos/dist/package/hmm.app
xcrun stapler validate apps/macos/dist/package/hmm.app
spctl -a -vv -t execute apps/macos/dist/package/hmm.app
```

## CI Notes

CI should not store a Developer ID private key unless release automation is
explicitly required. The current CI workflow builds and packages unsigned
artifacts only. To add signed CI releases later, store these as repository or
environment secrets:

- `MACOS_CERTIFICATE_P12_BASE64`
- `MACOS_CERTIFICATE_PASSWORD`
- `APPLE_NOTARY_KEY_P8_BASE64`
- `APPLE_NOTARY_KEY_ID`
- `APPLE_NOTARY_ISSUER_ID` for Team API Keys

Create a temporary keychain inside the release job, import the certificate,
run `apps/macos/script/package_signed.sh`, then run
`apps/macos/script/notarize.sh`.

## References

- Apple Developer Program: https://developer.apple.com/programs/
- Developer ID certificates: https://developer.apple.com/help/account/certificates/create-developer-id-certificates/
- Create a private key: https://developer.apple.com/help/account/keys/create-a-private-key
- Notarizing macOS software: https://developer.apple.com/documentation/security/notarizing-macos-software-before-distribution
- Customizing notarization workflow: https://developer.apple.com/documentation/security/customizing-the-notarization-workflow
