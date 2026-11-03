"""edit_schematic's place path, checked on the DelphiScript sources (pure Python; the live proof runs in Altium).

Run from the repo root:  uv run --with pytest python -m pytest -q server/tests/test_place_path.py

A call opens each .SchLib once and finds each symbol once, the scan stopping at the hit, and every part is copied from
that index; nothing in the path sleeps (a script runs on Altium's message loop, which a Sleep does not turn). A part's
free spot is read from an index of the sheet's pin ends, labels and ports, built once and grown by each placed part,
any other op making it stale. A placed copy's graphics are locked through UpdatePrimitivesAccessibility once it is
registered on the sheet.
"""
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_bridge_closes import BRIDGE, routines, unit  # noqa: E402

EDIT = routines(unit(BRIDGE / "schematic_edit.pas"))
OTHER = routines(unit(BRIDGE / "other_utils.pas"))
UTILS = routines(unit(BRIDGE / "schematic_utils.pas"))
PLACE_PATH = {"EdSymbol": EDIT, "EdReplicate": EDIT, "EdSpotAdd": EDIT, "EdSpotIndex": EDIT, "EdTaken": EDIT,
              "EdClearSpot": EDIT, "EdPutAt": EDIT, "EdPut": EDIT, "EdSetLink": EDIT, "EdFinishPart": EDIT,
              "EdReportPlaced": EDIT, "EditSchematicFromSpec": EDIT, "OpenLibraryDocument": OTHER,
              "ReplicateLibSymbol": UTILS}


def body(name):
    return PLACE_PATH[name][name]


class PlacePathTest(unittest.TestCase):
    def test_nothing_in_the_place_path_sleeps(self):
        for name in PLACE_PATH:
            with self.subTest(routine=name):
                self.assertNotRegex(body(name), r"\bSleep\s*\(")

    def test_a_symbol_is_found_once_a_call_and_its_library_opened_once(self):
        text = body("EdSymbol")
        index, library = text.index("EdSymKeys[n] = Key"), text.index("EdLibPaths.IndexOf(Path)")
        opened = text.index("OpenLibraryDocument('SchLib', LibPath)")
        self.assertLess(index, library)                          # the symbol index first, then the library's
        self.assertLess(library, opened)                         # opened only when the call has not opened it
        self.assertRegex(text, r"if \(k >= 0\) then LibDoc := EdLibDocs\[k\]\s*else\s*begin\s*OpenLibraryDocument")
        self.assertRegex(text, r"while \(Prim <> nil\) and \(Result = nil\) do")     # the scan stops at the hit
        self.assertRegex(text, r"EdSymKeys\.Add\(Key\);\s*EdSyms\.Add\(Result\);")
        replicate = body("EdReplicate")
        self.assertIn("EdSymbol(LibPath, LibRef)", replicate)
        self.assertNotIn("OpenLibraryDocument", replicate)

    def test_a_placed_copy_is_locked_once_it_is_on_the_sheet(self):
        text = body("EdPutAt")
        self.assertLess(text.index("RegisterSchObjectInContainer(Comp)"), text.index("Comp.UpdatePrimitivesAccessibility"))

    def test_the_free_spot_is_read_from_the_index_and_every_other_op_makes_it_stale(self):
        clear = body("EdClearSpot")
        self.assertIn("if not EdSpotsOk then EdSpotIndex(Comp);", clear)
        self.assertNotIn("EdTouchAt", clear)
        self.assertEqual(len(re.findall(r"\bEdTaken\(X, Y\)", clear)), 2)       # the pin end and its stub end
        loop = body("EditSchematicFromSpec")
        self.assertIn("if (Pos('|' + Kind + '|', '|PLACE|POWERNET|GEOMETRY|SAVE|') = 0) then EdSpotsOk := False;", loop)
        place = loop[loop.index("else if (Kind = 'PLACE') then"):loop.index("else if (Kind = 'REPLACE') then")]
        self.assertRegex(place, r"EdFinishPart\(Comp, Spec, i, Rec\);\s*EdSpotAdd\(Comp\);")
        self.assertIn("SkipAddr", body("EdSpotIndex"))           # the part being placed is not its own obstacle

    def test_the_call_lets_the_libraries_go_before_its_answer(self):
        loop = body("EditSchematicFromSpec")
        self.assertLess(loop.index("EdSyms := CreateObject(TInterfaceList)"), loop.index("PreProcess(EdDoc, '')"))
        self.assertRegex(loop, r"EdSyms\.Clear;\s*EdLibDocs\.Clear;")


if __name__ == "__main__":
    unittest.main()
