"""The guards that keep a test of the server's call path off the running Altium and the jobs it serves."""
from unittest import mock

import main
from main import altium_lock


def offline(root):
    """The one-script lock, its hold and queue and the resident bridge's files in `root`, never the live ones; no
    Altium running as the tools' bridge sees it and no box of Altium's seen by a call, so no form or box is closed and
    no Stop line or bridge start goes out; any process start fails the test; Altium never read as not responding.
    -> the patches, for start() and stop()."""
    return (mock.patch.multiple(altium_lock, LOCK=root / "altium_script.lock", HOLD=root / "altium_run.hold",
                                hung_windows=lambda: []),
            mock.patch.multiple(main.resident, MAILBOX=root, DIR=root / "resident",
                                BEAT=root / "resident" / "heartbeat.txt", START=root / "resident" / "start.txt",
                                WANTED=root / "resident" / "wanted"),
            mock.patch.multiple(main.resident.altium_run, altium_pids=lambda: set(),
                                start=mock.Mock(side_effect=AssertionError("unexpected process start"))),
            mock.patch.object(main, "x2_windows", lambda classes=None: []))
