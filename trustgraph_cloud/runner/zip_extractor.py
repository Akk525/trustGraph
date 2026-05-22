from __future__ import annotations

import stat
import zipfile
from pathlib import Path


class ZipSlipError(Exception):
    """Raised when a ZIP entry attempts path traversal or contains a symlink."""


class ZipTooLargeError(Exception):
    """Raised when a ZIP exceeds the configured file-count or byte limits."""


def safe_extract(
    zip_path: Path,
    extract_to: Path,
    max_files: int = 1000,
    max_bytes: int = 50_000_000,
) -> None:
    """
    Extract a ZIP archive to extract_to with defence-in-depth safety checks.

    Rejects:
    - Archives with more entries than max_files
    - Archives whose total uncompressed size exceeds max_bytes (zip-bomb guard)
    - Entries with absolute paths
    - Entries whose resolved path escapes extract_to (zip-slip guard)
    - Entries that are Unix symlinks

    Raises ZipSlipError or ZipTooLargeError on violation; any other zipfile
    exception propagates unchanged so callers can distinguish malformed ZIPs.
    """
    extract_to = extract_to.resolve()
    extract_to.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(zip_path, "r") as zf:
        members = zf.infolist()

        if len(members) > max_files:
            raise ZipTooLargeError(
                f"ZIP has {len(members)} entries; limit is {max_files}"
            )

        total_bytes = sum(m.file_size for m in members)
        if total_bytes > max_bytes:
            raise ZipTooLargeError(
                f"ZIP uncompressed size {total_bytes} B exceeds limit {max_bytes} B"
            )

        for member in members:
            name = member.filename

            # Reject absolute paths (both POSIX and Windows)
            if name.startswith("/") or name.startswith("\\") or (
                len(name) >= 2 and name[1] == ":"
            ):
                raise ZipSlipError(f"Absolute path rejected: {name!r}")

            # Reject '..' components — catches ../../../etc style traversal
            if ".." in Path(name).parts:
                raise ZipSlipError(f"Path traversal rejected: {name!r}")

            # Resolve full target and confirm it is rooted inside extract_to
            target = (extract_to / name).resolve()
            target_str = str(target)
            root_str = str(extract_to)
            if not (target_str == root_str or target_str.startswith(root_str + "/")):
                raise ZipSlipError(f"Zip-slip escape rejected: {name!r} → {target}")

            # Reject symlinks — external_attr upper 16 bits hold Unix file mode
            unix_mode = member.external_attr >> 16
            if unix_mode != 0 and stat.S_ISLNK(unix_mode):
                raise ZipSlipError(f"Symlink entry rejected: {name!r}")

            zf.extract(member, extract_to)
