"""The resident bridge, as this server meets it: the tools' script kept loaded in the running Altium
(c:/Projects/tools/altium/resident.py starts and stops it; its protocol in tools altium/resident/Resident.pas), whose
units are this repo's AltiumScript units and its own, serving the mailbox's requests from a timer with no RunScript.

While its form stands Altium refuses every other script ("Another script executing now"), so a call goes through it
when it holds this server's units as they are on disk (the units.txt of the folder its heartbeat names) and answers a
no-op within ANSWER seconds (serves); otherwise its form is closed first (yield_: WM_CLOSE ends its script; a dead
one's script Error box closed and the script stopped) and the call goes today's way, a RunScript of Altium_API>Run,
and the bridge is started again before the lock is released while it is wanted (restart: the tools' starter, run
with --held under this server's lock). Every request names `benign`, the window Altium's main thread holds active
while its main window is enabled ('0' while a modal dialog is up): the bridge takes a request only while that window,
its main window, its own or none is active, so it never runs one under a dialog. Kept here, not imported from the
tools, so the server stays self-contained; the layout is the tools' (DIR, the heartbeat, the form's class and
caption).
"""
import configparser
import ctypes
import ctypes.wintypes as W
import hashlib
import os
import re
import subprocess
import sys
import time
from pathlib import Path

DIR = Path("C:/Users/Public/altium_mcp/resident")
BEAT, WANTED = DIR / "heartbeat.txt", DIR / "wanted"
STARTER = Path("C:/Projects/tools/altium/resident.py")
FORM, CAPTION, APPLICATION, MAIN = "TScriptForm", "resident bridge", "TApplication", "TDocumentForm"
ANSWER = 0.2            # seconds a no-op may take before the bridge counts as not answering
STALE = 3               # seconds without a heartbeat (written each second while idle) before the bridge is dead
GONE_WAIT = 5           # seconds a closed form is given to go
STOP_WAIT = 30          # seconds EditScript:Stop's launcher is given to hand its line to Altium
STOP_SETTLE = 1         # seconds after that before the next RunScript: one sent sooner lands before the stop, which
                        # then ends it (TShowException "Error" at the restart after a raise, 2026-11-03)
START_WAIT = 90         # seconds the starter is given
WM_CLOSE = 0x0010
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def units(project):
    """This server's script units as the bridge would load them: {name: sha1 of its bytes} for every document of
    the project (Altium_API.PrjScr)."""
    project = Path(project)
    cfg = configparser.ConfigParser(interpolation=None, strict=False)
    cfg.read_string(project.read_bytes().removeprefix(b"\xef\xbb\xbf").decode("latin-1"))
    names = [cfg[s]["DocumentPath"] for s in cfg.sections() if re.fullmatch(r"Document\d+", s)]
    return {n: hashlib.sha1((project.parent / n).read_bytes()).hexdigest() for n in names}


def beat(now=None):
    """The heartbeat as the bridge last wrote it, {state, ..., folder, age}, or None: the bridge writes it whole and
    renames it into place, so a read between its delete and its rename finds none and tries again."""
    for _ in range(3):
        try:
            age = (time.time() if now is None else now) - os.stat(BEAT).st_mtime
            line = BEAT.read_text(encoding="latin-1").strip()
        except (FileNotFoundError, PermissionError):
            time.sleep(0.02)
            continue
        fields = dict(part.split(" ", 1) if " " in part else (part, "") for part in line.split("|"))
        if "state" in fields and "folder" in fields:
            return dict(fields, age=age)
        time.sleep(0.02)
    return None


def holds(mine, b=None):
    """Does the loaded bridge hold every unit `mine` names ({name: sha1}) with the same text?"""
    b = beat() if b is None else b
    if not b:
        return False
    try:
        lines = (Path(b["folder"]) / "units.txt").read_text(encoding="ascii").splitlines()
    except OSError:
        return False
    loaded = dict(line.split(" ", 1) for line in lines if " " in line)
    return all(loaded.get(name) == digest for name, digest in mine.items())


def _image(pid):
    k = ctypes.windll.kernel32
    handle = k.OpenProcess(0x1000, False, pid)                     # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    buf, size = ctypes.create_unicode_buffer(1024), W.DWORD(1024)
    ok = k.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size))
    k.CloseHandle(handle)
    return os.path.basename(buf.value) if ok else ""


def top_windows():
    """Every visible top-level window of an X2.EXE: [(hwnd, pid, class, title, enabled)]."""
    u, found, images = ctypes.windll.user32, [], {}

    def cb(h, _):
        if u.IsWindowVisible(h):
            pid = W.DWORD()
            u.GetWindowThreadProcessId(h, ctypes.byref(pid))
            if pid.value not in images:
                images[pid.value] = _image(pid.value).upper() == "X2.EXE"
            if images[pid.value]:
                cls, title = ctypes.create_unicode_buffer(256), ctypes.create_unicode_buffer(512)
                u.GetClassNameW(h, cls, 256)
                u.GetWindowTextW(h, title, 512)
                found.append((h, pid.value, cls.value, title.value, bool(u.IsWindowEnabled(h))))
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, W.HWND, W.LPARAM)(cb), 0)
    return found


