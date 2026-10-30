"""The mailbox between the servers and the bridge (pure Python; the bridge is faked the way Altium_API.pas's Run
serves the queue: only the lock holder's requests, taken by a rename, progress records, the answer renamed into
place with the request's id once the run has closed the bridge's script project).

Run from server/:  python -m unittest tests.test_mailbox -v
"""
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import mailbox  # noqa: E402
import main  # noqa: E402
import script_lock  # noqa: E402
from altium_files import read_altium_text  # noqa: E402
from PIL import Image  # noqa: E402


class FakeBridge:
    """Altium_API.pas's Run in Python: serve the queued requests of the lock's holder, oldest first, then close the
    bridge's script project and rename each answer into place."""

    def __init__(self, root, lock, steps=3, step_seconds=0.0, stop_after_take=False, answer=None, close_seconds=0.0):
        self.root, self.lock = Path(root), Path(lock)
        self.steps, self.step_seconds, self.stop_after_take = steps, step_seconds, stop_after_take
        self.answer = answer or (lambda request: {"echo": request})
        self.close_seconds = close_seconds
        self.served, self.answered, self.lock_held_at_close = [], [], []

    def holder(self):
        try:
            return str(json.loads(self.lock.read_text(encoding="utf-8"))["pid"])
        except (OSError, ValueError, KeyError):
            return ""

    def run(self):
        holder = self.holder()
        queue = self.root / mailbox.QUEUE
        for entry in sorted(queue.glob("*.req")) if holder and queue.is_dir() else ():
            rid = entry.stem
            folder = self.root / mailbox.MAIL / rid
            if not rid.startswith(holder + "-") or not folder.is_dir():
                continue
            try:
                os.replace(entry, folder / "taken")
            except OSError:
                continue
            self.serve(rid, folder)
        self.close()
        return self.served

    def close(self):
        """Run's end: the bridge's script project is closed, then each answer is renamed into place with closed.txt
        beside it."""
        time.sleep(self.close_seconds)
        self.lock_held_at_close.append(self.lock.exists())
        for folder in self.answered:
            (folder / "closed.txt").write_text("CLOSED C:\\Users\\Public\\altium_mcp\\bridge\\0\\Altium_API.PrjScr\r\n",
                                               encoding="cp1252")
            os.replace(folder / "response.tmp", folder / "response.json")

    def serve(self, rid, folder):
        self.served.append(rid)
        records = []

        def progress(step, count, total):
            records.append(f"{len(records) + 1}|{len(records) * 10}|{step}|{count}|{total}")
            (folder / "progress.log").write_text("\r\n".join(records) + "\r\n", encoding="cp1252")

        progress("taken", 0, 0)
        if self.stop_after_take:
            return
        request = json.loads(read_altium_text(folder / "request.json"))
        for i in range(1, self.steps + 1):
            time.sleep(self.step_seconds)
            progress(f"place R{i}", i, self.steps)
        (folder / "response.tmp").write_text(json.dumps({"success": True, "request_id": rid,
                                                         "result": self.answer(request)}), encoding="cp1252")
        progress("answered", 0, 0)
        self.answered.append(folder)


def take_lock(path, pid):
    Path(path).write_text(json.dumps({"pid": pid, "owner": "altium-mcp test (mailbox)", "since": time.time()}),
                          encoding="utf-8")


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.lock = self.root / "altium_script.lock"

    def tearDown(self):
        self.tmp.cleanup()

    def wait(self, mail, stall, **kw):
        seen = []

        async def on_progress(record, records):
            seen.append(record["step"])
        return asyncio.run(mailbox.wait(mail, stall, on_progress, poll=0.01, **kw)), seen


