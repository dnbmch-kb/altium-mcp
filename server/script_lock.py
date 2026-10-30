"""One script at a time in the running Altium, across every tool that drives it.

Altium runs one script at a time; a RunScript sent while another runs is lost or interleaves with it. This server,
its dev sandbox and the runner outside this repo (c:/Projects/tools/altium/altium_run.py, through
c:/Projects/tools/altium/altium_lock.py, the same protocol) each hold this lock from a
RunScript until its output arrives: the file C:/Users/Public/altium_mcp/altium_script.lock, created exclusively,
holding the holder's pid, what it runs and since when. A lock that holds no more is stale, and the next taker removes
it; one whose record names the script its runner sent (`done`, `x2`: altium_lock.script) holds while that script may
still run - its done file not written since and one of those X2.EXE running - whether its runner lives or not. A run
of several scripts holds Altium whole (altium_lock.run_hold, board_build.py): the file altium_run.hold beside the lock
carries the run's pid and a token its child processes inherit in ALTIUM_RUN_HOLD; while that run lives, a taker
without the token meets the hold as the holder.

A taker that waits registers as a waiter: one line in queue.txt beside the lock, `who pid N: what (waiting since
...)`, its pid in ALTIUM_QUEUE for the processes it starts. The slot goes in the queue's order: while a live waiter's
line stands first, nobody else takes the lock - a process carrying the live hold's token excepted - and the waiter
(or a process it started) that takes it removes its line. A waiter whose process is gone is dropped by the next
reader; lines of another form are kept and only listed. Kept here as well so the server stays self-contained.
"""
import contextlib
import ctypes
import ctypes.wintypes as W
import json
import msvcrt
import os
import re
import sys
import time
from pathlib import Path

LOCK = Path("C:/Users/Public/altium_mcp/altium_script.lock")
UNWRITTEN_GRACE = 10       # seconds an unreadable lock file may be in the middle of being written
HOLD = LOCK.with_name("altium_run.hold")
HOLD_ENV = "ALTIUM_RUN_HOLD"     # the hold's token, inherited by the holding run's children
QUEUE_ENV = "ALTIUM_QUEUE"       # the waiters a process waits for: its own pid and its parents', inherited
WAITER = re.compile(r"^(?P<who>.*?) ?pid (?P<pid>\d+): (?P<what>.*) "
                    r"\(waiting since (?P<since>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d)\)$")
SINCE = "%Y-%m-%d %H:%M:%S"


def describe(holder):
    return (f"Altium is running another script: {holder.get('owner', '?')} (pid {holder.get('pid', '?')}, "
            f"since {time.ctime(holder.get('since', 0))})")


def alive(pid):
    k = ctypes.windll.kernel32
    handle = k.OpenProcess(0x1000, False, pid)      # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    code = ctypes.c_ulong()
    k.GetExitCodeProcess(handle, ctypes.byref(code))
    k.CloseHandle(handle)
    return code.value == 259                         # STILL_ACTIVE


def x2_alive(pid):
    """An X2.EXE runs under this pid: the process lives and its image is X2.EXE, not a later process given the pid."""
    if not alive(pid):
        return False
    k = ctypes.windll.kernel32
    handle = k.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    buf, size = ctypes.create_unicode_buffer(1024), W.DWORD(1024)
    ok = k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
    k.CloseHandle(handle)
    return bool(ok) and os.path.basename(buf.value).upper() == "X2.EXE"


def read_record(path, unwritten):
    """A lock or hold file's record: None when there is none, {"owner": unwritten} while it is being written,
    {"pid": 0} when it cannot be read."""
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        try:
            if time.time() - path.stat().st_mtime < UNWRITTEN_GRACE:
                return {"owner": unwritten}
        except FileNotFoundError:
            return None
        return {"pid": 0}


def holds(rec):
    """Does a lock record still hold: one being written does, so does one whose process lives, and one naming the
    script its runner sent while that script may still run (tools/altium/altium_lock.py holds)."""
    if "pid" not in rec or alive(rec["pid"] or 0):
        return True
    if not rec.get("done"):
        return False
    try:
        done = os.stat(rec["done"])
        if done.st_size and done.st_mtime >= rec.get("since", 0):
            return False
    except OSError:
        pass
    return any(x2_alive(pid) for pid in rec.get("x2", ()))


# ---------------------------------------------------------------- the queue

def queue_file():
    return HOLD.with_name("queue.txt")


def waiter(line):
    """A waiter's line read: {who, pid, what, since (epoch)}; None for a line of another form."""
    if not (m := WAITER.match(line.strip())):
        return None
    try:
        since = time.mktime(time.strptime(m["since"], SINCE))
    except ValueError:
        return None
    return dict(who=m["who"], pid=int(m["pid"]), what=m["what"], since=since)


def mine():
    """The waiters this process stands for: itself and the waiters that started it (ALTIUM_QUEUE)."""
    return {os.getpid()} | {int(p) for p in os.environ.get(QUEUE_ENV, "").split(",") if p.isdigit()}


