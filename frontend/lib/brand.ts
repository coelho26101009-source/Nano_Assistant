/**
 * The product's visible name.
 *
 * The brand is simply NANO. `version.json` still carries "Nano Assistant" in
 * its `name` field, but that record is shared with the Python backend and the
 * Electron main process, which report it in their own payloads, so it is not
 * renamed from here. What a person reads in this interface comes from this
 * constant instead.
 */
export const BRAND_NAME = "NANO";
