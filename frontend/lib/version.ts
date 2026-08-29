/**
 * The product version, read from the one file that defines it.
 *
 * `version.json` at the repository root is the single source; `core/version.py`
 * reads the same file, and so does the Electron main process. Before this,
 * `index.tsx` carried `const APP_VERSION = "v1.0"` while the backend reported
 * "8.1.0" from two separate literals, so the About panel and the runtime
 * payload described different products.
 *
 * This is a build-time import on purpose. The lockup shows a version before the
 * eel bridge has connected — and must still show the right one if the backend
 * never connects at all — so it cannot depend on a backend call.
 */
import versionRecord from "../../version.json";

export type VersionRecord = {
  /** Semantic version other software consumes, e.g. "1.0.0". */
  product: string;
  /** What a person sees, e.g. "v1.0". */
  display: string;
  name: string;
  channel: string;
};

export const VERSION: VersionRecord = versionRecord as VersionRecord;

/** The string shown beside the wordmark and in About. */
export const APP_VERSION = VERSION.display;

export default VERSION;
