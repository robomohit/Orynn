"""Real filesystem scanner + actions for the Clutter Sweeper widget.

Every function here touches the REAL filesystem. No mock data.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path


def _human_size(b: int | float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if abs(b) < 1024:
            return f"{b:.1f} {unit}" if unit != "B" else f"{int(b)} B"
        b /= 1024
    return f"{b:.1f} TB"


# ── Categories for file organization ─────────────────────────────────────────
_CATEGORIES = {
    "Documents": {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt",
                  ".xls", ".xlsx", ".ppt", ".pptx", ".csv"},
    "Images":    {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg", ".webp",
                  ".ico", ".tiff", ".tif"},
    "Videos":    {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm"},
    "Audio":     {".mp3", ".wav", ".flac", ".aac", ".ogg", ".wma", ".m4a"},
    "Archives":  {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"},
    "Installers":{".exe", ".msi", ".dmg", ".deb", ".rpm", ".appimage"},
    "Code":      {".py", ".js", ".ts", ".html", ".css", ".json", ".xml",
                  ".yaml", ".yml", ".sh", ".bat", ".ps1", ".rb", ".go",
                  ".rs", ".c", ".cpp", ".h", ".java"},
}


def scan_folder(folder_path: str | None = None) -> dict:
    """Scan a real folder, return files sorted by size (largest first)."""
    if folder_path is None:
        folder_path = str(Path.home() / "Downloads")

    files: list[dict] = []
    total_bytes = 0

    try:
        for entry in os.scandir(folder_path):
            if entry.is_file(follow_symlinks=False):
                try:
                    st = entry.stat()
                    files.append({
                        "name": entry.name,
                        "size": _human_size(st.st_size),
                        "bytes": st.st_size,
                        "path": entry.path,
                        "modified": datetime.fromtimestamp(st.st_mtime).isoformat(),
                    })
                    total_bytes += st.st_size
                except OSError:
                    pass
    except PermissionError:
        pass

    files.sort(key=lambda f: f["bytes"], reverse=True)

    return {
        "folder": os.path.basename(folder_path),
        "folder_path": folder_path,
        "files": files,
        "total_size": _human_size(total_bytes),
        "total_bytes": total_bytes,
    }


def organize_files(folder_path: str) -> dict:
    """Move files into category subfolders (Documents, Images, etc.)."""
    moved: list[dict] = []
    errors: list[dict] = []

    try:
        for entry in os.scandir(folder_path):
            if not entry.is_file(follow_symlinks=False):
                continue
            ext = os.path.splitext(entry.name)[1].lower()
            target_cat = "Other"
            for cat, exts in _CATEGORIES.items():
                if ext in exts:
                    target_cat = cat
                    break

            target_dir = os.path.join(folder_path, target_cat)
            os.makedirs(target_dir, exist_ok=True)
            target_path = os.path.join(target_dir, entry.name)

            if os.path.exists(target_path):
                continue  # skip duplicates
            try:
                shutil.move(entry.path, target_path)
                moved.append({"name": entry.name, "to": target_cat})
            except OSError as e:
                errors.append({"name": entry.name, "error": str(e)})
    except Exception as e:
        errors.append({"name": "(scan)", "error": str(e)})

    return {"moved": moved, "errors": errors, "count": len(moved)}


def delete_files(file_paths: list[str]) -> dict:
    """Permanently delete specific files."""
    deleted: list[str] = []
    errors: list[dict] = []

    for path in file_paths:
        try:
            if os.path.isfile(path):
                os.remove(path)
                deleted.append(path)
            elif os.path.isdir(path):
                shutil.rmtree(path)
                deleted.append(path)
        except OSError as e:
            errors.append({"path": path, "error": str(e)})

    return {"deleted": deleted, "errors": errors, "count": len(deleted)}
