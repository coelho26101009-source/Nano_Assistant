/**
 * Public keys that may sign NANO update feeds: ECDSA P-256, SPKI DER in base64.
 * The same list as electron/lib/update-keys.json (a test checks they match).
 * The private key is the NANO_UPDATE_SIGNING_KEY GitHub Actions secret and is
 * never in the repository. See docs/UPDATES.md.
 */
export const UPDATE_KEYS: ReadonlyArray<{ id: string; spki: string }> = [
  {
    id: "d9c6b38f2b29f486",
    spki: "MFkwEwYHKoZIzj0CAQYIKoZIzj0DAQcDQgAEuIw8JPwMrU1bslKPs1yNbV1IT1KlZ7IenDWRTmh+aVALLjX9sBSFmDYkpy7+at9cPswhiuYflrB33IbxDd7S4Q==",
  },
];
