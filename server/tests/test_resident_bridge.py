"""The tools' resident bridge as this server meets it (resident_bridge.py), offline: its form closed before a RunScript,
one a runtime error may have paused stopped first - its Error box closed, EditScript:Stop waited for and given time to
land - and its form closed only when it still stands and is no longer busy: a WM_CLOSE under a paused script brings
Altium's Access violation in ScriptingSystem.DLL (2026-11-03).

Run from server/:  python -m unittest tests.test_resident_bridge -v
"""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import resident_bridge  # noqa: E402

FORM = (77, 1, resident_bridge.FORM, resident_bridge.CAPTION, True)     # shown without its parent: a top-level window
ERROR = (5, 1, "TMessageForm", "Error", True)


class YieldTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name) / "resident"
        isolate = mock.patch.multiple(resident_bridge, DIR=root, BEAT=root / "heartbeat.txt", WANTED=root / "wanted")
        isolate.start()
        self.addCleanup(isolate.stop)
        self.events, self.lines = [], []
        self.launcher = mock.Mock()
        self.launcher.wait.side_effect = lambda timeout: self.events.append(("launcher waited", timeout))

    def stop(self):
        self.events.append("EditScript:Stop")
        return self.launcher

    def yield_(self, shown, dead_already=False, beat_age=0, busy="0"):
        windows = iter(shown)
        user32 = mock.Mock()
        user32.PostMessageW.side_effect = lambda h, msg, w, l: self.events.append(("WM_CLOSE", h))
        b = {"state": "idle", "folder": "f", "age": beat_age, "busy": busy}
        with mock.patch.object(resident_bridge, "top_windows", side_effect=lambda: next(windows, [])), \
             mock.patch.object(resident_bridge, "beat", return_value=b), \
             mock.patch.object(resident_bridge.ctypes, "windll", mock.Mock(user32=user32), create=True), \
             mock.patch.object(resident_bridge.time, "sleep", side_effect=lambda s: self.events.append(("sleep", s))):
            return resident_bridge.yield_(self.lines.append, self.stop, dead_already)

    def test_one_that_raised_has_its_box_closed_and_is_stopped_with_its_form_gone(self):
        self.assertFalse(self.yield_([[ERROR], []], dead_already=True))
        self.assertEqual(self.events, [("WM_CLOSE", 5), "EditScript:Stop",
                                       ("launcher waited", resident_bridge.STOP_WAIT),
                                       ("sleep", resident_bridge.STOP_SETTLE)])

    def test_a_dead_ones_box_closed_then_its_script_stopped_and_its_form_gone_with_it(self):
        self.assertTrue(self.yield_([[FORM, ERROR], [FORM], []], beat_age=resident_bridge.STALE + 1))
        self.assertEqual(self.events, [("WM_CLOSE", 5), "EditScript:Stop",
                                       ("launcher waited", resident_bridge.STOP_WAIT),
                                       ("sleep", resident_bridge.STOP_SETTLE)])     # no WM_CLOSE of its form

    def test_a_form_the_stop_left_and_no_longer_busy_is_closed_after_the_stop(self):
        self.assertTrue(self.yield_([[FORM, ERROR], [FORM], [FORM], [FORM], []], beat_age=resident_bridge.STALE + 1))
        self.assertEqual(self.events, [("WM_CLOSE", 5), "EditScript:Stop",
                                       ("launcher waited", resident_bridge.STOP_WAIT),
                                       ("sleep", resident_bridge.STOP_SETTLE), ("WM_CLOSE", 77)])

    def test_a_busy_one_is_stopped_and_its_form_never_closed_while_still_busy(self):
        self.assertTrue(self.yield_([[FORM], [FORM], [FORM], [FORM]], busy="1500"))
        self.assertEqual(self.events, ["EditScript:Stop", ("launcher waited", resident_bridge.STOP_WAIT),
                                       ("sleep", resident_bridge.STOP_SETTLE)])
        self.assertIn("still busy after EditScript:Stop", self.lines[-1])

    def test_a_live_one_is_closed_and_not_stopped(self):
        self.assertTrue(self.yield_([[FORM], [FORM], []]))
        self.assertEqual(self.events, [("WM_CLOSE", 77)])

    def test_no_form_and_no_raise_is_nothing(self):
        self.assertFalse(self.yield_([[]]))
        self.assertEqual(self.events, [])

    def test_a_launcher_still_running_is_said_and_the_stop_still_settled(self):
        self.launcher.wait.side_effect = subprocess.TimeoutExpired("X2.EXE", resident_bridge.STOP_WAIT)
        self.yield_([[]], dead_already=True)
        self.assertEqual(self.events, ["EditScript:Stop", ("sleep", resident_bridge.STOP_SETTLE)])
        self.assertIn("EditScript:Stop's launcher still runs", self.lines[0])

    def test_the_servers_stop_returns_its_launcher(self):
        import main
        with mock.patch.object(main.subprocess, "Popen", return_value="launcher") as popen:
            self.assertEqual(main.altium_bridge.stop_scripting(), "launcher")
        self.assertIn("-REditScript:Stop", popen.call_args[0][0])


class HolderTest(unittest.TestCase):
    """The bridge reads the lock's holder from the listing of the folder holder beside the lock, never from a file a
    runner writes: its open of the lock while a taker wrote it was refused, EFOpenError's box pausing the bridge
    (2026-11-03). This server names itself there while it holds the lock, and nobody once it lets it go."""

    def setUp(self):
        import script_lock
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        isolate = mock.patch.multiple(script_lock, LOCK=root / "altium_script.lock", HOLD=root / "altium_run.hold")
        isolate.start()
        self.addCleanup(isolate.stop)
        self.lock, self.holder = script_lock, root / "holder"

    def test_named_while_held_and_not_after(self):
        self.holder.mkdir()
        (self.holder / "123.pid").touch()                   # a dead holder's, never released
        self.assertIsNone(self.lock.try_take("test"))
        self.assertEqual(sorted(p.name for p in self.holder.glob("*.pid")), [f"{os.getpid()}.pid"])
        self.lock.release()
        self.assertEqual(list(self.holder.glob("*.pid")), [])


class BenignTest(unittest.TestCase):
    """The window a request names as no dialog, read as the bridge compares Application.ActiveFormHandle: it deferred
    every request while Altium stood in the background, under Altium's own application window (2026-11-03)."""
    MAIN = (12846206, 1, resident_bridge.MAIN, "X.PrjPcb - Altium Designer", True)
    APP = (30810610, 1, resident_bridge.APPLICATION, "", True)

    def benign(self, top, active):
        with mock.patch.object(resident_bridge, "active_window", return_value=active):
            return resident_bridge.benign(top)

    def test_the_active_window_while_the_main_window_is_enabled(self):
        self.assertEqual(self.benign([self.MAIN, self.APP], 777), "777")

    def test_the_application_window_while_altium_holds_none_active(self):
        self.assertEqual(self.benign([self.MAIN, self.APP], 0), "30810610")

    def test_none_under_a_modal_dialog_or_without_a_main_window(self):
        self.assertEqual(self.benign([(12846206, 1, resident_bridge.MAIN, "X", False), self.APP], 0), "0")
        self.assertEqual(self.benign([self.APP], 0), "0")

    def test_as_the_bridge_compares_it(self):
        self.assertEqual(self.benign([self.MAIN, self.APP], 0x80000001), "-2147483647")


if __name__ == "__main__":
    unittest.main()