class LayoutTest(Fixture):
    def test_a_request_is_queued_only_once_its_folder_is_complete(self):
        mail = mailbox.Mail(self.root, "edit_schematic", {"a": 1}, {"edit_spec.txt": "SHEET|x\n"}, "42-1-1")
        mail.post()
        self.assertEqual([p.name for p in (self.root / "queue").iterdir()], ["42-1-1.req"])
        request = json.loads(read_altium_text(mail.folder / "request.json"))
        self.assertEqual(request, {"command": "edit_schematic", "request_id": "42-1-1", "sender_pid": 42, "a": 1})
        self.assertEqual(read_altium_text(mail.folder / "edit_spec.txt"), "SHEET|x\n")

    def test_ids_carry_the_sender_pid_and_differ(self):
        a, b = mailbox.new_id(), mailbox.new_id()
        self.assertNotEqual(a, b)
        self.assertEqual(mailbox.sender_pid(a), os.getpid())

    def test_a_record_still_being_written_is_skipped(self):
        records = mailbox.parse_records("1|0|taken|0|0\r\n2|15|place R1|1|60\r\n3|20|pla")
        self.assertEqual([r["step"] for r in records], ["taken", "place R1"])
        self.assertEqual(records[1], {"seq": 2, "elapsed_ms": 15, "step": "place R1", "count": 1, "total": 60})

    def test_a_queued_request_of_another_server_is_expired_under_the_lock(self):
        stale = mailbox.Mail(self.root, "get_all_nets", {}, request_id="7-1-1")
        stale.post()
        own = mailbox.Mail(self.root, "get_all_nets", {}, request_id="8-2-1")
        own.post()
        self.assertEqual(mailbox.expire_others(self.root, own.id), ["7-1-1"])
        self.assertTrue((stale.folder / "expired").is_file())
        self.assertEqual([p.name for p in (self.root / "queue").iterdir()], ["8-2-1.req"])

    def test_old_request_folders_are_pruned(self):
        old = mailbox.Mail(self.root, "x", {}, request_id="1-1-1")
        old.post()
        os.utime(old.folder, (time.time() - 2 * mailbox.KEEP_SECONDS,) * 2)
        new = mailbox.Mail(self.root, "x", {}, request_id="1-2-1")
        new.post()
        mailbox.prune(self.root)
        self.assertFalse(old.folder.exists())
        self.assertTrue(new.folder.exists())


class WaitTest(Fixture):
    def test_the_answer_to_this_request_comes_back_with_every_progress_record(self):
        mail = mailbox.Mail(self.root, "edit_schematic", {}, request_id=f"{os.getpid()}-1-1")
        mail.post()
        take_lock(self.lock, os.getpid())
        bridge = FakeBridge(self.root, self.lock, steps=3, step_seconds=0.05)
        threading.Thread(target=bridge.run).start()
        (state, text), seen = self.wait(mail, stall=2)
        self.assertEqual(state, "answered")
        self.assertEqual(json.loads(text)["request_id"], mail.id)
        self.assertIn("place R1", seen)          # the last record may land with the answer and go unseen

    def test_a_run_far_longer_than_the_stall_time_is_waited_for_while_it_reports(self):
        # 40 steps of 0.05 s = 2 s against a stall of 0.5 s: the wait follows the progress, not a clock
        mail = mailbox.Mail(self.root, "edit_schematic", {}, request_id=f"{os.getpid()}-1-1")
        mail.post()
        take_lock(self.lock, os.getpid())
        threading.Thread(target=FakeBridge(self.root, self.lock, steps=40, step_seconds=0.05).run).start()
        t0 = time.monotonic()
        (state, _), seen = self.wait(mail, stall=0.5)
        self.assertEqual(state, "answered")
        self.assertGreater(time.monotonic() - t0, 1.5)
        self.assertGreater(len(seen), 20)

    def test_a_request_nobody_takes_stalls_and_is_withdrawn(self):
        mail = mailbox.Mail(self.root, "get_all_nets", {}, request_id=f"{os.getpid()}-1-1")
        mail.post()
        (state, detail), _ = self.wait(mail, stall=0.2)
        self.assertEqual(state, "stalled")
        self.assertFalse(detail["taken"])
        self.assertTrue(mail.withdraw())
        self.assertEqual(list((self.root / "queue").iterdir()), [])

    def test_a_taken_request_that_goes_quiet_stalls_and_cannot_be_withdrawn(self):
        mail = mailbox.Mail(self.root, "edit_schematic", {}, request_id=f"{os.getpid()}-1-1")
        mail.post()
        take_lock(self.lock, os.getpid())
        FakeBridge(self.root, self.lock, stop_after_take=True).run()
        (state, detail), _ = self.wait(mail, stall=0.2)
        self.assertEqual(state, "stalled")
        self.assertTrue(detail["taken"])
        self.assertEqual(detail["last_record"]["step"], "taken")
        self.assertFalse(mail.withdraw())


