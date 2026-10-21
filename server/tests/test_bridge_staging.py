"""The bridge's content-named staging and build_schematic's spec (pure Python, no Altium needed).

Run from the repo root:  python -m unittest server.tests.test_bridge_staging -v
or from server/:          python -m unittest tests.test_bridge_staging -v
"""
import codecs
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import main  # noqa: E402

PRJSCR = "[Design]\r\nVersion=1.0\r\n\r\n[Document1]\r\nDocumentPath=a.pas\r\n\r\n[Document2]\r\nDocumentPath=sub\\b.pas\r\n"


class StageBridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.src, self.root = base / "src", base / "stage"
        (self.src / "sub").mkdir(parents=True)
        self.prj = self.src / "Bridge.PrjScr"
        self.prj.write_bytes(codecs.BOM_UTF8 + PRJSCR.encode("latin-1"))
        (self.src / "a.pas").write_text("procedure A; begin end;")
        (self.src / "sub" / "b.pas").write_text("procedure B; begin end;")

    def tearDown(self):
        self.tmp.cleanup()

    def staged_folders(self):
        return sorted(p.name for p in self.root.iterdir() if p.is_dir())

    def test_copies_the_project_and_every_unit_it_lists(self):
        staged = main.stage_bridge(self.prj, self.root)
        self.assertEqual(staged.name, "Bridge.PrjScr")
        self.assertEqual((staged.parent / "a.pas").read_text(), "procedure A; begin end;")
        self.assertEqual((staged.parent / "sub" / "b.pas").read_text(), "procedure B; begin end;")
        self.assertTrue((self.root / ".parked").is_file())

    def test_unchanged_sources_reuse_the_copy(self):
        first = main.stage_bridge(self.prj, self.root)
        self.assertEqual(main.stage_bridge(self.prj, self.root), first)
        self.assertEqual(len(self.staged_folders()), 1)

    def test_an_edited_unit_gets_a_new_copy_with_the_new_text(self):
        first = main.stage_bridge(self.prj, self.root)
        (self.src / "sub" / "b.pas").write_text("procedure B; begin Edited; end;")
        second = main.stage_bridge(self.prj, self.root)
        self.assertNotEqual(second.parent, first.parent)
        self.assertEqual((second.parent / "sub" / "b.pas").read_text(), "procedure B; begin Edited; end;")
        self.assertEqual((first.parent / "sub" / "b.pas").read_text(), "procedure B; begin end;")

    def test_a_unit_outside_the_project_is_refused(self):
        self.prj.write_text(PRJSCR.replace("a.pas", "..\\escape.pas"), encoding="latin-1")
        with self.assertRaises(ValueError):
            main.stage_bridge(self.prj, self.root)

    def test_a_missing_unit_is_refused(self):
        (self.src / "a.pas").unlink()
        with self.assertRaises(FileNotFoundError):
            main.stage_bridge(self.prj, self.root)

    def test_the_shipped_bridge_stages_whole(self):
        staged = main.stage_bridge(main.DEFAULT_SCRIPT_PATH, self.root)
        units = {p.name for p in staged.parent.iterdir()}
        for name in ("Altium_API.PrjScr", "Altium_API.pas", "schematic_utils.pas", "pcb_utils.pas",
                     "other_utils.pas", "json_utils.pas", "pcb_layout_duplicator.pas"):
            self.assertIn(name, units)


class CircuitSpecTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.lib = self.dir / "Parts.SchLib"
        self.lib.write_bytes(b"")
        self.parts = [dict(designator="D1", symbol_library=str(self.lib), symbol="LED", x=1000, y=2000)]

    def tearDown(self):
        self.tmp.cleanup()

    def test_without_a_path_nothing_is_saved(self):
        lines = main.circuit_spec(self.parts)
        self.assertEqual(lines, [f"PART|D1|{self.lib}|LED||1000|2000|0|0"])

    def test_the_save_record_leads_the_spec(self):
        target = self.dir / "board.SchDoc"
        lines = main.circuit_spec(self.parts, net_labels=[dict(x=1, y=2, text="RES+")], save_path=str(target))
        self.assertEqual(lines[0], f"SAVE|{target}")
        self.assertEqual(lines[-1], "NETLABEL|1|2|0|RES+")

    def test_a_save_path_it_cannot_honour_is_refused(self):
        (self.dir / "taken.SchDoc").write_bytes(b"")
        for bad in ("board.SchDoc", str(self.dir / "board.PcbDoc"), str(self.dir / "absent" / "board.SchDoc"),
                    str(self.dir / "taken.SchDoc")):
            with self.subTest(save_path=bad), self.assertRaises(ValueError):
                main.circuit_spec(self.parts, save_path=bad)

    def test_parts_and_wires_keep_their_checks(self):
        with self.assertRaises(ValueError):
            main.circuit_spec([dict(designator="D1", symbol_library=str(self.lib), symbol="LED", x=0)])
        with self.assertRaises(ValueError):
            main.circuit_spec([dict(self.parts[0], symbol_library=str(self.dir / "Parts.IntLib"))])
        with self.assertRaises(ValueError):
            main.circuit_spec(self.parts, wires=[[0, 0, 100]])


if __name__ == "__main__":
    unittest.main()
