# Release Builds

GitHub Actions builds the macOS and Windows applications whenever a `v*` tag is pushed.

## Artifacts

- `ai-memory-macos.zip`: signed and notarized macOS application bundle.
- `ai-memory-windows.zip`: standalone Windows executable.

Both packages include the pinned rclone helper and its license. Users do not need to install Python, Node.js, or rclone.

## Required GitHub Secrets

Configure these repository Actions secrets before publishing a release:

| Secret | Value |
| --- | --- |
| `APPLE_CERTIFICATE_P12_BASE64` | Developer ID Application certificate and private key exported as PKCS#12, then Base64 encoded |
| `APPLE_CERTIFICATE_PASSWORD` | Password used when exporting the PKCS#12 file |
| `APPLE_TEAM_ID` | Apple Developer Team ID |
| `APPLE_ID` | Apple ID used for notarization |
| `APPLE_APP_PASSWORD` | App-specific password created for the Apple ID |

Do not commit the `.p12` file, its password, or Apple credentials to the repository.

## macOS Signing Requirements

The PKCS#12 archive must contain a valid **Developer ID Application** certificate and its matching private key. An iOS Distribution or Apple Development certificate cannot sign a macOS application distributed outside the Mac App Store.

The release workflow:

1. creates a temporary keychain on the GitHub runner;
2. imports the Developer ID identity and Apple G2 intermediate certificate;
3. builds the application and bundled helper executables;
4. signs every Mach-O binary with the hardened runtime and a secure timestamp;
5. verifies the complete application signature;
6. submits the ZIP to Apple's notary service;
7. staples and validates the notarization ticket;
8. verifies Gatekeeper acceptance;
9. publishes both platform archives to GitHub Releases.

## Publish a Version

Update the version in:

- `pyproject.toml`;
- `src/aimemory/__init__.py`;
- `src/aimemory/assets/desktop.html`.

Run the tests, commit the version, then create and push the matching tag:

```bash
pytest -q
npm test
git tag -a vX.Y.Z -m "AI Memory vX.Y.Z"
git push origin main
git push origin vX.Y.Z
```

The workflow publishes the release only after the macOS and Windows jobs both succeed.

## Verify a Downloaded macOS Release

After extracting the published ZIP:

```bash
codesign --verify --deep --strict --verbose=2 "AI Memory.app"
xcrun stapler validate "AI Memory.app"
spctl --assess --type execute --verbose=2 "AI Memory.app"
```

The final command should report `accepted` with `source=Notarized Developer ID`.
