# Signing the Windows installers

Status: **not set up.** The installers that `build-windows.yml` produces are
unsigned, so Windows shows "Editor desconhecido" and SmartScreen may warn.
This page is the plan for changing that. Nothing here is wired into the build
yet, because every option needs an account and a decision from the owner, and
a build that signs half the time is worse than one that never claims to.

## What signing does and does not buy

* A signature proves the file came from the named publisher and was not
  changed afterwards. The UAC prompt shows the publisher instead of
  "Unknown".
* It does **not** remove SmartScreen warnings by itself. SmartScreen builds
  reputation per certificate/publisher over downloads. Since 2024 an EV
  certificate no longer skips that, so EV is no longer worth its extra cost
  for this project.
* Since June 2023, publicly trusted code-signing keys must live in hardware
  (an HSM or token). A `.pfx` file in a CI secret is no longer something a
  certificate authority will issue. So the realistic routes are all
  cloud-signing services.

## Options, in recommended order

Check each provider's current terms before choosing. Names, prices and
eligibility for individual developers outside the US change often.

1. **Microsoft's managed signing service (Trusted Signing in electron-builder's
   docs).** Microsoft holds the keys and runs identity validation.
   electron-builder 26 supports it natively through `win.azureSignOptions`, so
   no custom script is needed. It is a low monthly fee. Check first whether
   an individual based in Portugal can complete identity validation. If not,
   it needs a registered business.
2. **SignPath Foundation.** This is free signing for open-source projects that
   meet their criteria (OSI licence, which Apache-2.0 is, plus a public
   repository and a reproducible build in CI). The certificate is issued to
   SignPath Foundation, not to Simão, and the release must be built by their
   GitHub integration.
3. **An OV certificate on a cloud HSM** (for example, a CA's own cloud signing
   service). Works with `signtoolOptions` and a custom sign hook. It is the
   most setup and usually the most expensive per year.

## Wiring it up (option 1), when an account exists

1. In the signing service, create the signing account and a certificate
   profile. Note the **endpoint** (it is region-specific), the **account name**
   and the **profile name**, and the publisher name exactly as it appears on
   the certificate.
2. Create a Microsoft Entra app registration that may sign, and store only its
   credentials as **GitHub Actions secrets** in a protected environment (for
   example `release`) that requires the owner's approval:
   `AZURE_TENANT_ID`, `AZURE_CLIENT_ID`, `AZURE_CLIENT_SECRET`.
3. In `electron/package.json`, under `build.win`:

   ```json
   "azureSignOptions": {
     "publisherName": "<exactly as on the certificate>",
     "endpoint": "https://<region>.codesigning.azure.net",
     "codeSigningAccountName": "<account>",
     "certificateProfileName": "<profile>"
   }
   ```

   The defaults are already right: SHA-256 file digest, and Microsoft's
   RFC 3161 timestamp server. A timestamp keeps the signature valid after
   the certificate expires.
4. In `build-windows.yml`, give **only** the packaging job
   `environment: release` and pass the three secrets as `env`. Never add
   signing to `ci.yml` or to any workflow a pull request can trigger, because a
   fork's PR must never be able to reach a signing key.
5. After the build, verify both installers and fail the job if either is not
   signed:

   ```powershell
   signtool verify /pa /v electron\dist-electron\Nano-Setup-*-x64.exe
   signtool verify /pa /v electron\dist-electron\Nano-Setup-*-x64.msi
   Get-AuthenticodeSignature electron\dist-electron\Nano-Setup-*-x64.exe |
     Where-Object Status -ne 'Valid' | ForEach-Object { throw "unsigned: $($_.Path)" }
   ```

6. Generate `SHA256SUMS.txt` **after** signing. Signing changes the bytes, so
   checksums computed before it are wrong.

## What gets signed

electron-builder signs `Nano Assistant.exe`, the NSIS installer and
uninstaller, and the MSI. The embedded Python runtime's `python.exe` and
`.pyd` files are already signed by the Python Software Foundation and are left
as they are. Re-signing them would replace a trusted signature with ours.

## Until then

Say plainly, in the release notes and on the download page, that the Beta is
unsigned and that Windows will warn. Explain how to check the SHA-256. Never
ask people to turn off SmartScreen or Defender.
