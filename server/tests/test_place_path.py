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
              "ReplicateLibSymbol": UTILS, "EdKeepClash": EDIT, "EdClear": EDIT, "EdOpenProject": EDIT}


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

    def test_a_place_without_its_nudge_goes_where_it_is_told(self):
        loop = body("EditSchematicFromSpec")
        place = loop[loop.index("else if (Kind = 'PLACE') then"):loop.index("else if (Kind = 'REPLACE') then")]
        self.assertRegex(place, r"if \(GetFieldFromPipeString\(Rec, 15\) = '0'\) then\s*EdPutAt\(Comp,")
        self.assertRegex(place, r"else if not EdPut\(Comp,")

    def test_a_replace_that_keeps_the_wiring_labels_nothing(self):
        loop = body("EditSchematicFromSpec")
        replace = loop[loop.index("else if (Kind = 'REPLACE') then"):loop.index("else if (Kind = 'RELINK') then")]
        self.assertIn("Keep := (GetFieldFromPipeString(Rec, 12) = '1');", replace)
        self.assertIn("if Keep then Why := EdKeepClash(Comp, Old) else Why := EdReplaceClash(Comp, Old, Spec, i);",
                      replace)
        guarded = replace[replace.index("if not Keep then"):replace.index("EdRemove(Old);")]
        self.assertIn("EdLabelOldEnd(", guarded)                 # the old ends labelled only when not kept
        self.assertIn("EdUnmappedWired(", guarded)
        keep = body("EdKeepClash")
        self.assertIn("EdFindPin(Old, Pin.Designator)", keep)    # each new pin on an old pin of its designator ...
        self.assertIn("if (X <> OX) or (Y <> OY) then", keep)    # ... ending where it did
        self.assertIn("EdFindPin(Comp, Pin.Designator) = nil", keep)    # and no old pin left without a new one
        finish = body("EdFinishPart")
        self.assertIn("(GetFieldFromPipeString(Line, 12) <> '1')", finish)     # no unassigned pins listed for it

    def test_a_sheet_named_with_its_project_opens_inside_it(self):
        loop = body("EditSchematicFromSpec")
        project = loop.index("Why := EdOpenProject(GetFieldFromPipeString(Spec[0], 2), EdSheetPath);")
        self.assertLess(project, loop.index("SrvDoc := OpenLibraryDocument('SCH', EdSheetPath);"))
        opened = body("EdOpenProject")
        self.assertIn("GetWorkspace.DM_OpenProject(Path, True)", opened)
        self.assertIn("UpperCase(P.DM_ProjectFullPath) <> UpperCase(Path)", opened)    # read back by its path
        self.assertIn("UpperCase(P.DM_LogicalDocuments(i).DM_FullPath) = UpperCase(Sheet)", opened)   # lists it
        self.assertLess(opened.index("Listed := False;"), opened.index("P.DM_SetAsCurrentProject;"))

    def test_a_cleared_sheet_is_collected_before_anything_goes(self):
        clear = body("EdClear")
        collect, remove = clear.index("Doomed.Add(Obj)"), clear.index("EdRemove(Doomed[k])")
        self.assertLess(clear.index("SchIterator_Destroy(Iter)"), remove)
        self.assertLess(collect, clear.index("SchIterator_Destroy(Iter)"))
        self.assertIn("EdOwnedBySheet(Obj)", clear)
        self.assertIn("else if (Kind = 'CLEARSHEET') then\n            EdClear", body("EditSchematicFromSpec"))

    def test_the_call_lets_the_libraries_go_before_its_answer(self):
        loop = body("EditSchematicFromSpec")
        self.assertLess(loop.index("EdSyms := CreateObject(TInterfaceList)"), loop.index("PreProcess(EdDoc, '')"))
        self.assertRegex(loop, r"EdSyms\.Clear;\s*EdLibDocs\.Clear;")


if __name__ == "__main__":
    unittest.main()
