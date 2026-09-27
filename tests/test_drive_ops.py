"""mac-helper/ops/drive.py against a throwaway folder standing in for iCloud Drive (ICLOUD_DRIVE_ROOT), with a plain folder standing in
for the Trash (ICLOUD_DRIVE_TEST_TRASH). It runs under Apple's Python when present, because that is what the helper uses on a Mac.
Downloading offloaded files and the real `trash` command can only be checked on a Mac, by hand."""
import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

SCRIPT = pathlib.Path(__file__).parent.parent / "mac-helper" / "ops" / "drive.py"
PY = "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else sys.executable


@pytest.fixture()
def drive(tmp_path):
    root, trash = tmp_path / "Drive", tmp_path / "Trash"
    (root / "Documents" / "Tax").mkdir(parents=True)
    (root / "Documents" / "Tax" / "2025.txt").write_text("income 100\nexpenses 40\n")
    (root / "Documents" / "letter.rtf").write_text(r"{\rtf1\ansi Dear landlord, the heating is broken.}")
    (root / "Documents" / "photo.jpg").write_bytes(b"\xff\xd8\xff\x00\x00JFIF" + b"\x00" * 100)
    (root / "Music").mkdir()
    (root / "Music" / "Draft.logicx").mkdir()                                  # an app document (package)
    (root / ".hidden").write_text("x")
    (root / ".Trash").mkdir()
    (root / ".Trash" / "old.txt").write_text("gone")
    (root / "notes.md").write_text("# Ideas\n" + "word " * 5000)
    trash.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do not read")
    os.symlink(outside, root / "escape")
    return root, trash


def run(drive, op, args):
    root, trash = drive
    env = {**os.environ, "ICLOUD_DRIVE_ROOT": str(root), "ICLOUD_DRIVE_TEST_TRASH": str(trash),
           "ICLOUD_DRIVE_TEXT_CACHE": str(root.parent / "cache" / "text.sqlite")}
    p = subprocess.run([PY, "-I", str(SCRIPT), op, json.dumps(args)], capture_output=True, text=True, env=env, timeout=60)
    if p.returncode == 0:
        return True, json.loads(p.stdout)
    return False, p.stderr


def ok(drive, op, args):
    good, out = run(drive, op, args)
    assert good, out
    return out


def fails(drive, op, args, match):
    good, out = run(drive, op, args)
    assert not good and match in out, out
    return out


def test_listing_puts_folders_first_hides_dotfiles_and_never_shows_the_trash(drive):
    root = ok(drive, "drive_list", {})
    assert [i["name"] for i in root["items"]] == ["Documents", "Music", "notes.md"]
    assert ".Trash" not in json.dumps(ok(drive, "drive_list", {"include_hidden": True}))
    assert ".hidden" in json.dumps(ok(drive, "drive_list", {"include_hidden": True}))
    music = ok(drive, "drive_list", {"path": "Music"})
    assert music["items"][0]["type"] == "package"
    assert ok(drive, "drive_list", {"limit": 2})["truncated"] is True


def test_paths_can_never_leave_the_drive(drive):
    fails(drive, "drive_list", {"path": "../"}, "'..' is not allowed")
    fails(drive, "drive_read", {"path": "Documents/../../outside/secret.txt"}, "'..' is not allowed")
    fails(drive, "drive_read", {"path": "escape/secret.txt"}, "outside iCloud Drive")                 # through a symbolic link
    fails(drive, "drive_list", {"path": "escape"}, "outside iCloud Drive")
    fails(drive, "drive_read", {"path": "/etc/passwd"}, "not found")                                   # absolute paths are relative too
    fails(drive, "drive_list", {"path": ".Trash"}, "off limits")
    fails(drive, "drive_read", {"path": ".Trash/old.txt"}, "off limits")


def test_reading_text_and_documents_with_offsets(drive):
    t = ok(drive, "drive_read", {"path": "Documents/Tax/2025.txt"})
    assert t["text"].startswith("income 100") and t["kind"] == "text" and t["truncated"] is False
    part = ok(drive, "drive_read", {"path": "notes.md", "max_chars": 100, "offset": 8})
    assert len(part["text"]) == 100 and part["truncated"] is True and part["text"].startswith("word")
    if shutil.which("textutil"):
        assert "heating is broken" in ok(drive, "drive_read", {"path": "Documents/letter.rtf"})["text"]
    fails(drive, "drive_read", {"path": "Documents/photo.jpg"}, "binary file")
    fails(drive, "drive_read", {"path": "Documents"}, "that is a folder")
    fails(drive, "drive_read", {"path": "Music/Draft.logicx"}, "app document")


