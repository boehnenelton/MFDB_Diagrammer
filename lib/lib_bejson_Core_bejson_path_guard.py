"""
Library:        lib_bejson_Core_bejson_path_guard.py
Family:         Core
Description:    Secure path resolver and boundary protection logic.
Version:        1.1.0
Date:           2026-08-07
CHANGE (2026-08-07): Fixed a sibling-directory bypass in bejson_safe_join()
(found while fixing LIB-CH-H1, which delegates to this same helper). The
`str(target_path).startswith(str(base_path))` check is a raw string
prefix test: base_dir="/x/out" is a startswith-match for a resolved
target of "/x/out_evil/secret" -- no traversal needed, just a
similarly-named sibling. Replaced with Path.is_relative_to() (falls
back to a manual parents walk on Python <3.9, though policy floors at
3.10 so that branch is defensive only), which requires an exact
directory-boundary match, not a string prefix.
RELATIONAL_ID:  31305b9a-7172-4600-b45e-50355ec0b1c5
"""

import os
from pathlib import Path

def bejson_safe_join(base_dir: str, *paths: str) -> str:
    """
    Safely join paths and ensure the result is within the base_dir.
    Mitigates path traversal attacks (Phase 2), including sibling-directory
    prefix bypasses (Phase 3, LIB-CH-H1 follow-up).
    """
    base_path = Path(base_dir).resolve()
    # Handle environment variables in paths if any
    resolved_paths = [os.path.expandvars(p) for p in paths]
    target_path = base_path.joinpath(*resolved_paths).resolve()

    is_inside = target_path == base_path
    if not is_inside:
        try:
            is_inside = target_path.is_relative_to(base_path)
        except AttributeError:
            is_inside = base_path in target_path.parents

    if not is_inside:
        raise ValueError(f"Path traversal detected: {target_path} is outside of {base_path}")

    return str(target_path)


def resolve_storage_path(path: str) -> str:
    """
    Standardized resolve_path utility for environment abstraction (Phase 1).
    Prioritizes $BEJSON_STORAGE_ROOT.
    """
    storage_root = os.environ.get("BEJSON_STORAGE_ROOT")
    if not storage_root:
        # Fallback to local home if storage root is unknown
        storage_root = os.path.expanduser("~")
        
    if not path:
        return storage_root

    # Standardize absolute paths from legacy hardcoding (if encountered)
    if path.startswith("/storage/emulated/0"):
        return path.replace("/storage/emulated/0", storage_root)
        
    return path

def _bejson_mfdb_escapes_root(relative_path: str) -> bool:
    """
    Relative-depth path traversal check.  Normalizes separators then counts
    directory depth segment by segment.  Returns True (path is unsafe) if any
    '..' segment attempts to drop the depth below 0, indicating an escape
    above the MFDB root.

    Mirrors _escapesRoot in lib_bejson_Core_mfdb_validators.ts exactly
    (Remediation NEW-08).

    Args:
        relative_path: A relative path string, e.g. '../104a.mfdb.bejson'.

    Returns:
        True  — path escapes root (unsafe, reject).
        False — path stays within root (safe).
    """
    normalized = relative_path.replace("\\", "/")
    parts = normalized.split("/")
    depth = 0
    for part in parts:
        if part == "..":
            depth -= 1
            if depth < 0:
                return True
        elif part != "." and part != "":
            depth += 1
    return False


VERSION = "1.2.0"