def queued():
    try:
        return queue_file().read_text(encoding="utf-8-sig").splitlines()
    except FileNotFoundError:
        return []


@contextlib.contextmanager
def queue_edit():
    """The queue's lines for one change, under queue.lock's byte lock; written back whole (beside, then renamed over
    it) when changed."""
    path = queue_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path.with_name("queue.lock"), os.O_RDWR | os.O_CREAT)
    try:
        while True:
            try:
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                break
            except OSError:
                time.sleep(0.02)
        try:
            lines = queued()
            before = list(lines)
            yield lines
            if lines != before:
                spare = path.with_name(f"{path.name}.{os.getpid()}.tmp")
                spare.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
                for attempt in range(50):
                    try:
                        os.replace(spare, path)
                        break
                    except PermissionError:      # a reader holding it open for a moment
                        if attempt == 49:
                            spare.unlink(missing_ok=True)
                            raise
                        time.sleep(0.02)
        finally:
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)


def drop(pids):
    if not any((w := waiter(line)) and w["pid"] in pids for line in queued()):
        return
    with queue_edit() as lines:
        lines[:] = [line for line in lines if not ((w := waiter(line)) and w["pid"] in pids)]


def waiting(what):
    """This process waits its turn: its line (who, what, since) at the queue's end and its pid in ALTIUM_QUEUE; once,
    while its line stands."""
    me = os.getpid()
    project = os.path.basename(os.environ.get("CLAUDE_PROJECT_DIR", "").rstrip("/\\"))
    who = " ".join(p for p in (project, "altium-mcp", os.path.basename(sys.argv[0]) if sys.argv else "") if p)
    with queue_edit() as lines:
        if not any((w := waiter(line)) and w["pid"] == me for line in lines):
            lines.append(f"{who} pid {me}: {' '.join(str(what).split())} (waiting since {time.strftime(SINCE)})")
    chain = [p for p in os.environ.get(QUEUE_ENV, "").split(",") if p]
    if str(me) not in chain:
        os.environ[QUEUE_ENV] = ",".join(chain + [str(me)])


def leave():
    """This process's line out of the queue: its wait given up."""
    drop({os.getpid()})


def ahead():
    """The queue's first live waiter, as a holder's record, when this process does not stand for it; None when the
    queue is empty or its turn is this process's. Lines of waiters whose process is gone are removed."""
    me, dead = mine(), set()
    for w in filter(None, map(waiter, queued())):
        if not alive(w["pid"]):
            dead.add(w["pid"])
            continue
        if dead:
            drop(dead)
        return None if w["pid"] in me else dict(owner=f"next in the queue, {w['who']}: {w['what']}", pid=w["pid"],
                                                 since=w["since"])
    if dead:
        drop(dead)
    return None


# ---------------------------------------------------------------- the lock

def hold_holder():
    """The live run hold of another run, None when there is none or this process carries its token; a stale hold
    (its process gone) is removed."""
    rec = read_record(HOLD, "a run writing its hold")
    if rec is None or (rec.get("token") and rec["token"] == os.environ.get(HOLD_ENV)):
        return None
    if "pid" not in rec or alive(rec["pid"]):
        return dict(rec, owner=f"a run's hold: {rec.get('owner', '?')}")
    HOLD.unlink(missing_ok=True)
    return None


def carries_hold():
    token = os.environ.get(HOLD_ENV)
    return bool(token) and (read_record(HOLD, "") or {}).get("token") == token


def try_take(owner):
    """Take the lock: None when taken, else the live holder's record - the lock's, a run hold's this process does
    not carry, or the queue's first waiter when that is not this process."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if (hold := hold_holder()) is not None:
        return hold
    if not carries_hold() and (first := ahead()) is not None:
        return first
    while True:
        try:
            with open(LOCK, "x", encoding="utf-8") as f:
                json.dump(dict(pid=os.getpid(), owner=owner, since=time.time()), f)
            drop(mine())                             # its turn came: its line and its starters' leave the queue
            return None
        except FileExistsError:
            holder = read_record(LOCK, "a runner writing the lock")
            if holder is not None and holds(holder):
                return holder
            LOCK.unlink(missing_ok=True)             # stale: its process and its script are gone


def release():
    try:
        if json.loads(LOCK.read_text(encoding="utf-8")).get("pid") == os.getpid():
            LOCK.unlink()
    except (OSError, ValueError):
        pass


def take(owner, wait):
    """Blocking take for scripts: None when taken, else the holder that kept it past `wait` seconds; waiting in the
    queue meanwhile."""
    deadline, waited = time.monotonic() + wait, False
    while (holder := try_take(owner)) is not None:
        if time.monotonic() >= deadline:
            if waited:
                leave()
            return holder
        if not waited:
            waiting(owner)
            waited = True
        time.sleep(0.5)
    return None
