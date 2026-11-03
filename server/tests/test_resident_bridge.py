"""The tools' resident bridge as this server meets it (resident_bridge.py), offline: its form closed before a RunScript,
a dead one's script stopped and the stop given time to land before the restart's RunScript - the restart after a raise
met TShowException "Error" while its RunScript raced an EditScript:Stop sent and not waited for (2026-11-03).

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

    def yield_(self, shown, dead_already=False, beat_age=0):
        windows = iter(shown)
        user32 = mock.Mock()
        user32.PostMessageW.side_effect = lambda h, msg, w, l: self.events.append(("WM_CLOSE", h))
        with mock.patch.object(resident_bridge, "top_windows", side_effect=lambda: next(windows, [])), \
             mock.patch.object(resident_bridge, "beat", return_value={"state": "idle", "folder": "f", "age": beat_age}), \
             mock.patch.object(resident_bridge.ctypes, "windll", mock.Mock(user32=user32), create=True), \
             mock.patch.object(resident_bridge.time, "sleep", side_effect=lambda s: self.events.append(("sleep", s))):
            return resident_bridge.yield_(self.lines.append, self.stop, dead_already)

    def test_one_that_raised_is_stopped_with_its_form_gone_and_the_stop_given_time(self):
        self.assertFalse(self.yield_([[ERROR]], dead_already=True))
        self.assertEqual(self.events, ["EditScript:Stop", ("launcher waited", resident_bridge.STOP_WAIT),
                                       ("sleep", resident_bridge.STOP_SETTLE)])

    def test_a_dead_ones_box_and_form_closed_then_its_script_stopped_and_settled(self):
        self.assertTrue(self.yield_([[FORM, ERROR], []], beat_age=resident_bridge.STALE + 1))
        self.assertEqual(self.events, [("WM_CLOSE", 5), ("WM_CLOSE", 77), "EditScript:Stop",
                                       ("launcher waited", resident_bridge.STOP_WAIT),
                                       ("sleep", resident_bridge.STOP_SETTLE)])

    def test_a_live_one_is_closed_and_not_stopped(self):
        self.assertTrue(self.yield_([[FORM], []]))
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


if __name__ == "__main__":
    unittest.main()
