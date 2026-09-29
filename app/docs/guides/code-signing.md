# Code Signing & Notarization Setup

AutoApply supports code signing for Windows and macOS installers. When configured,
installers are automatically signed during CI release builds, eliminating platform
security warnings for end users.

**Signing is optional.** Builds work without certificates — they just produce unsigned
installers.

---

## Windows Code Signing

### Prerequisites
- A code signing certificate (.pfx file) from a trusted CA
  (e.g., DigiCert, Sectigo, GlobalSign)
- Standard OV (Organization Validation) certificates work. EV certificates provide
  immediate SmartScreen reputation but require hardware tokens.

### Setup

1. **Base64-encode your .pfx certificate:**
   ```bash
   base64 -i certificate.pfx -o certificate-base64.txt
   ```

2. **Add GitHub Secrets** (Settings → Secrets and variables → Actions):

   | Secret | Value |
   |--------|-------|
   | `WIN_CSC_LINK` | Contents of `certificate-base64.txt` |
   | `WIN_CSC_KEY_PASSWORD` | Password for the .pfx file |

3. **Push a version tag** (`v*`) to trigger a signed release build.

### Result
- The .exe installer will show your organization name instead of "Unknown publisher"
- SmartScreen reputation builds over time with OV certificates

---

## macOS Code Signing & Notarization

### Prerequisites
- Apple Developer Program membership ($99/year)
- "Developer ID Application" certificate from Apple Developer portal
- App-specific password for notarization

### Setup

1. **Export your Developer ID certificate** from Keychain Access as .p12

2. **Base64-encode the .p12 file:**
   ```bash
   base64 -i developer-id.p12 -o developer-id-base64.txt
   ```

3. **Generate an app-specific password** at https://appleid.apple.com
   (Sign In → App-Specific Passwords → Generate)

4. **Find your Team ID** at https://developer.apple.com/account
   (Membership → Team ID, a 10-character alphanumeric string)

5. **Add GitHub Secrets:**

   | Secret | Value |
   |--------|-------|
   | `CSC_LINK` | Contents of `developer-id-base64.txt` |
   | `CSC_KEY_PASSWORD` | Password for the .p12 file |
   | `APPLE_ID` | Your Apple Developer email |
   | `APPLE_ID_PASSWORD` | App-specific password (NOT your account password) |
   | `APPLE_TEAM_ID` | Your 10-character Team ID |

6. **Push a version tag** (`v*`) to trigger a signed + notarized release build.

### Result
- The .dmg installs without Gatekeeper warnings
- No `xattr -cr` workaround needed
- Users see "identified developer" in the security prompt

---

## Verifying Signatures

### Windows
```powershell
Get-AuthenticodeSignature .\AutoApply-Setup.exe
```

### macOS
```bash
codesign --verify --deep --strict /Applications/AutoApply.app
spctl --assess --type execute /Applications/AutoApply.app
```

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| "No identity found for signing" (macOS) | Verify `CSC_LINK` is valid base64 of .p12 file |
| Notarization timeout | Apple's service can take 5-15 minutes; CI timeout is 30 min |
| "Invalid credentials" notarization error | Use app-specific password, not account password |
| Windows signing fails silently | Check `WIN_CSC_LINK` is valid base64 of .pfx file |
| Unsigned build despite secrets configured | Verify secret names match exactly (case-sensitive) |

---

## Security Notes

- Certificate files and passwords exist ONLY in GitHub Actions secrets
- Secrets are never printed in logs (GitHub masks them automatically)
- The entitlements file (`entitlements.mac.plist`) allows JIT and unsigned memory
  access, which is required for Electron + Node.js + Python child processes
