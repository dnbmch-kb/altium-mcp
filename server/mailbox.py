"""The bridge's mailbox: a server hands Altium one request and takes back the answer to that request only.

Every altium-mcp server on the machine (one per client session) drives the one running Altium through the folder
below; the one-script lock (script_lock.py) lets one of them in at a time. The layout:

    C:/Users/Public/altium_mcp/
        altium_script.lock      the lock: who runs a script now (script_lock.py, tools/altium/altium_lock.py)
        queue/<id>.req          a request waiting for the bridge; the bridge takes it by renaming it into its folder
        mail/<id>/              everything one request exchanges:
            request.json            the command and its parameters, with request_id and sender_pid
            edit_spec.txt ...       the spec files the command reads
            taken                   the queue entry, moved here when the bridge takes the request
            progress.log            the bridge's progress records, one per line: seq|elapsed ms|step|count|total
            response.json           the answer, echoing request_id; written as response.tmp and renamed once
                                    the run has closed the bridge's script project
            closed.txt              how that went, one line per open copy: CLOSED, STILL OPEN or REFUSED <path>
        status.json             the call in flight, rewritten on every change, for a watchdog
        request.json, response.json   the single-file protocol of servers started before the mailbox, which the
                                bridge keeps answering until they restart (Altium_API.pas ServeLegacy)

<id> is <sender pid>-<milliseconds since the epoch>-<sequence>. The bridge takes only a request whose sender holds
the lock, so a request left behind by a server that gave up or died is never run later; the next server under the
lock expires it. A server waits for its own response.json and accepts it only with its own id. It waits as long as
the bridge reports progress and gives up when no record lands for `stall` seconds, not after a fixed time.
"""
import asyncio
import itertools
import json
import os
import shutil
import time
from pathlib import Path

from altium_files import read_altium_text, write_altium_text

QUEUE = "queue"
MAIL = "mail"
STATUS = "status.json"
KEEP_SECONDS = 24 * 3600          # a request's folder is kept this long for diagnosis
_sequence = itertools.count(1)


def new_id(pid=None) -> str:
    return f"{os.getpid() if pid is None else pid}-{int(time.time() * 1000)}-{next(_sequence)}"


def sender_pid(request_id: str) -> int:
    return int(request_id.split("-", 1)[0])


def parse_records(text: str) -> list:
    """progress.log as records; a line still being written (no line end yet) or malformed is skipped."""
    records = []
    for line in text.splitlines(keepends=True):
        if not line.endswith(("\n", "\r")):
            continue
        f = line.rstrip("\r\n").split("|")
        if len(f) < 5:
            continue
        try:
            records.append({"seq": int(f[0]), "elapsed_ms": int(f[1]), "step": f[2],
                            "count": int(f[3]), "total": int(f[4])})
        except ValueError:
            continue
    return records


class Mail:
    """One request: its id, its folder and what it exchanges."""

    def __init__(self, root, command: str, params: dict, files: dict = None, request_id: str = None):
        self.root = Path(root)
        self.id = request_id or new_id()
        self.command = command
        self.params = params
        self.files = files or {}
        self.folder = self.root / MAIL / self.id
        self.entry = self.root / QUEUE / f"{self.id}.req"

    def post(self):
        """Write the request's folder, then queue it: the entry appears only once everything it names is there."""
        self.folder.mkdir(parents=True, exist_ok=True)
        for name, text in self.files.items():
            if isinstance(text, bytes):                  # as given (an OutJob Altium reads in the ANSI code page)
                (self.folder / name).write_bytes(text)
            else:
                write_altium_text(self.folder / name, text)
        request = {"command": self.command, "request_id": self.id, "sender_pid": sender_pid(self.id), **self.params}
        # UTF-8 with a byte-order mark and the text unescaped: every value reaches the script as typed
        write_altium_text(self.folder / "request.json", json.dumps(request, indent=2, ensure_ascii=False))
        self.entry.parent.mkdir(parents=True, exist_ok=True)
        staging = self.entry.with_suffix(".tmp")
        staging.write_text(self.id, encoding="ascii")
        os.replace(staging, self.entry)

    def taken(self) -> bool:
        return (self.folder / "taken").exists()

    def withdraw(self) -> bool:
        """Take the request back from the queue: True when it was still waiting, False when the bridge has it."""
        try:
            self.entry.unlink()
            (self.folder / "withdrawn").write_text(time.ctime(), encoding="ascii")
            return True
        except FileNotFoundError:
            return False

    def progress_signature(self):
        """The progress file's (mtime, size), read without opening it, or None: the bridge appends every record
        through the one handle it holds for the request, so the file is read only when this changed."""
        try:
            st = os.stat(self.folder / "progress.log")
            return st.st_mtime_ns, st.st_size
        except OSError:
            return None

    def records(self) -> list:
        try:
            return parse_records(read_altium_text(self.folder / "progress.log"))
        except OSError:
            return []

    def answer(self):
        """The bridge's answer as text, or None while there is none."""
        try:
            return read_altium_text(self.folder / "response.json")
        except FileNotFoundError:
            return None

    def file(self, name: str) -> Path:
        return self.folder / name


