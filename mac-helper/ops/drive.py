"""iCloud Drive operations for the Mac helper. Run as: python3 -I drive.py <operation> '<json arguments>'. Prints one JSON document.

Everything happens inside the iCloud Drive folder that macOS keeps synced (~/Library/Mobile Documents/com~apple~CloudDocs), so every
change syncs to the user's other devices by itself. Paths are always RELATIVE to that folder; anything that resolves outside it,
including through a symbolic link, is refused. The Drive's own trash folder is never listed, written, moved or trashed.

Nothing is ever deleted permanently: drive_trash, and drive_write with overwrite, move items to the Trash with Apple's own `trash`
command, where the user can recover them. Offloaded files ("Optimise Mac Storage") are downloaded on demand with Apple's `brctl`.
"""
import base64
import datetime
import json
import mimetypes
import os
import sqlite3
import subprocess
import sys
import time
import unicodedata

ROOT = os.path.realpath(os.environ.get("ICLOUD_DRIVE_ROOT") or os.path.expanduser("~/Library/Mobile Documents/com~apple~CloudDocs"))
HERE = os.path.dirname(os.path.abspath(__file__))
PDF_TEXT = os.path.join(os.path.dirname(HERE), "bin", "pdf-text")
SF_DATALESS = 0x40000000                     # set on a file whose contents live only in iCloud
FORBIDDEN = {".trash"}                       # path components that are never touched
TEXTUTIL = {".docx", ".doc", ".rtf", ".rtfd", ".odt", ".html", ".htm", ".webarchive", ".wordml"}
PACKAGES = {".pages", ".numbers", ".key", ".rtfd", ".app", ".bundle", ".photoslibrary", ".logicx", ".band"}
MAX_WRITE = 500000
MAX_GET = 7 * 1024 * 1024                    # largest file drive_get_file hands over (the server passes its own lower cap)


class DriveError(Exception):
    pass


BLOCKED = ("macOS has not given the helper access to iCloud Drive yet. On the Mac, allow it in the dialog if one is showing, or turn it "
           "on in System Settings > Privacy & Security > Files and Folders (iCloud Drive) or Full Disk Access, for python3 and pdf-text.")


def run_tool(cmd, seconds, what):
    """Run one of Apple's command line tools (or the PDF reader) with a hard time limit and a readable failure."""
    try:
        return subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=seconds)
    except subprocess.TimeoutExpired:
        raise DriveError("%s did not finish within %ds. If this is the first time, macOS may be waiting for you to allow access to "
                         "iCloud Drive on the Mac." % (what, seconds))


# ------------------------------------------------------------------ paths
def rel_parts(path):
    parts = [p for p in str(path or "").replace("\\", "/").split("/") if p not in ("", ".")]
    if any(p == ".." for p in parts):
        raise DriveError("paths must stay inside iCloud Drive: '..' is not allowed")
    if any(p.lower() in FORBIDDEN for p in parts):
        raise DriveError("the Drive's trash folder is off limits")
    return parts


def resolve(path, must_exist=True):
    """Absolute path for a Drive-relative path, refusing anything that ends up outside the Drive (symbolic links included)."""
    parts = rel_parts(path)
    full = os.path.join(ROOT, *parts) if parts else ROOT
    parent = os.path.realpath(os.path.dirname(full)) if parts else ROOT
    if parent != ROOT and not parent.startswith(ROOT + os.sep):
        raise DriveError("that path leads outside iCloud Drive")
    full = os.path.join(parent, parts[-1]) if parts else ROOT
    if os.path.islink(full):
        target = os.path.realpath(full)
        if target != ROOT and not target.startswith(ROOT + os.sep):
            raise DriveError("that path is a link leading outside iCloud Drive")
    if must_exist and not os.path.lexists(full):
        raise DriveError("not found: '%s'" % "/".join(parts))
    return full


def rel(full):
    r = os.path.relpath(full, ROOT)
    return "" if r == "." else r


def folders_above(full, action):
    """Refuse, naming it, a file (or anything else that is not a folder) standing where a folder above `full` has to be."""
    at = ROOT
    for part in rel(full).split(os.sep)[:-1]:
        at = os.path.join(at, part)
        if not os.path.lexists(at):
            return
        if not os.path.isdir(at):
            raise DriveError("'%s' is a file, not a folder, so nothing can be %s inside it; nothing was created" % (rel(at), action))


