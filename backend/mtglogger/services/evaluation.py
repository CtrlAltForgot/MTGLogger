"""Durable labeled webcam evidence for held-out recognition evaluation."""

import json
import shutil
import threading
from pathlib import Path

from ..config import get_settings
from ..schemas import Candidate

_manifest_lock = threading.Lock()


def preserve_review_scan(source: Path, review_id: str) -> Path:
    """Archive every queued camera capture before UI cleanup can remove it."""
    root = get_settings().evaluation_dir / "raw"
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{review_id}{source.suffix.lower() or '.jpg'}"
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)
    manifest = root / "manifest.json"
    with _manifest_lock:
        records = json.loads(manifest.read_text()) if manifest.is_file() else []
        records = [item for item in records if item.get("review_id") != review_id]
        records.append({"review_id": review_id, "image_path": str(destination)})
        temporary = manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, indent=2) + "\n")
        temporary.replace(manifest)
    return destination


def preserve_confirmed_scan(
    source: Path,
    review_id: str,
    candidate: Candidate,
    language: str,
) -> Path:
    """Copy a confirmed camera frame and atomically upsert its exact label."""
    root = get_settings().evaluation_dir
    root.mkdir(parents=True, exist_ok=True)
    destination = root / f"{review_id}{source.suffix.lower() or '.jpg'}"
    if source.resolve() != destination.resolve():
        shutil.copyfile(source, destination)
    record = {
        "review_id": review_id,
        "image_path": str(destination),
        "scryfall_id": candidate.scryfall_id,
        "name": candidate.name,
        "set_code": candidate.set_code,
        "collector_number": candidate.collector_number,
        "language": language,
    }
    manifest = root / "manifest.json"
    with _manifest_lock:
        records = json.loads(manifest.read_text()) if manifest.is_file() else []
        records = [item for item in records if item.get("review_id") != review_id]
        records.append(record)
        temporary = manifest.with_suffix(".tmp")
        temporary.write_text(json.dumps(records, indent=2) + "\n")
        temporary.replace(manifest)
    return destination
