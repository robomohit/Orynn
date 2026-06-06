"""Real filesystem scanner + actions for the Clutter Sweeper widget.

Every function here touches the REAL filesystem. No mock data.
"""
from __future__ import annotations

import os
import shutil
from datetime import datetime
from pathlib import Path

from .state_store import read_json, workspace_state_path, write_json


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
    errors: list[dict] = []
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
    except OSError as e:
        errors.append({"path": folder_path, "error": str(e)})

    files.sort(key=lambda f: f["bytes"], reverse=True)

    return {
        "folder": os.path.basename(folder_path),
        "folder_path": folder_path,
        "files": files,
        "total_size": _human_size(total_bytes),
        "total_bytes": total_bytes,
        "errors": errors,
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


def _unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    for i in range(1, 10_000):
        candidate = parent / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    raise OSError(f"Could not find an available name for {path}")


def _trash_root() -> Path:
    return workspace_state_path(".trash") / "capsule"


def _trash_manifest_path() -> Path:
    return workspace_state_path("capsule_trash_manifest.json")


def _load_trash_manifest() -> list[dict]:
    data = read_json(_trash_manifest_path(), [])
    return data if isinstance(data, list) else []


def _save_trash_manifest(items: list[dict]) -> None:
    write_json(_trash_manifest_path(), items)


def delete_files(file_paths: list[str], *, permanent: bool = False) -> dict:
    """Move files to AI Computer's local trash by default.

    Permanent deletion remains available only when explicitly requested by
    backend callers. The API endpoint defaults to the reversible path.
    """
    deleted: list[str] = []
    trashed: list[dict] = []
    errors: list[dict] = []
    batch_dir = (_trash_root()
                 / datetime.now().strftime("%Y%m%d-%H%M%S-%f"))

    for path in file_paths:
        try:
            src = Path(path).expanduser()
            if not src.exists():
                errors.append({"path": path, "error": "Path does not exist"})
                continue
            if permanent:
                if src.is_file() or src.is_symlink():
                    src.unlink()
                    deleted.append(str(src))
                elif src.is_dir():
                    shutil.rmtree(src)
                    deleted.append(str(src))
                continue

            src_resolved = src.resolve()
            trash_resolved = _trash_root().resolve()
            if src_resolved == trash_resolved or trash_resolved in src_resolved.parents:
                errors.append({"path": path, "error": "Path is already in AI Computer trash"})
                continue

            batch_dir.mkdir(parents=True, exist_ok=True)
            target = _unique_path(batch_dir / src.name)
            item_type = "dir" if src.is_dir() else "file"
            shutil.move(str(src), str(target))
            entry = {
                "id": f"trash-{datetime.now().strftime('%Y%m%d%H%M%S%f')}",
                "original": str(src),
                "trash_path": str(target),
                "name": src.name,
                "type": item_type,
                "moved_at": datetime.now().isoformat(),
            }
            deleted.append(str(src))
            trashed.append(entry)
        except OSError as e:
            errors.append({"path": path, "error": str(e)})

    if trashed:
        manifest = _load_trash_manifest()
        manifest.extend(trashed)
        _save_trash_manifest(manifest)

    return {
        "deleted": deleted,
        "trashed": trashed,
        "errors": errors,
        "count": len(deleted),
        "permanent": permanent,
        "trash_folder": str(batch_dir) if trashed else None,
    }


def restore_trashed(items: list[dict | str]) -> dict:
    """Restore entries previously returned by delete_files."""
    manifest = _load_trash_manifest()
    by_trash = {str(item.get("trash_path")): item for item in manifest
                if isinstance(item, dict) and item.get("trash_path")}
    trash_root = _trash_root().resolve()
    restored: list[dict] = []
    errors: list[dict] = []

    for item in items:
        if isinstance(item, str):
            trash_path = item
        else:
            trash_path = str(item.get("trash_path", ""))

        manifest_entry = by_trash.get(trash_path)
        original = manifest_entry.get("original") if manifest_entry else None

        if not trash_path:
            errors.append({"item": item, "error": "Missing trash_path"})
            continue
        if not manifest_entry or not original:
            errors.append({"trash_path": trash_path, "error": "Trash item is not in AI Computer trash manifest"})
            continue

        try:
            src = Path(trash_path)
            src_resolved = src.resolve()
            if src_resolved == trash_root or trash_root not in src_resolved.parents:
                errors.append({"trash_path": trash_path, "error": "Trash item is outside AI Computer trash"})
                continue
            if not src.exists():
                errors.append({"trash_path": trash_path, "error": "Trash item does not exist"})
                continue
            destination = _unique_path(Path(str(original)))
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(destination))
            record = {
                "trash_path": trash_path,
                "restored_to": str(destination),
                "restored_at": datetime.now().isoformat(),
            }
            restored.append(record)
            if trash_path in by_trash:
                by_trash[trash_path].update(record)
        except OSError as e:
            errors.append({"trash_path": trash_path, "error": str(e)})

    if restored:
        _save_trash_manifest(manifest)

    return {"restored": restored, "errors": errors, "count": len(restored)}