def not_root(full, what):
    if full == ROOT:
        raise DriveError("refusing to %s the whole iCloud Drive" % what)


# ------------------------------------------------------------------ metadata
def iso(ts):
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).isoformat().replace("+00:00", "Z") if ts else None


def offloaded(st):
    return bool(getattr(st, "st_flags", 0) & SF_DATALESS)


def describe(full):
    st = os.lstat(full)
    is_dir = os.path.isdir(full) and not os.path.islink(full)
    ext = os.path.splitext(full)[1].lower()
    kind = "package" if is_dir and ext in PACKAGES else ("folder" if is_dir else "file")
    out = {"path": rel(full), "name": os.path.basename(full) or "iCloud Drive", "type": kind, "modified": iso(st.st_mtime)}
    if kind != "folder":
        out["bytes"] = st.st_size
        out["offloaded"] = offloaded(st)
    return out


def inside(full):
    target = os.path.realpath(full)
    return target == ROOT or target.startswith(ROOT + os.sep)


def listing(full, include_hidden):
    items = []
    for e in os.scandir(full):
        if e.name.lower() in FORBIDDEN or (e.name.startswith(".") and not include_hidden):
            continue
        if e.is_symlink() and not inside(e.path):                          # links leading out of the Drive are not shown at all
            continue
        items.append(describe(e.path))
    items.sort(key=lambda i: (i["type"] != "folder", i["name"].lower()))
    return items


# ------------------------------------------------------------------ downloads and text
def ensure_local(full, budget):
    """Start downloading an offloaded file and wait for it within the time budget. Returns True once the contents are on the Mac."""
    if not offloaded(os.lstat(full)):
        return True
    run_tool(["/usr/bin/brctl", "download", full], max(5, min(budget, 30)), "Starting the download")
    deadline = time.time() + budget
    while time.time() < deadline:
        if not offloaded(os.lstat(full)):
            return True
        time.sleep(1)
    return False


def extract_text(full, seconds=40):
    ext = os.path.splitext(full)[1].lower()
    if ext == ".pdf":
        if not os.access(PDF_TEXT, os.X_OK):
            raise DriveError("PDF reading is not installed on the Mac yet; run the helper's install.sh again")
        p = run_tool([PDF_TEXT, full], seconds, "Reading the PDF")
        if p.returncode != 0:
            raise DriveError("could not read this PDF: " + p.stderr.decode("utf-8", "replace").strip()[:200])
        return p.stdout.decode("utf-8", "replace"), "pdf"
    if ext in TEXTUTIL:
        p = run_tool(["/usr/bin/textutil", "-convert", "txt", "-stdout", full], seconds, "Converting the document")
        if p.returncode != 0:
            raise DriveError("could not convert this document to text")
        return p.stdout.decode("utf-8", "replace"), ext.lstrip(".")
    with open(full, "rb") as f:
        raw = f.read(20 * 1024 * 1024 + 1)
    if b"\x00" in raw[:8192]:
        raise DriveError("this is a binary file (%s); only text, PDF and Word/RTF/ODT/HTML documents can be read" % (ext or "no extension"))
    if len(raw) > 20 * 1024 * 1024:
        raise DriveError("this text file is larger than 20 MB")
    for enc in ("utf-8", "utf-16", "latin-1"):
        try:
            return raw.decode(enc), "text"
        except UnicodeDecodeError:
            continue
    raise DriveError("could not decode this file as text")


TEST_TRASH = os.environ.get("ICLOUD_DRIVE_TEST_TRASH")        # tests only: a plain folder standing in for the Trash


def trash(full):
    if TEST_TRASH:
        os.rename(full, os.path.join(TEST_TRASH, "%d-%s" % (time.time_ns(), os.path.basename(full))))
        return
    p = run_tool(["/usr/bin/trash", full], 40, "Moving it to the Trash")
    if p.returncode != 0 or os.path.lexists(full):
        raise DriveError("macOS could not move it to the Trash: " + p.stderr.decode("utf-8", "replace").strip()[:200])


# ------------------------------------------------------------------ operations
def op_list(a):
    full = resolve(a.get("path"))
    if not os.path.isdir(full):
        return {"item": describe(full)}
    items = listing(full, bool(a.get("include_hidden")))
    limit = a.get("limit") or 200
    return {"path": rel(full), "count": len(items), "truncated": len(items) > limit, "items": items[:limit]}


