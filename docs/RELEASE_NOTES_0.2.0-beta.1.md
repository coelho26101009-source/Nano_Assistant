NANO 0.2.0-beta.1 is a public Beta.

This update brings a redesigned dark-first interface, a new NANO visual identity
with a corrected symbol, and clearer Brain and Memory views. The external voice
overlay has also been redesigned, with more restrained motion and support for
reduced-motion settings.

Provider selection is more responsive: AUTO routing and failover have improved,
and LOCAL and CLOUD modes keep their separate provider paths. The packaged
Python dependencies now come from reproducible, hash-verified locks. This Beta
also includes startup, stability and security maintenance.

**Download:** `Nano-Setup-0.2.0-beta.1-x64.exe` for Windows x64. Verify its
SHA256 against the attached `SHA256SUMS.txt` before installing. The MSI is built
and checked as a workflow artifact, but is not a public download.

**Known limitations:** This is Beta software. The installer is not code-signed,
so Windows may show an unknown-publisher or SmartScreen warning. Updates are
manual; there is no automatic updater. Installation and removal on a genuinely
clean Windows environment could not be validated for this release candidate;
the NSIS installer was installed, launched and uninstalled on the build machine
using a temporary location and profile. Do not disable
Windows security controls to install NANO.
