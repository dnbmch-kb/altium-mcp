"""sheet_plan: where edit_schematic draws a connection, from the sheet as Altium reports it (pure Python).

The expectations are the house sheet (c:/Projects/#Notes/guides/schematic_style.md): horizontal labels on stubs
sized to their text, supplies up, ground down with its name hidden, never a sideways port.

Run from server/:  python -m unittest tests.test_sheet_plan -v
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import schematic_edit as se  # noqa: E402
import sheet_plan as sp  # noqa: E402

POWER = se.power_table()


def part(key, des, body, pins, texts=(), uid=None):
    """pins: (name, x, y, d) with a 100 mil pin; the body end is 100 mil back against the direction."""
    out = []
    for name, x, y, d in pins:
        dx, dy = sp.DIRS[d]
        out.append([name, x, y, d, x - dx * 100, y - dy * 100])
    return {"key": key, "uid": uid or f"U{key}", "designator": des, "body": list(body), "pins": out,
            "texts": [list(t) for t in texts]}


# JP1 as on the raw-head adapter: a 1x3 header whose pins point left, its body to their right
JP1 = part(1, "JP1", (1100, 950, 1300, 1250), [("1", 1000, 1200, 2), ("2", 1000, 1100, 2), ("3", 1000, 1000, 2)],
           [(1100, 1260, 0, 0, "JP1")])


class TextBoxTest(unittest.TestCase):
    def test_justification_and_turn(self):
        self.assertEqual(sp.text_box(0, 0, 0, 0, "AB"), (0, 0, 140, 90))
        self.assertEqual(sp.text_box(0, 0, 0, 2, "AB"), (-140, 0, 0, 90))          # bottom right: ends at the anchor
        self.assertEqual(sp.text_box(0, 0, 1, 0, "AB"), (-90, 0, 0, 140))          # turned: reads up from it

    def test_a_label_stub_is_sized_to_its_text(self):
        self.assertEqual(sp.stub_for("SDA"), 400)          # 3 x 60 + 200 = 380, on the 50 mil grid
        self.assertEqual(sp.stub_for("UART0_RXD"), 750)    # 9 x 60 + 200 = 740


class PlanTest(unittest.TestCase):
    def plan(self, parts, pending, draws=(), **free):
        return sp.plan(dict(parts=parts, **free), pending, POWER, draws)

    def no_sideways_port(self, p):
        self.assertTrue(all(port[2] in (1, 3) for port in p["ports"]), p["ports"])

    def test_a_left_pin_label_sits_at_the_far_end_and_runs_back_toward_the_pin(self):
        p = self.plan([JP1], [[1, "1", "SDA", 1000, 1200, 2], [1, "3", "SCL", 1000, 1000, 2]])
        self.assertEqual(p["labels"], [[600, 1200, 0, 0, "SDA"], [600, 1000, 0, 0, "SCL"]])
        self.assertEqual(p["wires"], [[1000, 1200, 600, 1200], [1000, 1000, 600, 1000]])
        self.assertEqual(p["overlaps"], [])
        self.assertEqual(p["errors"], [])

    def test_a_right_pin_label_starts_near_the_pin(self):
        u1 = part(2, "U1", (1500, 900, 1900, 1300), [("4", 2000, 1000, 0)])
        p = self.plan([u1], [[2, "4", "EN", 2000, 1000, 0]])
        self.assertEqual(p["labels"], [[2100, 1000, 0, 0, "EN"]])
        self.assertEqual(p["wires"], [[2000, 1000, 2350, 1000]])

    def test_an_up_or_down_pin_label_stays_horizontal(self):
        u1 = part(2, "U1", (1900, 1100, 2300, 1500), [("5", 2000, 1000, 3)])
        p = self.plan([u1], [[2, "5", "EN", 2000, 1000, 3]])
        self.assertEqual(p["labels"], [[2000, 900, 0, 0, "EN"]])
        self.assertEqual(p["wires"], [[2000, 1000, 2000, 900]])

    def test_a_sideways_ground_pin_never_takes_a_sideways_port(self):
        p = self.plan([JP1], [[1, "1", "SDA", 1000, 1200, 2], [1, "2", "GND", 1000, 1100, 2],
                              [1, "3", "SCL", 1000, 1000, 2]])
        self.no_sideways_port(p)
        self.assertEqual(p["overlaps"], [])
        gnd = next(c for c in p["connections"] if c["net"] == "GND")
        if gnd["kind"] == "port":
            self.assertTrue(all(port[3:] == [4, "GND", False] for port in p["ports"]))

    def test_neighbouring_ground_pins_share_a_wire_and_one_hidden_port(self):
        u2 = part(3, "U2", (2900, 1100, 3300, 1500), [("4", 3000, 1000, 3), ("5", 3100, 1000, 3),
                                                     ("6", 3200, 1000, 3)])
        p = self.plan([u2], [[3, "4", "GND", 3000, 1000, 3], [3, "5", "GND", 3100, 1000, 3],
                             [3, "6", "GND", 3200, 1000, 3]])
        self.assertEqual(p["ports"], [[3100, 900, 3, 4, "GND", False]])
        self.assertEqual(p["junctions"], [[3100, 900]])
        self.assertIn([3000, 900, 3200, 900], p["wires"])
        self.assertEqual(p["connections"][0]["pins"], ["4", "5", "6"])

    def test_a_supply_port_moves_out_but_keeps_pointing_up(self):
        r1 = part(4, "R1", (3800, 950, 3900, 1050), [("1", 4000, 1000, 0)])
        p = self.plan([r1], [[4, "1", "3V3", 4000, 1000, 0]], labels=[[4100, 1060, 0, 0, "B"]])
        self.assertEqual(p["ports"], [[4300, 1000, 1, 2, "3V3", True]])
        self.assertEqual(p["overlaps"], [])

    def test_a_ground_pin_pointing_up_takes_a_jog_and_a_port_pointing_down(self):
        u3 = part(5, "U3", (4900, 600, 5100, 900), [("8", 5000, 1000, 1)])
        p = self.plan([u3], [[5, "8", "GND", 5000, 1000, 1]])
        self.assertEqual(p["ports"], [[5200, 1100, 3, 4, "GND", False]])
        self.assertEqual(p["wires"], [[5000, 1000, 5000, 1100], [5000, 1100, 5200, 1100]])

    def test_a_connection_that_can_only_touch_another_pin_is_refused(self):
        r3 = part(6, "R3", (5800, 950, 5900, 1050), [("1", 6000, 1000, 0)])
        wall = part(7, "X1", (6700, 900, 6800, 1100), [("1", 6300, 1000, 2)])   # its pin end on every stub
        p = self.plan([r3, wall], [[6, "1", "NET_B", 6000, 1000, 0]])
        self.assertEqual(p["labels"], [])
        self.assertIn("pin X1.1", p["errors"][0])

    def test_wire_junction_and_port_ops_resolve_pin_ends_and_are_avoided(self):
        r1 = part(8, "R1", (7900, 950, 8100, 1050), [("1", 7800, 1000, 2), ("2", 8200, 1000, 0)])
        c1 = part(9, "C1", (8950, 900, 9050, 1100), [("1", 9000, 1200, 1), ("2", 9000, 800, 3)], uid="CCCC")
        draws = se.draw_ops([
            {"op": "wire", "points": [{"comp": "des:R1", "pin": "2"}, [9000, 1000], {"comp": "uid:CCCC", "pin": "1"}]},
            {"op": "port", "net": "GND", "at": {"comp": "des:C1", "pin": "2"}, "orient": 3},
            {"op": "wire", "points": [[0, 0], [100, 100]]},
            {"op": "junction", "at": {"comp": "des:R9", "pin": "1"}}])
        p = self.plan([r1, c1], [[8, "1", "SIG", 7800, 1000, 2]], draws)
        self.assertEqual(p["wires"][:2], [[8200, 1000, 9000, 1000], [9000, 1000, 9000, 1200]])
        self.assertEqual(p["ports"], [[9000, 800, 3, 4, "GND", False]])
        self.assertTrue(any("horizontal or vertical" in e for e in p["errors"]))
        self.assertTrue(any("no pin des:R9.1" in e for e in p["errors"]))
        self.assertEqual(p["labels"], [[7400, 1000, 0, 0, "SIG"]])

    def test_draw_spec(self):
        p = self.plan([JP1], [[1, "1", "SDA", 1000, 1200, 2]],
                      se.draw_ops([{"op": "port", "net": "GND", "at": [500, 500], "orient": 3}]))
        lines = sp.draw_spec("C:/x/A.SchDoc", p)
        self.assertEqual(lines[0], "SHEET|C:/x/A.SchDoc")
        self.assertEqual(lines[-1], "SAVE")
        self.assertIn("WIRE|1000|1200|600|1200", lines)
        self.assertIn("LABEL|600|1200|0|0|SDA", lines)
        self.assertIn("PORT|500|500|3|4|0|GND", lines)
        self.assertNotIn("SAVE", sp.draw_spec("C:/x/A.SchDoc", p, save=False))


class DrawOpsTest(unittest.TestCase):
    def test_refusals_name_the_problem(self):
        cases = [({"op": "wire", "points": [[0, 0]]}, "two or more"),
                 ({"op": "wire", "points": [[0, 0], {"comp": "R1", "pin": "1"}]}, "uid:<UNIQUEID>"),
                 ({"op": "port", "net": "GND", "at": [0, 0], "orient": 4}, "orient is 0 right"),
                 ({"op": "junction", "at": [0.5, 0]}, "whole number"),
                 ({"op": "port", "net": "GND", "at": [0, 0]}, "missing 'orient'")]
        for op, msg in cases:
            with self.subTest(msg=msg):
                with self.assertRaisesRegex(ValueError, msg):
                    se.draw_ops([op])


if __name__ == "__main__":
    unittest.main()