def op_search(a):
    q = str(a.get("query") or "").strip().lower()
    if not q:
        raise DriveError("a search text is required")
    start, limit, hits = resolve(a.get("path")), a.get("limit") or 50, []
    for dp, dns, fns in os.walk(start):
        dns[:] = [d for d in dns if d.lower() not in FORBIDDEN and not d.startswith(".")]
        dns[:] = [d for d in dns if inside(os.path.join(dp, d))]
        for name in dns + fns:
            if not name.startswith(".") and q in name.lower() and inside(os.path.join(dp, name)):
                hits.append(describe(os.path.join(dp, name)))
                if len(hits) >= limit:
                    return {"query": q, "count": len(hits), "truncated": True, "items": hits}
        dns[:] = [d for d in dns if os.path.splitext(d)[1].lower() not in PACKAGES]      # do not descend into app documents
    return {"query": q, "count": len(hits), "truncated": False, "items": hits}


SEARCHABLE = {".txt", ".md", ".markdown", ".csv", ".json", ".xml", ".log", ".tex", ".pdf"} | TEXTUTIL
MAX_INDEX_BYTES = 30 * 1024 * 1024
CACHE_PATH = os.environ.get("ICLOUD_DRIVE_TEXT_CACHE") or os.path.expanduser(
    "~/Library/Application Support/icloud-mac-helper/drive-text-cache.sqlite")


def fold(s):
    """Lower case without accents, so 'cafe' finds 'Café'."""
    return "".join(ch for ch in unicodedata.normalize("NFKD", s.lower()) if not unicodedata.combining(ch))


def text_cache():
    os.makedirs(os.path.dirname(CACHE_PATH), mode=0o700, exist_ok=True)
    db = sqlite3.connect(CACHE_PATH)
    try:
        os.chmod(CACHE_PATH, 0o600)                     # private; a file owned by someone else is left as it is
    except OSError:
        pass
    db.execute("CREATE TABLE IF NOT EXISTS text (path TEXT PRIMARY KEY, mtime REAL, size INTEGER, body TEXT)")
    return db


def excerpt(text, word, width=90):
    """A short passage around the first place the (accent-folded) word occurs."""
    folded = [fold(ch) or ch for ch in text]
    joined = "".join(folded)
    pos = max(0, joined.find(word))
    idx, total = 0, 0                                     # map the folded position back to the original text
    for idx, piece in enumerate(folded):
        if total >= pos:
            break
        total += len(piece)
    a, b = max(0, idx - width), min(len(text), idx + width)
    return ("..." if a else "") + " ".join(text[a:b].split()) + ("..." if b < len(text) else "")