def test_getting_a_file_hands_over_the_exact_bytes_and_refuses_what_is_not_one_file(drive):
    import base64
    got = ok(drive, "drive_get_file", {"path": "Documents/photo.jpg"})
    assert base64.b64decode(got["data_base64"]) == (drive[0] / "Documents" / "photo.jpg").read_bytes()
    assert got["name"] == "photo.jpg" and got["mime_type"] == "image/jpeg" and got["bytes"] == (drive[0] / "Documents" / "photo.jpg").stat().st_size
    fails(drive, "drive_get_file", {"path": "Documents"}, "folder")
    fails(drive, "drive_get_file", {"path": "Music/Draft.logicx"}, "app document")
    fails(drive, "drive_get_file", {"path": "notes.md", "max_bytes": 100}, "more than")
    fails(drive, "drive_get_file", {"path": "escape/secret.txt"}, "")                 # outside the Drive, through a link
    fails(drive, "drive_get_file", {"path": "../outside/secret.txt"}, "")


def test_content_search_finds_words_inside_files_with_an_excerpt_and_remembers_them(drive):
    root, _ = drive
    (root / "Documents" / "recipe.txt").write_text("Grandma's Café crème brûlée\nneeds six eggs and cream")
    first = ok(drive, "drive_search_content", {"query": "cafe CREME", "budget": 40})
    assert first["complete"] and [i["path"] for i in first["items"]] == ["Documents/recipe.txt"]
    assert "Café crème" in first["items"][0]["excerpt"] and first["read_now"] >= 3
    cached = ok(drive, "drive_search_content", {"query": "expenses", "budget": 40})
    assert [i["path"] for i in cached["items"]] == ["Documents/Tax/2025.txt"] and cached["read_now"] == 0   # all from the cache now
    if os.path.exists("/usr/bin/textutil"):                                   # RTF/Word text needs macOS's textutil
        rtf = ok(drive, "drive_search_content", {"query": "heating landlord", "budget": 40})
        assert [i["path"] for i in rtf["items"]] == ["Documents/letter.rtf"]
    assert ok(drive, "drive_search_content", {"query": "income", "path": "Documents/Tax"})["count"] == 1
    assert ok(drive, "drive_search_content", {"query": "nothing matches this"})["count"] == 0
    cache = root.parent / "cache" / "text.sqlite"
    assert oct(cache.stat().st_mode & 0o777) == "0o600"


def test_content_search_skips_the_trash_hidden_files_links_out_and_changed_files_are_reread(drive):
    root, _ = drive
    assert ok(drive, "drive_search_content", {"query": "gone"})["count"] == 0                     # .Trash
    assert ok(drive, "drive_search_content", {"query": "do not read"})["count"] == 0              # outside, through a link
    fails(drive, "drive_search_content", {"query": "x", "path": "../outside"}, "")
    ok(drive, "drive_search_content", {"query": "income"})
    (root / "Documents" / "Tax" / "2025.txt").write_text("income 999 and a refund")
    again = ok(drive, "drive_search_content", {"query": "refund"})
    assert again["count"] == 1 and again["read_now"] == 1


def test_writing_never_silently_replaces_and_only_writes_plain_text(drive):
    root, trash = drive
    w = ok(drive, "drive_write", {"path": "Plans/week.md", "content": "Mon: cinema"})
    assert w["replaced"] is False and (root / "Plans" / "week.md").read_text() == "Mon: cinema"
    fails(drive, "drive_write", {"path": "Plans/week.md", "content": "x"}, "already exists")
    r = ok(drive, "drive_write", {"path": "Plans/week.md", "content": "Tue: park", "overwrite": True})
    assert r["replaced"] is True and (root / "Plans" / "week.md").read_text() == "Tue: park"
    assert [p.read_text() for p in trash.iterdir()] == ["Mon: cinema"]                         # the old version went to the Trash
    fails(drive, "drive_write", {"path": "report.pdf", "content": "x"}, "only plain text")
    fails(drive, "drive_write", {"path": "Documents", "content": "x", "overwrite": True}, "folder with that name")
    fails(drive, "drive_write", {"path": "", "content": "x"}, "whole iCloud Drive")