def expire_others(root, own_id: str) -> list:
    """Under the lock, every queued request but our own was left by a server that no longer holds it (gave up or
    died): move each into its folder as 'expired', so no later bridge run takes it. Returns their ids."""
    queue = Path(root) / QUEUE
    gone = []
    for entry in sorted(queue.glob("*.req")) if queue.is_dir() else ():
        rid = entry.stem
        if rid == own_id:
            continue
        target = Path(root) / MAIL / rid
        try:
            target.mkdir(parents=True, exist_ok=True)
            os.replace(entry, target / "expired")
            gone.append(rid)
        except OSError:
            pass
    return gone


def prune(root, keep_seconds: float = KEEP_SECONDS, now: float = None):
    """Delete request folders older than keep_seconds."""
    mail = Path(root) / MAIL
    now = time.time() if now is None else now
    for folder in mail.iterdir() if mail.is_dir() else ():
        try:
            if folder.is_dir() and now - folder.stat().st_mtime > keep_seconds:
                shutil.rmtree(folder, ignore_errors=True)
        except OSError:
            pass


def write_status(root, status: dict):
    """status.json, replaced whole so a reader never sees half of it."""
    path = Path(root) / STATUS
    staging = path.with_name(f"status.{os.getpid()}.tmp")
    try:
        staging.write_text(json.dumps(status, indent=2, ensure_ascii=False), encoding="utf-8")
        os.replace(staging, path)
    except OSError:
        pass


async def wait(mail: Mail, stall: float, on_progress=None, poll: float = 0.25, clock=time.monotonic,
               sleep=asyncio.sleep, watch=None, watch_every: float = 2.0, after_watch: float = 10.0):
    """Wait for the bridge's answer to this request. Returns ("answered", text) when response.json lands, or
    ("stalled", details) when nothing happened for `stall` seconds: not taken from the queue, or taken and no
    progress record since. Every new record goes to on_progress(record, records) first. Every `watch_every`
    seconds watch() looks at Altium; when it reports that something held the script (a box it closed), the
    wait gives the script `after_watch` more seconds to report instead of the rest of the stall time."""
    last_change = clock()
    seen_seq, taken, signature = 0, False, None
    next_watch = clock() + watch_every
    records = []
    while True:
        text = mail.answer()
        if text is not None:
            return "answered", text
        if not taken and mail.taken():
            taken, last_change = True, clock()
        current = mail.progress_signature()
        if current is not None and current != signature:
            signature = current
            records = mail.records() or records
        newest = records[-1]["seq"] if records else 0
        if newest > seen_seq:
            seen_seq, last_change = newest, clock()
            if on_progress is not None:
                await on_progress(records[-1], records)
        if watch is not None and clock() >= next_watch:
            next_watch = clock() + watch_every
            if await watch():
                stall = min(stall, clock() - last_change + after_watch)
        if clock() - last_change >= stall:
            return "stalled", {"taken": taken or bool(records), "last_record": records[-1] if records else None,
                               "records": len(records), "seconds": stall}
        await sleep(poll)
