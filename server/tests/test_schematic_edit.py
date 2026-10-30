"""schematic_edit's spec builder and the edit tools' validation (pure Python, no Altium needed).

Run from the repo root:  uv run --with pytest python -m pytest -q server/tests/test_schematic_edit.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import schematic_edit as se  # noqa: E402


class EditSpecTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.sheet = base / "Board.SchDoc"
        self.sheet.write_bytes(b"x")
        self.lib = base / "parts.SchLib"
        self.lib.write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def spec(self, ops, **kw):
        return se.edit_spec(str(self.sheet), ops, **kw)

    def test_header_names_the_sheet_and_the_power_nets_and_saves_last(self):
        lines = self.spec([{"op": "delete", "uid": "ABCD"}], power_nets=["3V3_SW", "GND_ISO"])
        self.assertEqual(lines[0], f"SHEET|{self.sheet}")
        self.assertIn("POWERNET|3V3_SW|2|1|1", lines)       # supply bar, up
        self.assertIn("POWERNET|GND_ISO|4|3|1", lines)      # another ground: power ground, down, name shown
        self.assertIn("POWERNET|GND|4|3|0", lines)          # always: power ground, down, name hidden
        self.assertEqual(lines[-1], "SAVE")
        self.assertNotIn("SAVE", self.spec([{"op": "delete", "uid": "ABCD"}], save=False))

    def test_a_power_net_pin_carries_its_port_style_a_signal_pin_none(self):
        lines = self.spec([{"op": "attach_pin", "comp": "uid:Q1", "pin": "2", "net": "GND"},
                           {"op": "attach_pin", "comp": "des:R5", "pin": "1", "net": "SW_PC"}])
        self.assertIn("ATTACH|uid:Q1|2|GND|4|3", lines)
        self.assertIn("ATTACH|des:R5|1|SW_PC|-1|-1", lines)

    def test_place_writes_its_link_fields_pins_and_closes_with_end(self):
        lines = self.spec([{"op": "place", "designator": "R100", "symbol_library": str(self.lib), "lib_ref": "RESISTOR",
                            "x": 16500, "y": 19000, "orient": 1, "link_library": "Res-Gen.DbLib",
                            "db_table": "Panasonic_ERJ_0402", "design_item": "ERJ-2RKF1001X",
                            "pins": {"1": "SW_PC", "2": "PC_HOLD_G"}, "parameters": {"Value": "1k"}}])
        i = next(k for k, l in enumerate(lines) if l.startswith("PLACE|"))
        self.assertEqual(lines[i], f"PLACE|R100|{self.lib}|RESISTOR|16500|19000|1|0|Res-Gen.DbLib|Panasonic_ERJ_0402|ERJ-2RKF1001X||")
        self.assertEqual(lines[i + 1], "PARAM|Value|1k")
        self.assertEqual(lines[i + 2:i + 5], ["PIN|1|SW_PC|-1|-1", "PIN|2|PC_HOLD_G|-1|-1", "END"])

    def test_place_part_appends_the_part_of_a_multi_part_symbol(self):
        op = {"op": "place", "designator": "U7", "symbol_library": str(self.lib), "lib_ref": "TLV9152IDR",
              "x": 1000, "y": 2000, "part": 2, "pins": {"5": "IN_B"}}
        lines = self.spec([op])
        self.assertIn(f"PLACE|U7|{self.lib}|TLV9152IDR|1000|2000|0|0||||||2", lines)
        with self.assertRaisesRegex(ValueError, "part must be a whole number"):
            self.spec([dict(op, part=0)])

    def test_description_and_a_description_parameter_set_the_component_description(self):
        op = {"op": "place", "designator": "C1", "symbol_library": str(self.lib), "lib_ref": "CAP",
              "x": 0, "y": 0, "link_library": "Cap-Gen.DbLib", "description": "CAP CER 0.1UF 50V X7R 0402",
              "parameters": {"Value": "0.1uF"}}
        lines = self.spec([op])
        i = next(k for k, l in enumerate(lines) if l.startswith("PLACE|"))
        self.assertEqual(lines[i + 1:i + 4], ["DESCRIPTION|CAP CER 0.1UF 50V X7R 0402", "PARAM|Value|0.1uF", "END"])
        # an older caller's Description parameter: the component description, never a PARAM line
        old = dict(op, description=None, parameters={"description": "CAP CER 1UF", "Value": "1uF"})
        lines = self.spec([old])
        self.assertIn("DESCRIPTION|CAP CER 1UF", lines)
        self.assertFalse([l for l in lines if l.upper().startswith("PARAM|DESCRIPTION")])
        self.assertIn("DESCRIPTION|CAP CER 1UF", self.spec([dict(old, description="CAP CER 1UF")]))
        relink = self.spec([{"op": "relink", "comp": "des:C1", "description": "CAP CER 10UF"}])
        self.assertEqual(relink[relink.index("RELINK|des:C1|||||") + 1:][:2], ["DESCRIPTION|CAP CER 10UF", "END"])
        self.assertNotIn("DESCRIPTION|", "\n".join(self.spec([dict(op, description="")])))
        for bad, msg in ((dict(old, description="CAP CER 2UF"), "differ"),
                         (dict(op, description="A|B"), "may not contain"),
                         (dict(op, description=None, parameters={"Description": ""}), "parameter Description is empty")):
            with self.subTest(msg=msg), self.assertRaisesRegex(ValueError, msg):
                self.spec([bad])

    def test_a_part_without_a_row_takes_its_footprint_library_as_the_last_field(self):
        pcblib = self.lib.with_name("Res-Gen.PcbLib")
        pcblib.write_bytes(b"x")
        op = {"op": "place", "designator": "R7", "symbol_library": str(self.lib), "lib_ref": "RESISTOR",
              "x": 100, "y": 200, "link_library": "Res-Gen.DbLib", "db_table": "Resistors$",
              "footprint": "RES_0603_1608", "comment": "4k7", "footprint_library": str(pcblib)}
        self.assertIn(f"PLACE|R7|{self.lib}|RESISTOR|100|200|0|0|Res-Gen.DbLib|Resistors$||RES_0603_1608|4k7||{pcblib}",
                      self.spec([op]))                    # field 13, the part, empty; 14 the library
        self.assertIn(f"PLACE|R7|{self.lib}|RESISTOR|100|200|0|0|Res-Gen.DbLib|Resistors$||RES_0603_1608|4k7|2|{pcblib}",
                      self.spec([dict(op, part=2)]))
        replace = {"op": "replace", "comp": "des:R7", "symbol_library": str(self.lib), "lib_ref": "RESISTOR",
                   "pin_map": {"1": "1"}, "nets": {"1": "A"}, "footprint": "RES_0603_1608",
                   "footprint_library": str(pcblib)}
        self.assertIn(f"REPLACE|des:R7|{self.lib}|RESISTOR|-1|0||||RES_0603_1608||{pcblib}", self.spec([replace]))
        self.assertIn(f"RELINK|des:R7|Res-Gen.DbLib|||RES_0603_1608||{pcblib}",
                      self.spec([{"op": "relink", "comp": "des:R7", "link_library": "Res-Gen.DbLib",
                                  "footprint": "RES_0603_1608", "footprint_library": str(pcblib)}]))
        # without it every line reads as before: nothing after the link fields
        self.assertIn(f"PLACE|R7|{self.lib}|RESISTOR|100|200|0|0|Res-Gen.DbLib|Resistors$||RES_0603_1608|4k7",
                      self.spec([dict(op, footprint_library=None)]))
        for bad, msg in ((dict(op, design_item="MPN-R-1"), "without a database row"),
                         (dict(op, footprint=None), "needs footprint"),
                         (dict(op, footprint_library="Res-Gen.PcbLib"), "existing .PcbLib"),
                         (dict(op, footprint_library=str(self.lib)), "existing .PcbLib")):
            with self.subTest(msg=msg), self.assertRaisesRegex(ValueError, msg):
                self.spec([bad])

    def test_replace_needs_a_net_for_every_mapped_pin_and_maps_by_function(self):
        op = {"op": "replace", "comp": "uid:XHHAWBYW", "symbol_library": str(self.lib), "lib_ref": "BZT52H-B5V6-QX",
              "pin_map": {"2": "1", "1": "2"}, "nets": {"1": "5V"}}
        with self.assertRaisesRegex(ValueError, "no net for new pin 2"):
            self.spec([op])
        op["nets"] = {"1": "5V", "2": "GND"}
        lines = self.spec([op])
        self.assertIn("MAP|2|1|5V|2|1", lines)            # old cathode 2 -> new cathode 1
        self.assertIn("MAP|1|2|GND|4|3", lines)

    def test_several_old_pins_onto_one_new_pin_stub_it_once(self):
        lines = self.spec([{"op": "replace", "comp": "des:U1", "symbol_library": str(self.lib), "lib_ref": "MOD",
                            "pin_map": {"49_1": "49", "49_2": "49"}, "nets": {"49": "GND"}}])
        self.assertIn("MAP|49_1|49|GND|4|3", lines)
        self.assertIn("MAPOLD|49_2|49|GND|4|3", lines)

    def test_replace_connects_extra_new_pins_and_marks_no_erc(self):
        lines = self.spec([{"op": "replace", "comp": "des:IC3", "symbol_library": str(self.lib), "lib_ref": "TPS7A2633DRVR",
                            "pin_map": {"1": "6", "5": "1"}, "nets": {"6": "LDO_IN", "1": "LDO_OUT", "4": "LDO_IN"},
                            "no_erc": ["3"]}])
        i = next(k for k, l in enumerate(lines) if l.startswith("REPLACE|"))
        self.assertEqual(lines[i], f"REPLACE|des:IC3|{self.lib}|TPS7A2633DRVR|-1|0|||||")
        self.assertEqual(lines[i + 1:i + 6], ["MAP|1|6|LDO_IN|-1|-1", "MAP|5|1|LDO_OUT|-1|-1",
                                              "PIN|4|LDO_IN|-1|-1", "NOPIN|3", "END"])

    def test_a_pin_with_a_net_and_a_no_erc_marker_is_refused(self):
        with self.assertRaisesRegex(ValueError, "pin 3 has a net and a No-ERC"):
            self.spec([{"op": "place", "designator": "U1", "symbol_library": str(self.lib), "lib_ref": "X",
                        "x": 0, "y": 0, "pins": {"3": "A"}, "no_erc": ["3"]}])

    def test_delete_component_lists_what_to_keep(self):
        lines = self.spec([{"op": "delete_component", "uid": "DHMMDSNB", "keep": ["FZCFQLXR", "VBIOQJFB"]},
                           {"op": "delete_component", "uid": "X1", "dead_ends": False}])
        self.assertIn("DELCOMP|DHMMDSNB|1|FZCFQLXR,VBIOQJFB", lines)
        self.assertIn("DELCOMP|X1|0|", lines)

    def test_label_to_port_takes_the_power_style(self):
        self.assertIn("LABEL_TO_PORT|PZWFJNSF|3V3|2|1|1", self.spec([{"op": "label_to_port", "uid": "PZWFJNSF", "text": "3V3"}]))

    def test_a_row_with_alternates_sends_every_footprint_the_first_current(self):
        op = {"op": "relink", "comp": "uid:MOLEXJ1A", "link_library": "house.DbLib", "db_table": "molex_microfit",
              "design_item": "430450400", "footprint": "MOL_MF3_2X02_RA_THT",
              "alternate_footprints": ["MOL_MF3_2X02_VERT_OR_RA_THT"]}
        lines = self.spec([op])
        self.assertIn("RELINK|uid:MOLEXJ1A|house.DbLib|molex_microfit|430450400|"
                      "MOL_MF3_2X02_RA_THT;MOL_MF3_2X02_VERT_OR_RA_THT|", lines)
        self.assertIn("RELINK|uid:MOLEXJ1A|house.DbLib|molex_microfit|430450400|MOL_MF3_2X02_RA_THT|",
                      self.spec([dict(op, alternate_footprints=[])]))
        for bad, msg in ((dict(op, footprint=None), "needs footprint, the current one"),
                         (dict(op, alternate_footprints="MOL_MF3_2X02_VERT_OR_RA_THT"), "is a list"),
                         (dict(op, alternate_footprints=["A;B"]), "may not contain ';' or repeat"),
                         (dict(op, alternate_footprints=["MOL_MF3_2X02_RA_THT"]), "may not contain ';' or repeat")):
            with self.subTest(msg=msg), self.assertRaisesRegex(ValueError, msg):
                self.spec([bad])

    def test_entry_and_relink_and_no_erc(self):
        lines = self.spec([{"op": "attach_entry", "symbol": "RS422_RX", "entry": "DI", "net": "GND"},
                           {"op": "relink", "comp": "uid:LRAKJBWM", "link_library": "Res-Gen.DbLib",
                            "db_table": "Panasonic_ERJ_0402", "design_item": "ERJ-2RKF1002X", "footprint": "SMD-0402-RES",
                            "comment": "10k"},
                           {"op": "no_erc_pins", "comp": "des:U1", "pins": ["32", "33"]}])
        self.assertIn("ENTRY|RS422_RX|DI|GND|4|3", lines)
        self.assertIn("RELINK|uid:LRAKJBWM|Res-Gen.DbLib|Panasonic_ERJ_0402|ERJ-2RKF1002X|SMD-0402-RES|10k", lines)
        self.assertEqual(lines[lines.index("RELINK|uid:LRAKJBWM|Res-Gen.DbLib|Panasonic_ERJ_0402|ERJ-2RKF1002X|SMD-0402-RES|10k") + 1], "END")
        self.assertIn("NOERC|des:U1|32", lines)
        self.assertIn("NOERC|des:U1|33", lines)

    def test_refusals_name_the_problem(self):
        cases = [
            ([{"op": "smash"}], "unknown op"),
            ([{"op": "rename_label", "uid": "A"}], "missing 'text'"),
            ([{"op": "attach_pin", "comp": "R5", "pin": "1", "net": "X"}], "uid:<UNIQUEID>"),
            ([{"op": "rename_net", "old": "A", "new": "B|C"}], "may not contain"),
            ([{"op": "place", "designator": "R1", "symbol_library": "C:/nope.SchLib", "lib_ref": "R", "x": 0, "y": 0}],
             "existing .SchLib"),
            ([{"op": "place", "designator": "R1", "symbol_library": str(self.lib), "lib_ref": "R", "x": 1.5, "y": 0}],
             "whole number"),
            ([], "no ops"),
        ]
        for ops, msg in cases:
            with self.subTest(msg=msg):
                with self.assertRaisesRegex(ValueError, msg):
                    self.spec(ops)
        with self.assertRaisesRegex(ValueError, "existing .SchDoc"):
            se.edit_spec(str(self.sheet.with_name("missing.SchDoc")), [{"op": "delete", "uid": "A"}])


class AnnotateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.sheet = Path(self.tmp.name) / "A.SchDoc"
        self.sheet.write_bytes(b"x")

    def tearDown(self):
        self.tmp.cleanup()

    def test_spec_assigns_and_saves(self):
        lines = se.annotate_spec([str(self.sheet)], {"U1": "R7", "U2": "C3"})
        self.assertEqual(lines, [f"SHEET|{self.sheet}", "ASSIGN|U1|R7", "ASSIGN|U2|C3", "SAVE"])

    def test_duplicate_and_unannotated_designators_are_refused(self):
        with self.assertRaisesRegex(ValueError, "given to both"):
            se.annotate_spec([str(self.sheet)], {"U1": "R7", "U2": "R7"})
        with self.assertRaisesRegex(ValueError, "unannotated"):
            se.annotate_spec([str(self.sheet)], {"U1": "R?"})

    def test_auto_keeps_existing_and_numbers_the_rest_top_down_left_right(self):
        comps = [
            {"uid": "a", "designator": "R5", "x": 0, "y": 0},
            {"uid": "b", "designator": "R?", "x": 100, "y": 500},      # higher on the sheet: numbered first
            {"uid": "c", "designator": "R?", "x": 50, "y": 100},
            {"uid": "d", "designator": "R?", "x": 10, "y": 100},       # same row, further left: before c
            {"uid": "e", "designator": "SW?", "x": 0, "y": 0, "lib_ref": "CPC1017N"},
            {"uid": "f", "designator": "RL?", "x": 0, "y": -10, "lib_ref": "CPC1017N"},
            {"uid": "g", "designator": "SW1", "x": 0, "y": 0},
        ]
        got = se.auto_designators(comps, prefix_override={"CPC1017N": "SW"})
        self.assertEqual(got, {"b": "R1", "d": "R2", "c": "R3", "e": "SW2", "f": "SW3"})


class ToolValidationTest(unittest.TestCase):
    """The tools refuse a bad spec before anything reaches Altium."""

    def setUp(self):
        import main
        self.main = main
        self.calls = []

        async def refuse(command, params, **kw):
            self.calls.append(command)
            return {"success": False, "error": "bridge must not be called"}
        self.saved = main.altium_bridge.execute_command
        main.altium_bridge.execute_command = refuse

    def tearDown(self):
        self.main.altium_bridge.execute_command = self.saved

    def run_tool(self, coro):
        import asyncio
        import json
        return json.loads(asyncio.run(coro))

    def test_bad_ops_and_paths_never_reach_the_bridge(self):
        m = self.main
        r = self.run_tool(m.edit_schematic(None, "C:/nope/X.SchDoc", [{"op": "delete", "uid": "A"}]))
        self.assertFalse(r["success"])
        self.assertIn("existing .SchDoc", r["error"])
        r = self.run_tool(m.open_project(None, "C:/nope/X.PrjPcb"))
        self.assertIn("existing .PrjPcb", r["error"])
        r = self.run_tool(m.annotate_schematic(None, [], {"A": "R1"}))
        self.assertIn("no sheets", r["error"])
        self.assertEqual(self.calls, [])


class TwoPassTest(unittest.TestCase):
    """edit_schematic connects pins in two bridge calls: the ops and the sheet's geometry, then the planned drawing."""

    def setUp(self):
        import main
        self.main = main
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.sheet = base / "Board.SchDoc"
        self.sheet.write_bytes(b"x")
        self.lib = base / "parts.SchLib"
        self.lib.write_bytes(b"x")
        self.saved = main.altium_bridge.execute_command
        self.specs, self.answers = [], []

        async def bridge(command, params, files=None, ctx=None, **kw):
            self.specs.append(files[main.EDIT_SPEC_FILE].splitlines())
            return {"success": True, "result": self.answers.pop(0)}
        main.altium_bridge.execute_command = bridge

    def tearDown(self):
        self.main.altium_bridge.execute_command = self.saved
        self.tmp.cleanup()

    def run_tool(self, ops):
        import asyncio
        import json
        return json.loads(asyncio.run(self.main.edit_schematic(None, str(self.sheet), ops)))

    def placed(self, bound=True):
        return {"designator": "JP1", "uid": "AAAA", "x": 1200, "y": 1100, "pins": [], "unassigned_pins": [],
                "library": "dnbm.IntLib", "design_item": "HDR3",
                "models": [{"type": "PCBLIB", "name": "HDR_1X03", "current": True, "use_component_library": bound}]}

    def test_a_left_pin_is_labelled_on_its_side_and_the_sheet_saved_in_the_second_pass(self):
        geometry = {"parts": [{"key": 77, "designator": "JP1", "body": [1100, 950, 1300, 1250],
                               "pins": [["1", 1000, 1100, 2, 1100, 1100]], "texts": []}],
                    "labels": [], "ports": [], "wires": []}
        self.answers = [{"success": True, "sheet": str(self.sheet), "saved": "", "ops": 1, "errors": [],
                         "placed": [self.placed()], "pending": [[77, "1", "SDA", 1000, 1100, 2]],
                         "geometry": geometry},
                        {"success": True, "sheet": str(self.sheet), "saved": str(self.sheet), "ops": 0,
                         "errors": [], "placed": [], "pending": [], "geometry": None}]
        r = self.run_tool([{"op": "place", "designator": "JP1", "symbol_library": str(self.lib), "lib_ref": "HDR3",
                            "x": 1200, "y": 1100, "link_library": "dnbm.IntLib", "pins": {"1": "SDA"}}])
        first, second = self.specs
        self.assertEqual(first[-1], "GEOMETRY")
        self.assertNotIn("SAVE", first)
        self.assertEqual(second, [f"SHEET|{self.sheet}", "WIRE|1000|1100|600|1100", "LABEL|600|1100|0|0|SDA", "SAVE"])
        self.assertTrue(r["success"], r)
        self.assertEqual(r["saved"], str(self.sheet))
        self.assertEqual(r["connections"][0]["kind"], "label")
        self.assertNotIn("geometry", r)

    def test_a_model_left_on_any_library_is_an_error(self):
        self.answers = [{"success": True, "sheet": str(self.sheet), "saved": str(self.sheet), "ops": 1, "errors": [],
                         "placed": [self.placed(bound=False)], "pending": [], "geometry": None}]
        r = self.run_tool([{"op": "place", "designator": "JP1", "symbol_library": str(self.lib), "lib_ref": "HDR3",
                            "x": 1200, "y": 1100, "link_library": "dnbm.IntLib"}])
        self.assertFalse(r["success"])
        self.assertIn("JP1 PCBLIB HDR_1X03", r["errors"][0])
        self.assertEqual(self.specs[0][-1], "SAVE")          # nothing to connect: one pass, saved

    def test_the_request_carries_the_description_inside_the_place_block(self):
        self.answers = [{"success": True, "sheet": str(self.sheet), "saved": str(self.sheet), "ops": 1, "errors": [],
                         "placed": [self.placed()], "pending": [], "geometry": None}]
        r = self.run_tool([{"op": "place", "designator": "C1", "symbol_library": str(self.lib), "lib_ref": "CAP",
                            "x": 1000, "y": 1000, "link_library": "Cap-Gen.DbLib", "db_table": "Murata_GRM_0402",
                            "design_item": "GRM155R71H104KE14D", "description": "PROOF DESC 0.1UF",
                            "parameters": {"Proof": "1"}}])
        self.assertTrue(r["success"], r)
        spec = self.specs[0]
        i = next(k for k, l in enumerate(spec) if l.startswith("PLACE|"))
        self.assertEqual(spec[i], f"PLACE|C1|{self.lib}|CAP|1000|1000|0|0|Cap-Gen.DbLib|Murata_GRM_0402|"
                                  "GRM155R71H104KE14D||")
        self.assertEqual(spec[i + 1:], ["DESCRIPTION|PROOF DESC 0.1UF", "PARAM|Proof|1", "END", "SAVE"])

    def test_a_footprint_by_library_path_is_sent_and_read_back_as_bound(self):
        pcblib = self.lib.with_name("Res-Gen.PcbLib")
        pcblib.write_bytes(b"x")
        located = dict(self.placed(bound=False), designator="R7", library="Res-Gen.DbLib", design_item="RESISTOR",
                       models=[{"type": "PCBLIB", "name": "RES_0603_1608", "current": True,
                                "use_component_library": False, "library_path": str(pcblib)}])
        self.answers = [{"success": True, "sheet": str(self.sheet), "saved": str(self.sheet), "ops": 1, "errors": [],
                         "placed": [located], "pending": [], "geometry": None}]
        r = self.run_tool([{"op": "place", "designator": "R7", "symbol_library": str(self.lib), "lib_ref": "RESISTOR",
                            "x": 1000, "y": 1000, "link_library": "Res-Gen.DbLib", "footprint": "RES_0603_1608",
                            "footprint_library": str(pcblib)}])
        self.assertTrue(r["success"], r)                      # a library path is not library Any
        self.assertEqual(self.specs[0][-3:], [f"PLACE|R7|{self.lib}|RESISTOR|1000|1000|0|0|Res-Gen.DbLib|||"
                                              f"RES_0603_1608|||{pcblib}", "END", "SAVE"])

    def test_what_the_bridge_removed_comes_back(self):
        removed = [{"uid": "WIREAAAA", "kind": "wire", "text": ""}, {"uid": "LBLBBBBB", "kind": "label", "text": "SDA"}]
        self.answers = [{"success": True, "sheet": str(self.sheet), "saved": str(self.sheet), "ops": 1, "errors": [],
                         "placed": [], "removed": removed, "pending": [], "geometry": None}]
        r = self.run_tool([{"op": "detach_pin", "comp": "des:R1", "pin": "1"}])
        self.assertTrue(r["success"], r)
        self.assertEqual(r["removed"], removed)
        self.assertEqual(self.specs[0][-2:], ["DETACH|des:R1|1", "SAVE"])   # nothing to connect: one pass

    def test_unbound_models_ignore_unlinked_parts(self):
        part = dict(self.placed(bound=False), library="parts.SchLib")
        self.assertEqual(se.unbound_models([part]), [])
        self.assertEqual(se.unbound_models([self.placed(bound=False)]), ["JP1 PCBLIB HDR_1X03"])
        anywhere = self.placed(bound=False)
        anywhere["models"][0]["library_path"] = ""
        self.assertEqual(se.unbound_models([anywhere]), ["JP1 PCBLIB HDR_1X03"])


if __name__ == "__main__":
    unittest.main()