def test_creating_folders_is_idempotent(drive):
    made = ok(drive, "drive_mkdir", {"path": "Documents/Tax/2026"})
    assert made["existed"] is False and made["path"] == "Documents/Tax/2026"
    assert ok(drive, "drive_mkdir", {"path": "Documents/Tax/2026"})["existed"] is True
    fails(drive, "drive_mkdir", {"path": "notes.md"}, "file with that name")


@pytest.mark.parametrize("path", ["notes.md/sub", "notes.md/a/b", "Documents/Tax/2025.txt/2026"])
def test_a_file_in_the_way_of_a_new_folder_is_named_not_a_crash(drive, path):
    file = path.split(".txt")[0] + ".txt" if ".txt" in path else "notes.md"
    out = fails(drive, "drive_mkdir", {"path": path}, f"'{file}' is a file, not a folder")
    assert "Traceback" not in out and "nothing was created" in out
    out = fails(drive, "drive_write", {"path": path + "/x.txt", "content": "x"}, f"'{file}' is a file, not a folder")
    assert "Traceback" not in out


def test_moving_renames_or_moves_into_a_folder_and_never_overwrites(drive):
    root, _ = drive
    assert ok(drive, "drive_move", {"path": "notes.md", "to": "ideas.md"})["to"] == "ideas.md"
    assert ok(drive, "drive_move", {"path": "ideas.md", "to": "Documents"})["to"] == "Documents/ideas.md"
    assert (root / "Documents" / "ideas.md").exists()
    fails(drive, "drive_move", {"path": "Documents/ideas.md", "to": "Documents/Tax/2025.txt"}, "already exists")
    fails(drive, "drive_move", {"path": "Documents", "to": "Documents/Tax"}, "into itself")
    fails(drive, "drive_move", {"path": "Documents/ideas.md", "to": "Nope/ideas.md"}, "does not exist")
    fails(drive, "drive_move", {"path": "", "to": "x"}, "whole iCloud Drive")
    fails(drive, "drive_move", {"path": "Documents/ideas.md", "to": ".Trash"}, "off limits")
    fails(drive, "drive_move", {"path": "Documents/ideas.md", "to": "escape/stolen.md"}, "outside iCloud Drive")


def test_trashing_moves_to_the_trash_and_refuses_the_root(drive):
    root, trash = drive
    t = ok(drive, "drive_trash", {"path": "Documents/Tax"})
    assert t["type"] == "folder" and not (root / "Documents" / "Tax").exists() and len(list(trash.iterdir())) == 1
    fails(drive, "drive_trash", {"path": ""}, "whole iCloud Drive")
    fails(drive, "drive_trash", {"path": ".Trash/old.txt"}, "off limits")
    fails(drive, "drive_trash", {"path": "nope"}, "not found")


def test_searching_names_skips_hidden_items_and_the_trash(drive):
    s = ok(drive, "drive_search", {"query": "TAX"})
    assert [i["path"] for i in s["items"]] == ["Documents/Tax"]
    assert ok(drive, "drive_search", {"query": "2025"})["items"][0]["path"] == "Documents/Tax/2025.txt"
    assert ok(drive, "drive_search", {"query": "old"})["count"] == 0                        # only in .Trash
    assert ok(drive, "drive_search", {"query": "secret"})["count"] == 0                     # only behind the escaping link
    assert ok(drive, "drive_search", {"query": "escape"})["count"] == 0
    fails(drive, "drive_search", {"query": "  "}, "search text is required")


def test_the_helper_runs_drive_operations_through_the_fixed_script_with_a_time_budget(monkeypatch):
    sys.path.insert(0, str(SCRIPT.parent.parent))
    import icloud_mac_helper as h
    assert h.DRIVE_OPS == {op for op in h.OPS if op.startswith("drive_")} and len(h.DRIVE_OPS) == 10
    cmd = h.build_command("drive_list", {"path": "x"})
    assert cmd[:3] == [sys.executable, "-I", h.DRIVE_SCRIPT] and cmd[3] == "drive_list" and json.loads(cmd[4]) == {"path": "x"}
    seen = {}

    def fake_run(cmd, **kw):
        seen["args"] = json.loads(cmd[-1])
        class P:
            returncode, stdout, stderr = 0, b'{"ok": 1}', b""
        return P()
    monkeypatch.setattr(h.subprocess, "run", fake_run)
    assert h.run_op("drive_read", {"path": "a.txt"}, timeout=60)[0] is True
    assert seen["args"] == {"path": "a.txt", "budget": 50}
    assert h.run_op("drive_read", {"path": "a.txt", "budget": 999}, timeout=60)[2].startswith("operation drive_read does not take")
