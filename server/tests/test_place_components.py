"""place_components: the side convention, the request sent, and the read-back check (pure Python, bridge faked).

Run from server/:  python -m unittest tests.test_place_components -v
"""
import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import main  # noqa: E402


class ConventionTest(unittest.TestCase):
    def test_a_bottom_part_is_stored_turned_180(self):
        # the raw-head adapter's bottom parts: J4 at 90, C18-C20 at 270 (KiCad capture) landed on their pads only so
        self.assertEqual(main.altium_rotation(90, "bottom"), 270)
        self.assertEqual(main.altium_rotation(270, "bottom"), 90)
        self.assertEqual(main.altium_rotation(90, "top"), 90)
        for r in (0, 45, 90, 180, 270):
            self.assertEqual(main.side_rotation(main.altium_rotation(r, "bottom"), "bottom"), r)

    def test_the_bottom_angle_altium_takes_at_0_and_180(self):
        # 90 and 270 cannot tell + 180 from 360 - angle; pad 1 of IC_SOT23_5 read back at 0, 90 and 180 on a
        # scratch board (2026-10-23): Altium mirrors a bottom part top to bottom, then turns it, so + 180
        self.assertEqual(main.altium_rotation(0, "bottom"), 180)
        self.assertEqual(main.altium_rotation(180, "bottom"), 0)


class PlaceTest(unittest.TestCase):
    def setUp(self):
        self.sent, self.answer = [], None
        self.saved = main.altium_bridge.execute_command

        async def bridge(command, params):
            self.sent.append((command, params))
            return {"success": True, "result": self.answer}
        main.altium_bridge.execute_command = bridge

    def tearDown(self):
        main.altium_bridge.execute_command = self.saved

    def run_tool(self, placements, board=""):
        return json.loads(asyncio.run(main.place_components(None, placements, board)))

    def test_a_bottom_part_is_sent_with_altiums_angle_and_read_back_in_both(self):
        self.answer = {"board": "C:/b/A.PcbDoc", "placed_count": 1, "missing_designators": [],
                       "components": [{"designator": "J4", "x": 1300, "y": 1000, "rotation": 270,
                                       "layer": "Bottom Layer"}]}
        r = self.run_tool([{"designator": "J4", "x": 1300, "y": 1000, "rotation": 90, "side": "bottom"}],
                          "C:/b/A.PcbDoc")
        self.assertEqual(self.sent[0][1], {"placements": ["J4|1300.0|1000.0|270.0|bottom"], "board": "C:\\b\\A.PcbDoc"})
        self.assertTrue(r["success"], r)
        self.assertEqual(r["components"][0], {"designator": "J4", "x": 1300, "y": 1000, "rotation": 90.0,
                                              "side": "bottom", "altium_rotation": 270.0})

    def test_a_rotation_the_board_did_not_hold_is_an_error(self):
        # LED240: the tool reported R1 at 180 while the board kept 0
        self.answer = {"board": "C:/b/LED240.PcbDoc", "placed_count": 1, "missing_designators": [],
                       "components": [{"designator": "R1", "x": 0, "y": -551.18, "rotation": 0, "layer": "Top Layer"}]}
        r = self.run_tool([{"designator": "R1", "x": 0, "y": -551.18, "rotation": 180, "side": "top"}])
        self.assertFalse(r["success"])
        self.assertEqual(r["errors"], ["R1 holds rotation 0 (Altium 0), not 180"])

    def test_refusals(self):
        cases = [([{"designator": "R1", "x": 0, "y": 0, "rotation": 90}], "needs its side"),
                 ([{"designator": "R1", "x": 0, "y": 0, "layer": "bottom"}], "'layer' is now 'side'"),
                 ([{"designator": "R1", "x": 0, "y": 0, "side": "under"}], "side must be"),
                 ([{"designator": "R|1", "x": 0, "y": 0}], "must not contain")]
        for placements, msg in cases:
            with self.subTest(msg=msg):
                self.assertIn(msg, self.run_tool(placements)["error"])
        self.assertIn("full path of a .PcbDoc", self.run_tool([{"designator": "R1", "x": 0, "y": 0}], "A.PcbDoc")["error"])
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
