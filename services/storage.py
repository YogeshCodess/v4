"""
StorageAdapter — abstracts all file I/O behind a single interface.

Current implementation: local filesystem.
Swap to S3/GCS by implementing the same interface.

Usage:
    storage = StorageAdapter.local(base_dir=settings.output_dir)
    storage.write(project_name, "requirements.md", content)
    text = storage.read(project_name, "requirements.md")
    files = storage.list_files(project_name)
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

# Characters illegal in Windows filenames (NTFS). Also drops ASCII control
# chars (0x00–0x1F). Linux/macOS allow most of these but `/` is a separator
# everywhere, so sanitising on all platforms keeps paths consistent.
_ILLEGAL_PATH_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_project_dirname(project_name: str) -> str:
    """Slugify a project name into a single directory segment that is valid
    on Windows, macOS, and Linux.

    Replaces NTFS-illegal chars (< > : " / \\ | ? *) and ASCII control chars
    with `_`, normalises spaces to `_`, lowercases, and strips trailing
    dots/spaces (also illegal on Windows). Falls back to "project" if the
    result is empty.
    """
    safe = _ILLEGAL_PATH_CHARS.sub("_", project_name).replace(" ", "_").lower()
    safe = safe.rstrip(". ")
    return safe or "project"


class StorageAdapter:
    """Filesystem-backed storage adapter. All agents write through this."""

    def __init__(self, base_dir: Path):
        self._base = Path(base_dir)
        self._base.mkdir(parents=True, exist_ok=True)

    @classmethod
    def local(cls, base_dir) -> "StorageAdapter":
        return cls(Path(base_dir))

    # ── Directory helpers ────────────────────────────────────────────────────

    def project_dir(self, project_name: str) -> Path:
        """Return (and create) the output directory for a project."""
        p = self._base / safe_project_dirname(project_name)
        p.mkdir(parents=True, exist_ok=True)
        return p

    # ── Core operations ──────────────────────────────────────────────────────

    def write(self, project_name: str, filename: str, content: str) -> Path:
        """Write text content to a file in the project directory."""
        dest = self.project_dir(project_name) / filename
        dest.parent.mkdir(parents=True, exist_ok=True)  # create subdirs (e.g. qt_gui/, .github/workflows/)
        dest.write_text(content, encoding="utf-8")
        log.debug("storage.write", extra={"file": str(dest), "bytes": len(content)})
        return dest

    def write_bytes(self, project_name: str, filename: str, data: bytes) -> Path:
        dest = self.project_dir(project_name) / filename
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        return dest

    def read(self, project_name: str, filename: str) -> Optional[str]:
        """Read a text file, returning None if it doesn't exist."""
        src = self.project_dir(project_name) / filename
        if not src.exists():
            return None
        return src.read_text(encoding="utf-8")

    def exists(self, project_name: str, filename: str) -> bool:
        return (self.project_dir(project_name) / filename).exists()

    def list_files(self, project_name: str, pattern: str = "*") -> list[Path]:
        """List all files matching pattern in the project directory."""
        return sorted(self.project_dir(project_name).glob(pattern))

    def write_outputs(self, project_name: str, outputs: dict[str, str]) -> dict[str, Path]:
        """
        Write a dict of {filename: content} and return {filename: path}.
        This is the primary interface for agents.
        """
        written = {}
        for filename, content in outputs.items():
            written[filename] = self.write(project_name, filename, content)
        return written

    # ── Path utilities ───────────────────────────────────────────────────────

    def abs_path(self, project_name: str, filename: str) -> Path:
        return self.project_dir(project_name) / filename
