"""Disk-backed cache for exchange company-directory lookups.

Search-by-name needs a live browser fetch to build/refresh a directory, and
that occasionally fails -- a site hiccup, a stale Chrome profile lock left
by an earlier crashed run, a network blip. Before this, each lookup module
only cached in-process (a module-level dict), which is wiped every time the
webapp restarts -- so a restart plus one bad fetch meant search simply
failed with no fallback.

Persisting the last-known-good directory to disk means a failed refresh
degrades to "search slightly stale data" instead of "search fails outright",
and the cache survives webapp restarts. A successful fetch always
overwrites the file, so a newly listed company shows up next time the cache
refreshes.
"""

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "state" / "lookup_cache"


def load(name: str):
    path = CACHE_DIR / f"{name}.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        log.exception("Failed to read lookup cache %s -- ignoring it", path)
        return None


def save(name: str, data) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path = CACHE_DIR / f"{name}.json"
    tmp = path.with_suffix(".json.tmp")
    try:
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        log.exception("Failed to write lookup cache %s", path)
