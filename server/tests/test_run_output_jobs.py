"""run_output_jobs: the container's output folder listed before and after, the files reported, an empty run a
failure with its reason (pure Python; the bridge is faked).

Run from server/:  python -m unittest tests.test_run_output_jobs -v
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import main  # noqa: E402
from outjob_parser import outjob_project, output_folder, produced, snapshot  # noqa: E402

# The shape of Elch's TEMPMODUL.OutJob: a JLC and a Reports container, both under Project Outputs
OUTJOB = """[OutputJobFile]
Version=1.0

[OutputGroup1]
Name=
OutputMedium1=JLC
OutputMedium1_Type=GeneratedFiles
OutputMedium2=Reports
OutputMedium2_Type=GeneratedFiles
OutputType1=Gerber
OutputName1=Gerber Files
OutputDocumentPath1=BOARD.PcbDoc
OutputEnabled1=1
OutputEnabled1_OutputMedium1=1
OutputType2=Design Rules Check
OutputName2=Design Rules Check
OutputDocumentPath2=BOARD.PcbDoc
OutputEnabled2=1

[PublishSettings]
OutputBasePath1=Project Outputs for BOARD
OutputPathMedia1=[Media Name]
OutputBasePath2=Project Outputs for BOARD
OutputPathMedia2=[Media Name]
"""
PRJPCB = """[Design]
Version=1.0

[Document1]
DocumentPath=BOARD.PcbDoc

[Document2]
DocumentPath=fab\\BOARD.OutJob
"""


class Fixture(unittest.TestCase):
    """A project folder with fab/BOARD.OutJob, listed by BOARD.PrjPcb."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "fab").mkdir()
        self.outjob = self.dir / "fab" / "BOARD.OutJob"
        self.outjob.write_text(OUTJOB, encoding="utf-8")
        (self.dir / "BOARD.PrjPcb").write_text(PRJPCB, encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()


class OutputFolderTest(Fixture):
    def test_the_project_listing_the_outjob_and_its_output_folder(self):
        prj = outjob_project(self.outjob)
        self.assertEqual(prj.name, "BOARD.PrjPcb")
        self.assertEqual(output_folder(prj, {"base_path": "Project Outputs for BOARD"}),
                         self.dir / "Project Outputs for BOARD")
        self.assertEqual(output_folder(prj, {"base_path": ""}), self.dir / "Project Outputs for BOARD")

    def test_an_outjob_no_project_lists_is_refused(self):
        stray = self.dir / "fab" / "OTHER.OutJob"
        stray.write_text(OUTJOB, encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "lists OTHER.OutJob"):
            outjob_project(stray)

    def test_produced_lists_new_and_changed_files(self):
        out = self.dir / "out"
        (out / "JLC").mkdir(parents=True)
        (out / "JLC" / "old.txt").write_text("a")
        (out / "JLC" / "same.txt").write_text("a")
        before = snapshot(out)
        (out / "JLC" / "old.txt").write_text("abc")
        (out / "JLC" / "new.gbr").write_text("12345")
        self.assertEqual(produced(before, snapshot(out)),
                         [{"path": "JLC/new.gbr", "size": 5}, {"path": "JLC/old.txt", "size": 3}])
        self.assertEqual(snapshot(self.dir / "missing"), {})


class RunOutputJobsTest(Fixture):
    def setUp(self):
        super().setUp()
        self.calls = []
        self.write = True
        self.saved = main.altium_bridge.execute_command

        async def bridge(command, params):
            self.calls.append((command, params))
            if command == "get_output_job_containers":
                return {"success": True, "result": {"success": True, "outjob_path": str(self.outjob)}}
            if self.write:                       # what Altium does when the OutJob is in front
                jlc = self.dir / "Project Outputs for BOARD" / "JLC"
                jlc.mkdir(parents=True, exist_ok=True)
                (jlc / "BOARD.GTL").write_text("G04*")
            return {"success": True, "result": {"success": True, "outjob_path": str(self.outjob), "container_results": [
                {"container_name": params["container_names"][0], "ran": True}]}}
        main.altium_bridge.execute_command = bridge

    def tearDown(self):
        main.altium_bridge.execute_command = self.saved
        super().tearDown()

    def run_tool(self, names, path=""):
        return json.loads(asyncio.run(main.run_output_jobs(None, names, path)))

    def test_the_files_a_container_wrote_are_returned(self):
        r = self.run_tool(["JLC"])
        self.assertTrue(r["success"], r)
        c = r["containers"][0]
        self.assertEqual(c["files"], [{"path": "JLC/BOARD.GTL", "size": 4}])
        self.assertEqual(Path(c["output_folder"]), self.dir / "Project Outputs for BOARD")
        # the focused OutJob is asked for once, then named in the run, so both talk about the same file
        self.assertEqual(self.calls[0][0], "get_output_job_containers")
        self.assertEqual(self.calls[1], ("run_output_jobs", {"container_names": ["JLC"], "outjob_path": str(self.outjob)}))

    def test_a_run_that_writes_nothing_fails_with_the_reason(self):
        self.write = False
        main_sleep = main.asyncio.sleep

        async def no_wait(_):
            return None
        main.asyncio.sleep = no_wait
        try:
            r = self.run_tool(["JLC", "Reports"], str(self.outjob))
        finally:
            main.asyncio.sleep = main_sleep
        self.assertFalse(r["success"])
        jlc, reports = r["containers"]
        self.assertIn("nothing appeared or changed", jlc["error"])
        self.assertIn("Gerber Files", jlc["error"])
        self.assertIn("no output of the OutJob is connected", reports["error"])
        self.assertEqual([c[0] for c in self.calls], ["run_output_jobs", "run_output_jobs"])

    def test_an_unknown_container_never_reaches_altium(self):
        r = self.run_tool(["Gerbers"], str(self.outjob))
        self.assertIn("has no container Gerbers; it has JLC, Reports", r["error"])
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
