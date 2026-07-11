import json
import os

from pinchana_pinterest.storage import MediaStorage


def _entry(storage: MediaStorage, pin_id: str, size: int, age: int):
    directory = storage.entry(pin_id)
    directory.mkdir()
    (directory / "media.jpg").write_bytes(b"x" * size)
    metadata = directory / "metadata.json"
    metadata.write_text(json.dumps({"local_files": ["media.jpg"], "response": {}}))
    os.utime(metadata, (age, age))


def test_cache_evicts_oldest_completed_entry(tmp_path):
    storage = MediaStorage(str(tmp_path), max_size_gb=0.000001, max_item_mb=1)
    _entry(storage, "1", 700, 1)
    _entry(storage, "2", 700, 2)
    storage.ensure_space()
    assert not storage.entry("1").exists()
    assert storage.entry("2").exists()