def op_search_content(a):
    """Words INSIDE files (text, PDF, Word/RTF/ODT/HTML), not just names. Extracted text is kept in a private cache keyed by path,
    modification time and size, so each file is read once and later searches are fast. Offloaded files are not downloaded for a
    search. The time budget is honoured: if it runs out, the answer says how many files are still unread and to ask again."""
    words = [fold(w) for w in str(a.get("query") or "").split() if w.strip()]
    if not words:
        raise DriveError("a search text is required")
    start, limit = resolve(a.get("path")), a.get("limit") or 20
    deadline = time.time() + max(5, (a.get("budget") or 45) - 8)
    db, hits = text_cache(), []
    stats = {"files_searched": 0, "read_now": 0, "not_yet_read": 0, "only_in_icloud": 0, "unreadable": 0, "folders_not_accessible": 0,
             "downloads_started": 0}

    def blocked(_err):                                     # os.walk skips unreadable folders silently: count them instead
        stats["folders_not_accessible"] += 1

    try:
        for dp, dns, fns in os.walk(start, onerror=blocked):
            dns[:] = [d for d in dns if d.lower() not in FORBIDDEN and not d.startswith(".") and inside(os.path.join(dp, d))
                      and os.path.splitext(d)[1].lower() not in PACKAGES]
            for name in fns:
                full = os.path.join(dp, name)
                if name.startswith(".") or os.path.splitext(name)[1].lower() not in SEARCHABLE or not inside(full):
                    continue
                st = os.lstat(full)
                if st.st_size > MAX_INDEX_BYTES:
                    continue
                row = db.execute("SELECT mtime, size, body FROM text WHERE path = ?", (rel(full),)).fetchone()
                cached = bool(row and row[0] == st.st_mtime and row[1] == st.st_size)
                if not cached and offloaded(st):                   # text read before stays usable after macOS offloads the file
                    stats["only_in_icloud"] += 1
                    if a.get("download") and time.time() < deadline and stats["downloads_started"] < 200:
                        try:
                            run_tool(["/usr/bin/brctl", "download", full], 10, "Starting the download")
                            stats["downloads_started"] += 1
                        except DriveError:
                            pass
                    continue
                if cached:
                    body = row[2]
                elif time.time() < deadline:
                    try:
                        body = extract_text(full, max(3, min(20, int(deadline - time.time()))))[0][:500000]
                    except (DriveError, OSError):
                        body = ""
                        stats["unreadable"] += 1
                    db.execute("INSERT OR REPLACE INTO text VALUES (?, ?, ?, ?)", (rel(full), st.st_mtime, st.st_size, body))
                    stats["read_now"] += 1
                else:
                    stats["not_yet_read"] += 1
                    continue
                stats["files_searched"] += 1
                haystack = fold(body) + "\n" + fold(name)
                if len(hits) < limit and all(w in haystack for w in words):
                    hits.append({**describe(full), "excerpt": excerpt(body, words[0]) if body else ""})
        db.commit()
    finally:
        db.close()
    done = stats["not_yet_read"] == 0
    notes = []
    if not done:
        notes.append("Ran out of time before reading every file (the first search reads them all once). Ask again to search the "
                     "rest; it gets faster each time.")
    if stats["only_in_icloud"] and not a.get("download"):
        notes.append("%d file(s) are only in iCloud and were not searched. Search again with download=true to fetch them to the Mac "
                     "in the background; their text is then remembered even if macOS offloads them again." % stats["only_in_icloud"])
    elif stats["downloads_started"]:
        notes.append("Started downloading %d file(s) from iCloud; search again in a minute or two to include them."
                     % stats["downloads_started"])
    if stats["folders_not_accessible"]:
        notes.append("%d folder(s) could not be opened, so they were not searched. %s" % (stats["folders_not_accessible"], BLOCKED))
    return {"query": " ".join(words), "count": len(hits), "complete": done and not stats["folders_not_accessible"], **stats,
            "items": hits, **({"message": " ".join(notes)} if notes else {})}


def op_info(a):
    full = resolve(a.get("path"))
    out = describe(full)
    if out["type"] == "folder":
        out["items"] = sum(1 for e in os.scandir(full) if not e.name.startswith(".") and e.name.lower() not in FORBIDDEN)
    return out


def op_read(a):
    full = resolve(a.get("path"))
    if os.path.isdir(full) and os.path.splitext(full)[1].lower() != ".rtfd":
        raise DriveError("that is a folder; use drive_list_folder" if describe(full)["type"] == "folder" else
                         "that is an app document (%s), which cannot be read as text" % os.path.splitext(full)[1])
    budget = a.get("budget") or 45
    start = time.time()
    # Leave time to extract the text after the download, all within the job's budget.
    if not os.path.isdir(full) and not ensure_local(full, max(1, budget - 15)):
        return {"path": rel(full), "downloading": True,
                "message": "This file is only in iCloud and is still downloading to the Mac. Ask again in a minute."}
    text, kind = extract_text(full, max(5, int(budget - (time.time() - start))))
    offset, limit = a.get("offset") or 0, a.get("max_chars") or 30000
    part = text[offset:offset + limit]
    return {"path": rel(full), "kind": kind, "chars": len(text), "offset": offset, "truncated": offset + len(part) < len(text), "text": part}