class WatchTest(Fixture):
    def test_a_box_closed_during_the_call_shortens_the_wait_to_the_grace_time(self):
        mail = mailbox.Mail(self.root, "take_view_screenshot", {}, request_id=f"{os.getpid()}-1-1")
        mail.post()
        take_lock(self.lock, os.getpid())
        FakeBridge(self.root, self.lock, stop_after_take=True).run()
        calls = []

        async def watch():
            calls.append(1)
            return len(calls) == 1                # a box held the script and was closed
        t0 = time.monotonic()
        (state, detail), _ = self.wait(mail, stall=30, watch=watch, watch_every=0.05, after_watch=0.3)
        self.assertEqual(state, "stalled")
        self.assertLess(time.monotonic() - t0, 5)


class TwoServersTest(Fixture):
    def test_the_bridge_serves_only_the_lock_holder_and_each_answer_lands_in_its_own_folder(self):
        a = mailbox.Mail(self.root, "get_all_nets", {"who": "a"}, request_id="1001-5-1")
        b = mailbox.Mail(self.root, "get_all_nets", {"who": "b"}, request_id="1002-5-1")
        a.post()
        b.post()                                # two servers queue at once: nothing is overwritten
        take_lock(self.lock, 1001)
        self.assertEqual(FakeBridge(self.root, self.lock).run(), ["1001-5-1"])
        self.assertTrue(b.entry.exists())       # b is not run while a holds the lock
        take_lock(self.lock, 1002)
        self.assertEqual(FakeBridge(self.root, self.lock).run(), ["1002-5-1"])
        self.assertEqual(json.loads(a.answer())["result"]["echo"]["who"], "a")
        self.assertEqual(json.loads(b.answer())["result"]["echo"]["who"], "b")

    def test_a_request_left_by_a_server_that_gave_up_is_never_run(self):
        gave_up = mailbox.Mail(self.root, "edit_schematic", {}, request_id="1001-5-1")
        gave_up.post()
        take_lock(self.lock, 1002)              # the next server holds the lock
        self.assertEqual(FakeBridge(self.root, self.lock).run(), [])
        mine = mailbox.Mail(self.root, "get_all_nets", {}, request_id="1002-6-1")
        mailbox.expire_others(self.root, mine.id)
        mine.post()
        take_lock(self.lock, 1001)              # even when its pid holds the lock again later
        self.assertEqual(FakeBridge(self.root, self.lock).run(), [])
        self.assertIsNone(gave_up.answer())