def window(top=None):
    """The bridge's form: TScriptForm "resident bridge", a child of Altium's application window (or a top-level
    window of an X2.EXE); None when there is none."""
    u = ctypes.windll.user32
    u.FindWindowExW.restype, u.FindWindowExW.argtypes = W.HWND, (W.HWND, W.HWND, W.LPCWSTR, W.LPCWSTR)
    for h, _, cls, title, _ in top_windows() if top is None else top:
        if cls == APPLICATION and (child := u.FindWindowExW(h, None, FORM, CAPTION)):
            return child
        if cls == FORM and title == CAPTION:
            return h
    return None


def benign(top=None):
    """The window Altium's main thread holds active (GetGUIThreadInfo's hwndActive, the bridge's
    Application.ActiveFormHandle) as a 32-bit signed integer, while its main window is enabled; '0' while a modal
    dialog has it disabled, or there is none."""
    mains = [w for w in (top_windows() if top is None else top) if w[2] == MAIN]
    if not mains or not all(w[4] for w in mains):
        return "0"

    class GUITHREADINFO(ctypes.Structure):
        _fields_ = [("cbSize", W.DWORD), ("flags", W.DWORD), ("hwndActive", W.HWND), ("hwndFocus", W.HWND),
                    ("hwndCapture", W.HWND), ("hwndMenuOwner", W.HWND), ("hwndMoveSize", W.HWND),
                    ("hwndCaret", W.HWND), ("rcCaret", W.RECT)]
    u = ctypes.windll.user32
    u.GetWindowThreadProcessId.argtypes = (W.HWND, ctypes.POINTER(W.DWORD))
    thread = u.GetWindowThreadProcessId(mains[0][0], None)
    info = GUITHREADINFO(cbSize=ctypes.sizeof(GUITHREADINFO))
    if not thread or not u.GetGUIThreadInfo(thread, ctypes.byref(info)):
        return "0"
    return str(ctypes.c_int32(info.hwndActive or 0).value)


def answers(mailbox, root, within=ANSWER):
    """Does the bridge answer a no-op within `within` seconds (under this server's lock)? `mailbox` is the server's
    mailbox module, `root` its folder; a no-op not answered by then is withdrawn."""
    t0 = time.monotonic()
    mail = mailbox.Mail(root, "resident_noop", {"benign": benign()})
    mail.post()
    while time.monotonic() - t0 < within:
        try:
            if mail.answer() is not None:
                return True
        except PermissionError:                     # renamed into place a moment ago
            pass
        time.sleep(0.002)
    mail.withdraw()
    return False


def dead(b=None):
    """The bridge's form stands but it has not beaten for STALE seconds."""
    b = beat() if b is None else b
    return b is None or b["age"] > STALE


def stopped(log, stop):
    """A script stopped: `stop()` sends EditScript:Stop and returns its launcher, which is waited for (it exits once
    Altium has the line), then STOP_SETTLE seconds, so the RunScript sent next is not the one the stop ends."""
    launcher = stop()
    try:
        launcher.wait(STOP_WAIT)
    except subprocess.TimeoutExpired:
        log(f"resident bridge: EditScript:Stop's launcher still runs after {STOP_WAIT}s")
    time.sleep(STOP_SETTLE)


def yield_(log, stop, dead_already=False):
    """The bridge out of the way of a RunScript, under this server's lock: a dead one's (or `dead_already`, one whose
    request raised) script Error box closed, its form closed by WM_CLOSE and waited for until it is gone, and a dead
    one's script stopped (stopped: `stop`, the caller's EditScript:Stop returning its launcher) - with `dead_already`
    even when its form is gone. True when a form was there."""
    top = top_windows()
    h = window(top)
    if not h:
        if dead_already:
            stopped(log, stop)
        return False
    gone_dead = dead_already or dead()
    u = ctypes.windll.user32
    if gone_dead:
        for box, _, cls, title, _ in top:
            if cls == "TMessageForm" and title == "Error":
                log(f"resident bridge dead: its script Error box {box} closed")
                u.PostMessageW(box, WM_CLOSE, 0, 0)
    u.PostMessageW(h, WM_CLOSE, 0, 0)
    t0 = time.monotonic()
    while window() and time.monotonic() - t0 < GONE_WAIT:
        time.sleep(0.02)
    if gone_dead:
        stopped(log, stop)
    log(f"resident bridge {'dead' if gone_dead else 'loaded'}: its form closed in {time.monotonic() - t0:.2f}s")
    return True


def wanted():
    return WANTED.exists()


def restart(log, env=None):
    """The bridge started again under this server's lock while it is wanted: the tools' starter, `start --held`;
    True when it says so."""
    if not wanted() or not STARTER.is_file():
        return False
    try:
        r = subprocess.run([sys.executable, str(STARTER), "start", "--held"], capture_output=True, text=True,
                           timeout=START_WAIT, env=env, creationflags=NO_WINDOW)
    except subprocess.TimeoutExpired:
        log(f"resident bridge: its starter gave no answer in {START_WAIT}s")
        return False
    log(f"resident bridge start: exit {r.returncode}: {(r.stdout + r.stderr).strip()[-300:]}")
    return r.returncode == 0
