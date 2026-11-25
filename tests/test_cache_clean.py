"""'Limpa a cache' only removes what is older than 24 hours.

The old version emptied the temp folders completely, including the files of
an installer that was half-way through. These tests build a fake temp folder
and check what survives.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from plugins import ghost_organizer as go

DAY = 24 * 60 * 60


@pytest.fixture
def folders(tmp_path, monkeypatch):
    temp = tmp_path / "Temp"
    temp.mkdir()
    explorer = tmp_path / "Explorer"
    explorer.mkdir()
    monkeypatch.setattr(go, "_cache_folders", lambda: [temp])
    monkeypatch.setattr(go, "_explorer_cache_folder", lambda: explorer)
    return temp, explorer


def _write(path: Path, size: int = 10) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    return path


def _make_recent(path: Path, now: float) -> None:
    """Dated one hour before ``now`` (which the tests put in the future)."""
    os.utime(path, (now - 3600, now - 3600))


def test_only_files_older_than_a_day_are_deleted(folders):
    temp, _ = folders
    now = time.time() + 3 * DAY           # every real file is "3 days old"
    old = _write(temp / "old.tmp", 100)
    recent = _write(temp / "installer-part.tmp", 100)
    _make_recent(recent, now)

    result = go.clean_windows_cache(now=now)

    assert not old.exists()
    assert recent.exists()
    assert result["deleted"] == 1
    assert result["kept_recent"] == 1
    assert "24 horas" in result["summary"]


def test_a_folder_with_a_recent_file_is_kept_with_that_file(folders):
    temp, _ = folders
    now = time.time() + 3 * DAY
    _write(temp / "setup" / "old.bin")
    recent = _write(temp / "setup" / "unpacking.bin")
    _make_recent(recent, now)
    gone = _write(temp / "leftover" / "a.log")

    go.clean_windows_cache(now=now)

    assert recent.exists()
    assert not (temp / "setup" / "old.bin").exists()
    assert not gone.exists()
    assert not (temp / "leftover").exists()      # empty and old: removed
    assert temp.exists()                          # the temp folder itself stays


def test_nothing_is_deleted_when_everything_is_new(folders):
    temp, _ = folders
    now = time.time()                     # files were just written
    _write(temp / "a.tmp")
    _write(temp / "b" / "c.tmp")

    result = go.clean_windows_cache(now=now)

    assert result["deleted"] == 0
    assert (temp / "a.tmp").exists()
    assert (temp / "b" / "c.tmp").exists()


@pytest.mark.skipif(os.name == "nt", reason="symlinks need extra rights on Windows")
def test_links_are_never_followed(folders, tmp_path):
    temp, _ = folders
    now = time.time() + 3 * DAY
    precious = tmp_path / "Documents"
    keep = _write(precious / "thesis.docx")
    os.symlink(precious, temp / "link-to-documents", target_is_directory=True)

    go.clean_windows_cache(now=now)

    assert keep.exists()
    assert (temp / "link-to-documents").is_symlink()


def test_only_thumbnail_caches_go_in_explorers_folder(folders):
    _, explorer = folders
    now = time.time() + 3 * DAY
    thumbs = _write(explorer / "thumbcache_256.db")
    icons = _write(explorer / "iconcache_32.db")
    settings = _write(explorer / "ExplorerStartupLog.etl")

    go.clean_windows_cache(now=now)

    assert not thumbs.exists()
    assert not icons.exists()
    assert settings.exists()


def test_the_same_folder_listed_twice_is_cleaned_once(tmp_path, monkeypatch):
    temp = tmp_path / "Temp"
    temp.mkdir()
    monkeypatch.setenv("TEMP", str(temp))
    monkeypatch.setattr(go.Path, "home", classmethod(lambda cls: tmp_path))
    (tmp_path / "AppData" / "Local").mkdir(parents=True)
    os.symlink(temp, tmp_path / "AppData" / "Local" / "Temp") if os.name != "nt" else None

    folders = go._cache_folders()
    resolved = [os.path.normcase(str(p.resolve())) for p in folders if p.exists()]
    assert len(resolved) == len(set(resolved))


def test_files_of_another_account_are_not_called_in_use(folders, monkeypatch):
    temp, _ = folders
    now = time.time() + 3 * DAY
    locked = _write(temp / "open-in-word.tmp")
    foreign = _write(temp / "admin-only.tmp")
    real_unlink = os.unlink

    def unlink(path, *args, **kwargs):
        name = os.path.basename(str(path))
        if name == "open-in-word.tmp":
            exc = PermissionError(13, "in use")
            exc.winerror = 32                 # sharing violation
            raise exc
        if name == "admin-only.tmp":
            exc = PermissionError(13, "denied")
            exc.winerror = 5                  # access denied
            raise exc
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(go.os, "unlink", unlink)
    result = go.clean_windows_cache(now=now)

    assert locked.exists() and foreign.exists()
    assert result["in_use"] == 1
    assert result["no_access"] == 1
    assert "1 em uso" in result["summary"]
    assert "1 sem permissão" in result["summary"]