class ExecuteCommandTest(Fixture):
    """execute_command end to end against the fake bridge, the lock in a scratch folder."""

    def setUp(self):
        super().setUp()
        self.saved = (main.EXCHANGE_DIR, script_lock.LOCK, script_lock.HOLD, main.altium_bridge.run_altium_script)
        main.EXCHANGE_DIR = self.root
        script_lock.LOCK, script_lock.HOLD = self.lock, self.root / "altium_run.hold"
        self.env = dict(os.environ)
        os.environ.pop(script_lock.HOLD_ENV, None)
        self.owners = []

        async def altium():                    # RunScript: Altium serves the queue a moment later
            self.owners.append(json.loads(self.lock.read_text(encoding="utf-8"))["owner"])
            threading.Timer(0.05, FakeBridge(self.root, self.lock, steps=2, step_seconds=0.02).run).start()
            return True
        main.altium_bridge.run_altium_script = altium

    def tearDown(self):
        main.EXCHANGE_DIR, script_lock.LOCK, script_lock.HOLD, main.altium_bridge.run_altium_script = self.saved
        os.environ.clear()
        os.environ.update(self.env)
        super().tearDown()

    def test_a_live_run_hold_keeps_the_lock_from_every_taker_without_its_token(self):
        # board_build holds Altium for a run (tools/altium/altium_lock.py run_hold): this server meets the hold as the
        # holder, the dev/call_tool.py the run starts carries its token and takes the lock; a dead run's hold goes
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
        try:
            script_lock.HOLD.write_text(json.dumps(dict(pid=child.pid, owner="board_build CTRL", since=0, token="t")),
                                        encoding="utf-8")
            holder = script_lock.try_take("altium-mcp get_all_nets (mailbox)")
            self.assertIn("a run's hold: board_build CTRL", script_lock.describe(holder))
            self.assertFalse(self.lock.exists())
            os.environ[script_lock.HOLD_ENV] = "t"
            self.assertIsNone(script_lock.try_take("altium-mcp edit_schematic (dev)"))
            script_lock.release()
            del os.environ[script_lock.HOLD_ENV]
        finally:
            child.kill()
            child.wait()
        self.assertIsNone(script_lock.try_take("altium-mcp get_all_nets (mailbox)"))
        script_lock.release()
        self.assertFalse(script_lock.HOLD.exists())

    def test_a_call_holds_the_lock_as_a_mailbox_caller_and_gets_its_own_answer(self):
        r = asyncio.run(main.altium_bridge.execute_command("get_all_nets", {"x": 1},
                                                           files={"edit_spec.txt": "S\n"}, stall=2))
        self.assertTrue(r["success"], r)
        self.assertEqual(r["result"]["echo"]["x"], 1)
        self.assertEqual(self.owners, ["altium-mcp get_all_nets (mailbox)"])
        self.assertEqual(Path(r["mail"]).name, r["request_id"])
        self.assertEqual(read_altium_text(Path(r["mail"]) / "edit_spec.txt"), "S\n")
        self.assertFalse(self.lock.exists())
        status = json.loads((self.root / "status.json").read_text(encoding="utf-8"))
        self.assertEqual((status["state"], status["id"]), ("answered", r["request_id"]))

    def test_the_lock_is_held_until_the_bridge_has_closed_its_script_project(self):
        # Run renames the answer into place only after it closed its own script project (Altium_API.pas), so the
        # server's lock covers the close and the next caller's RunScript never meets the bridge still closing
        bridges = []

        async def altium():
            bridges.append(FakeBridge(self.root, self.lock, steps=1, close_seconds=0.3))
            threading.Timer(0.05, bridges[-1].run).start()
            return True
        main.altium_bridge.run_altium_script = altium
        r = asyncio.run(main.altium_bridge.execute_command("get_all_nets", {}, stall=5))
        self.assertTrue(r["success"], r)
        self.assertEqual(bridges[0].lock_held_at_close, [True])
        self.assertTrue(read_altium_text(Path(r["mail"]) / "closed.txt").startswith("CLOSED "))
        self.assertFalse(self.lock.exists())

    def test_a_refused_runscript_is_sent_again_and_the_request_served(self):
        # Altium refuses a RunScript while another script finishes, with a box of its own; the request stays queued
        boxes, posted, runs = [], [], []

        async def altium():
            runs.append(1)
            if len(runs) == 1:
                boxes.append((4242, "TMessageForm", "Error"))
            else:
                threading.Timer(0.05, FakeBridge(self.root, self.lock, steps=1).run).start()
            return True

        class User32:
            def PostMessageW(self, h, msg, w, l):
                posted.append(h)
                boxes.clear()
        saved = (main.x2_windows, main.ctypes_user32, main.grab_window)
        main.x2_windows = lambda classes=None: list(boxes)
        main.ctypes_user32 = lambda: User32()
        main.grab_window = lambda h: Image.new("RGB", (10, 10))
        main.altium_bridge.run_altium_script = altium
        try:
            r = asyncio.run(main.altium_bridge.execute_command("get_all_nets", {}, stall=10))
        finally:
            main.x2_windows, main.ctypes_user32, main.grab_window = saved
        self.assertTrue(r["success"], r)
        self.assertEqual((len(runs), posted), (2, [4242]))
        self.assertEqual(len(r["runscript_refused"]), 1)

    def test_a_stall_is_reported_with_the_request_withdrawn(self):
        async def lost():
            return True                         # the RunScript never arrives
        main.altium_bridge.run_altium_script = lost
        r = asyncio.run(main.altium_bridge.execute_command("get_all_nets", {}, stall=0.3))
        self.assertFalse(r["success"])
        self.assertIn("did not take the request within 0.3 s, so it was withdrawn", r["error"])
        self.assertEqual(list((self.root / "queue").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
