"""The tools' resident bridge as this server meets it (c:/Projects/tools/altium/resident.py, imported by main.py),
offline: a call goes through it only while it holds this server's script units as they are on disk; after a call made
today's way it is started again only while it is wanted, and a start that fails leaves the call's own result. Its
yield, its state and the window a request is served under are the tools' own, pinned by tools altium/test_resident.py.

Run from server/:  python -m unittest tests.test_resident -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import main  # noqa: E402
from tests.offline import offline  # noqa: E402


class Fixture(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        for guard in offline(self.root):
            guard.start()
            self.addCleanup(guard.stop)
        main.resident.DIR.mkdir()
        self.lines = []


class HeldTest(Fixture):
    def setUp(self):
        super().setUp()
        self.project = self.root / "AltiumScript" / "Altium_API.PrjScr"
        self.project.parent.mkdir()
        self.project.write_bytes(b"\xef\xbb\xbf[Design]\r\nVersion=1.0\r\n\r\n[Document1]\r\nDocumentPath=Altium_API.pas"
                                 b"\r\n\r\n[Document2]\r\nDocumentPath=pcb_utils.pas\r\n")
        for name in ("Altium_API.pas", "pcb_utils.pas"):
            (self.project.parent / name).write_bytes(f"// {name}\r\n".encode("ascii"))
        self.folder = main.resident.DIR / "0123456789abcdef"     # a staged version, as its heartbeat names it
        self.folder.mkdir()

    def load(self, *names):
        units = {n: main.resident.sha((self.project.parent / n).read_bytes()) for n in names} | {"Resident.pas": "f" * 40}
        (self.folder / "units.txt").write_text("".join(f"{n} {s}\r\n" for n, s in units.items()), encoding="ascii")
        main.resident.BEAT.write_text(f"state idle|ticks 7|busy 0|folder {self.folder}", encoding="latin-1")

    def test_held_while_the_bridge_holds_each_unit_as_it_is_on_disk(self):
        self.load("Altium_API.pas", "pcb_utils.pas")
        self.assertTrue(main.held_by_resident(self.project))

    def test_not_held_once_a_unit_changed_on_disk(self):
        self.load("Altium_API.pas", "pcb_utils.pas")
        (self.project.parent / "pcb_utils.pas").write_bytes(b"// edited since the bridge loaded it\r\n")
        self.assertFalse(main.held_by_resident(self.project))

    def test_not_held_while_the_bridge_lacks_a_unit(self):
        self.load("Altium_API.pas")
        self.assertFalse(main.held_by_resident(self.project))

    def test_not_held_without_a_heartbeat(self):
        self.assertFalse(main.held_by_resident(self.project))


class RestartTest(Fixture):
    def want(self):
        main.resident.WANTED.touch()

    def test_started_again_under_the_callers_lock_only_while_wanted(self):
        with mock.patch.object(main.resident, "start") as start:
            main.restart_resident(self.lines.append)
            start.assert_not_called()
            self.want()
            main.restart_resident(self.lines.append)
        start.assert_called_once_with(self.lines.append, held=True)

    def test_a_start_that_fails_is_said_and_the_call_goes_on(self):
        self.want()
        with mock.patch.object(main.resident, "start", side_effect=RuntimeError("the bridge's units declare a name "
                                                                                 "twice: x in a.pas, b.pas")):
            main.restart_resident(self.lines.append)
        self.assertEqual(self.lines, ["resident bridge not started: the bridge's units declare a name twice: x in "
                                      "a.pas, b.pas"])

    def test_with_no_altium_running_nothing_is_sent(self):
        self.want()
        main.restart_resident(self.lines.append)
        self.assertEqual(self.lines, ["resident bridge not started: Altium is not running"])


if __name__ == "__main__":
    unittest.main()
