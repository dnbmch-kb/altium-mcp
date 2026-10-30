"""One script at a time in the running Altium, across every tool that drives it.

Altium runs one script at a time; a RunScript sent while another runs is lost or interleaves with it. This server,
its dev sandbox and the runner outside this repo (c:/Projects/tools/altium/altium_run.py, through
c:/Projects/tools/altium/altium_lock.py, the same protocol) each hold this lock from a
RunScript until its output arrives: the file C:/Users/Public/altium_mcp/altium_script.lock, created exclusively,
holding the holder's pid, what it runs and since when. A holder whose process has exited left a stale file, which
the next taker removes. A run of several scripts holds Altium whole (altium_lock.run_hold, board_build.py): the
file altium_run.hold beside the lock carries the run's pid and a token its child processes inherit in ALTIUM_RUN_HOLD;
while that run lives, a taker without the token meets the hold as the holder. Kept here as well so the server stays
self-contained.
"""
import ctypes
import json
import os
import time
from pathlib import Path

LOCK = Path("C:/Users/Public/altium_mcp/altium_script.lock")
UNWRITTEN_GRACE = 10       # seconds an unreadable lock file may be in the middle of being written
HOLD = LOCK.with_name("altium_run.hold")
HOLD_ENV = "ALTIUM_RUN_HOLD"     # the hold's token, inherited by the holding run's children


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


def hold_holder():
    """The live run hold of another run, None when there is none or this process carries its token; a stale hold
    (its process gone) is removed."""
    try:
        rec = json.loads(HOLD.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        try:
            if time.time() - HOLD.stat().st_mtime < UNWRITTEN_GRACE:
                return {"owner": "a run writing its hold"}
        except FileNotFoundError:
            return None
        rec = {"pid": 0}
    if rec.get("token") and rec["token"] == os.environ.get(HOLD_ENV):
        return None
    if alive(rec.get("pid", 0)):
        return dict(rec, owner=f"a run's hold: {rec.get('owner', '?')}")
    HOLD.unlink(missing_ok=True)
    return None


def try_take(owner):
    """Take the lock: None when taken, else the live holder's record - the lock's, or a run hold's this process
    does not carry."""
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    if (hold := hold_holder()) is not None:
        return hold
    while True:
        try:
            with open(LOCK, "x", encoding="utf-8") as f:
                json.dump(dict(pid=os.getpid(), owner=owner, since=time.time()), f)
            return None
        except FileExistsError:
            try:
                holder = json.loads(LOCK.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                if time.time() - LOCK.stat().st_mtime < UNWRITTEN_GRACE:
                    return {"owner": "a runner writing the lock"}
                holder = {"pid": 0}
            if alive(holder.get("pid", 0)):
                return holder
            LOCK.unlink(missing_ok=True)             # stale: its process is gone


def release():
    try:
        if json.loads(LOCK.read_text(encoding="utf-8")).get("pid") == os.getpid():
            LOCK.unlink()
    except (OSError, ValueError):
        pass


def take(owner, wait):
    """Blocking take for scripts: None when taken, else the holder that kept it past `wait` seconds."""
    deadline = time.monotonic() + wait
    while (holder := try_take(owner)) is not None:
        if time.monotonic() >= deadline:
            return holder
        time.sleep(0.5)
    return None