def op_get_file(a):
    """The file itself, base64-encoded, so an agent can attach or send it. Folders and app documents (packages such as .pages) are
    refused: they are not single files. Offloaded files are downloaded first, within the time budget."""
    full = resolve(a.get("path"))
    info = describe(full)
    if info["type"] != "file":
        raise DriveError("that is a folder; use drive_list_folder" if info["type"] == "folder" else
                         "that is an app document (%s), which is a bundle of files, not one file. Export it (for example to PDF) first"
                         % os.path.splitext(full)[1])
    limit = min(a.get("max_bytes") or MAX_GET, MAX_GET)
    if info["bytes"] > limit:
        raise DriveError("the file is %.1f MB, more than the %.1f MB that can be handed over" % (info["bytes"] / 1048576, limit / 1048576))
    if not ensure_local(full, max(1, (a.get("budget") or 45) - 5)):
        return {"path": rel(full), "downloading": True,
                "message": "This file is only in iCloud and is still downloading to the Mac. Ask again in a minute."}
    with open(full, "rb") as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise DriveError("the file is larger than the %.1f MB that can be handed over" % (limit / 1048576))
    return {"path": rel(full), "name": os.path.basename(full), "bytes": len(data), "modified": info["modified"],
            "mime_type": mimetypes.guess_type(full)[0] or "application/octet-stream",
            "data_base64": base64.b64encode(data).decode("ascii")}


def op_write(a):
    content = a.get("content") or ""
    if len(content) > MAX_WRITE:
        raise DriveError("that is more than %d characters" % MAX_WRITE)
    full = resolve(a.get("path"), must_exist=False)
    not_root(full, "overwrite")
    if os.path.splitext(full)[1].lower() in PACKAGES or os.path.splitext(full)[1].lower() in TEXTUTIL | {".pdf"}:
        raise DriveError("only plain text files can be written (for example .txt, .md, .csv, .json)")
    folders_above(full, "written")
    replaced = False
    if os.path.lexists(full):
        if os.path.isdir(full):
            raise DriveError("a folder with that name already exists")
        if not a.get("overwrite"):
            raise DriveError("a file with that name already exists; pass overwrite: true to replace it (the old one goes to the Trash)")
        trash(full)
        replaced = True
    os.makedirs(os.path.dirname(full), exist_ok=True)
    with open(full, "x", encoding="utf-8") as f:
        f.write(content)
    out = describe(full)
    out["replaced"] = replaced
    return out


def op_mkdir(a):
    full = resolve(a.get("path"), must_exist=False)
    not_root(full, "create")
    folders_above(full, "created")
    if os.path.lexists(full):
        if os.path.isdir(full):
            return dict(describe(full), existed=True)
        raise DriveError("a file with that name already exists")
    os.makedirs(full)
    return dict(describe(full), existed=False)


def op_move(a):
    src = resolve(a.get("path"))
    not_root(src, "move")
    dst = resolve(a.get("to"), must_exist=False)
    if os.path.isdir(dst) and not os.path.islink(dst) and describe(dst)["type"] == "folder":
        dst = os.path.join(dst, os.path.basename(src))                      # moving INTO an existing folder
    not_root(dst, "overwrite")
    if os.path.lexists(dst):
        raise DriveError("'%s' already exists; nothing was moved" % rel(dst))
    if not os.path.isdir(os.path.dirname(dst)):
        raise DriveError("the destination folder '%s' does not exist; create it first" % rel(os.path.dirname(dst)))
    if os.path.isdir(src) and (dst + os.sep).startswith(src + os.sep):
        raise DriveError("a folder cannot be moved into itself")
    os.rename(src, dst)
    return {"from": rel(src), "to": rel(dst), "moved": True}


def op_trash(a):
    full = resolve(a.get("path"))
    not_root(full, "trash")
    what = describe(full)
    trash(full)
    return {"trashed": what["path"], "type": what["type"], "recoverable": "moved to the Trash; recover it from Recently Deleted in iCloud Drive"}


OPS = {"drive_list": op_list, "drive_search": op_search, "drive_search_content": op_search_content, "drive_info": op_info, "drive_read": op_read, "drive_get_file": op_get_file,
       "drive_write": op_write, "drive_mkdir": op_mkdir, "drive_move": op_move, "drive_trash": op_trash}


def main(argv):
    if len(argv) != 3 or argv[1] not in OPS:
        print("usage: drive.py <%s> '<json>'" % "|".join(sorted(OPS)), file=sys.stderr)
        return 2
    if not os.path.isdir(ROOT):
        print("iCloud Drive is not turned on for this Mac user (no %s)" % ROOT, file=sys.stderr)
        return 1
    try:
        result = OPS[argv[1]](json.loads(argv[2]))
    except DriveError as e:
        print("execution error: Error: %s" % e, file=sys.stderr)
        return 1
    except PermissionError:
        print("execution error: Error: " + BLOCKED, file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
