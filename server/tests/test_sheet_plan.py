"""sheet_plan: where edit_schematic draws a connection, from the sheet as Altium reports it (pure Python).

Run from server/:  python -m unittest tests.test_sheet_plan -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import sheet_plan as sp  # noqa: E402

POWER = {"GND": (5, 3), "3V3": (2, 1)}


def part(key, des, body, pins, texts=()):
    """pins: (name, x, y, d) with a 100 mil pin; the body end is 100 mil back against the direction."""
    out = []
    for name, x, y, d in pins:
        dx, dy = sp.DIRS[d]
        out.append([name, x, y, d, x - dx * 100, y - dy * 100])
    return {"key": key, "designator": des, "body": list(body), "pins": out, "texts": [list(t) for t in texts]}


# JP1 as on the raw-head adapter: a 1x3 header whose pins point left, its body to their right
JP1 = part(1, "JP1", (1100, 950, 1300, 1250), [("1", 1000, 1200, 2), ("2", 1000, 1100, 2), ("3", 1000, 1000, 2)],
           [(1100, 1260, 0, 0, "JP1")])


class TextBoxTest(unittest.TestCase):
    def test_justification_and_turn(self):
        self.assertEqual(sp.text_box(0, 0, 0, 0, "AB"), (0, 0, 140, 90))
        self.assertEqual(sp.text_box(0, 0, 0, 2, "AB"), (-140, 0, 0, 90))          # bottom right: ends at the anchor
        self.assertEqual(sp.text_box(0, 0, 1, 0, "AB"), (-90, 0, 0, 140))          # turned: reads up from it
        self.assertEqual(sp.text_box(0, 0, 1, 2, "AB"), (-90, -140, 0, 0))         # turned, bottom right: ends at it


class PlanTest(unittest.TestCase):
    def plan(self, parts, pending, **free):
        return sp.plan(dict(parts=parts, **free), pending, POWER)

    def test_labels_sit_on_the_pin_side_right_justified_on_a_left_pin(self):
        p = self.plan([JP1], [[1, "1", "SDA", 1000, 1200, 2], [1, "3", "SCL", 1000, 1000, 2]])
        self.assertEqual(p["labels"], [[900, 1200, 0, 2, "SDA"], [900, 1000, 0, 2, "SCL"]])
        self.assertEqual(p["wires"], [[1000, 1200, 900, 1200], [1000, 1000, 900, 1000]])
        self.assertEqual(p["overlaps"], [])
        self.assertEqual(p["errors"], [])
        for c in p["connections"]:                 # every label box left of the stub end, clear of the body
            self.assertLessEqual(c["boxes"][0][2], 900)

    def test_a_sideways_power_pin_among_neighbours_takes_a_label(self):
        p = self.plan([JP1], [[1, "1", "SDA", 1000, 1200, 2], [1, "2", "GND", 1000, 1100, 2],
                              [1, "3", "SCL", 1000, 1000, 2]])
        self.assertEqual(p["ports"], [])
        self.assertIn([900, 1100, 0, 2, "GND"], p["labels"])
        self.assertEqual(p["overlaps"], [])

    def test_a_down_pin_label_is_turned_and_ends_at_the_stub(self):
        u1 = part(2, "U1", (1900, 1100, 2300, 1500), [("5", 2000, 1000, 3)])
        p = self.plan([u1], [[2, "5", "EN", 2000, 1000, 3]])
        self.assertEqual(p["labels"], [[2000, 900, 1, 2, "EN"]])
        self.assertEqual(p["connections"][0]["boxes"][0], [1910, 760, 2000, 900])

    def test_neighbouring_pins_on_one_power_net_share_a_wire_and_one_port(self):
        u2 = part(3, "U2", (2900, 1100, 3300, 1500), [("4", 3000, 1000, 3), ("5", 3100, 1000, 3),
                                                     ("6", 3200, 1000, 3)])
        p = self.plan([u2], [[3, "4", "GND", 3000, 1000, 3], [3, "5", "GND", 3100, 1000, 3],
                             [3, "6", "GND", 3200, 1000, 3]])
        self.assertEqual(p["ports"], [[3200, 900, 3, 5, "GND"]])
        self.assertEqual(p["junctions"], [[3100, 900]])
        self.assertIn([3000, 900, 3200, 900], p["wires"])
        self.assertEqual(p["connections"][0]["pins"], ["4", "5", "6"])
        self.assertEqual(p["labels"], [])

    def test_a_supply_port_turns_the_pin_way_when_up_is_taken(self):
        r1 = part(4, "R1", (3800, 950, 3900, 1050), [("1", 4000, 1000, 0)])
        blocker = [4120, 1060, 0, 0, "BUSY"]        # a label right above the stub end
        p = self.plan([r1], [[4, "1", "3V3", 4000, 1000, 0]], labels=[blocker])
        self.assertEqual(p["ports"], [[4100, 1000, 0, 2, "3V3"]])
        self.assertEqual(p["overlaps"], [])
        free = self.plan([r1], [[4, "1", "3V3", 4000, 1000, 0]])
        self.assertEqual(free["ports"], [[4100, 1000, 1, 2, "3V3"]])

    def test_a_spot_on_a_foreign_wire_is_never_taken(self):
        r2 = part(5, "R2", (4800, 950, 4900, 1050), [("1", 5000, 1000, 0)])
        wire = [5100, 800, 5100, 1200]             # runs through the 100 mil stub end
        p = self.plan([r2], [[5, "1", "NET_A", 5000, 1000, 0]], wires=[wire])
        self.assertEqual(p["labels"], [[5300, 1000, 0, 0, "NET_A"]])
        self.assertEqual(p["errors"], [])

    def test_a_connection_that_can_only_touch_another_pin_is_refused(self):
        r3 = part(6, "R3", (5800, 950, 5900, 1050), [("1", 6000, 1000, 0)])
        # a foreign part's pin ends on every stub end the planner may use
        wall = part(7, "X1", (6700, 900, 6800, 1100), [("1", 6100, 1000, 2), ("2", 6300, 1000, 2),
                                                       ("3", 6500, 1000, 2)])
        p = self.plan([r3, wall], [[6, "1", "NET_B", 6000, 1000, 0]])
        self.assertEqual(p["labels"], [])
        self.assertEqual(p["wires"], [])
        self.assertIn("pin X1.1", p["errors"][0])

    def test_text_that_cannot_clear_is_reported(self):
        r4 = part(8, "R4", (6800, 950, 6900, 1050), [("1", 7000, 1000, 0)])
        big = part(9, "U9", (7050, 900, 8000, 1300), [])   # a body right of the pin covers every label spot
        p = self.plan([r4, big], [[8, "1", "LONG_NET", 7000, 1000, 0]])
        self.assertEqual(p["labels"], [[7100, 1000, 0, 0, "LONG_NET"]])
        self.assertTrue(any("the body of U9" in o for o in p["overlaps"]))

    def test_draw_spec(self):
        p = self.plan([JP1], [[1, "2", "GND", 1000, 1100, 2]])
        lines = sp.draw_spec("C:/x/A.SchDoc", p)
        self.assertEqual(lines[0], "SHEET|C:/x/A.SchDoc")
        self.assertEqual(lines[-1], "SAVE")
        self.assertIn("WIRE|1000|1100|900|1100", lines)
        self.assertIn("LABEL|900|1100|0|2|GND", lines)
        self.assertNotIn("SAVE", sp.draw_spec("C:/x/A.SchDoc", p, save=False))


if __name__ == "__main__":
    unittest.main()
