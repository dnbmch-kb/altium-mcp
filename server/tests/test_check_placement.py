"""check_placement: the request it sends, the courtyard measure among it (pure Python, bridge faked).

Run from server/:  python -m unittest tests.test_check_placement -v
"""
import asyncio
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import main  # noqa: E402


class ParamsTest(unittest.TestCase):
    def test_the_request(self):
        self.assertEqual(main.placement_check_params(["J1", "C3"], 6, True),
                         {"clearance_mils": 6.0, "courtyard": True, "designators": ["J1", "C3"]})
        self.assertEqual(main.placement_check_params(None, 5.9, False), {"clearance_mils": 5.9, "courtyard": False})

    def test_refusals(self):
        cases = [((None, 0, False), "above 0"), ((None, -1, False), "above 0"), ((None, "6", False), "a number"),
                 ((None, True, False), "a number"), ((None, 6, "yes"), "courtyard must be true or false")]
        for args, msg in cases:
            with self.subTest(msg=msg, args=args):
                with self.assertRaisesRegex(ValueError, msg):
                    main.placement_check_params(*args)


class ToolTest(unittest.TestCase):
    def setUp(self):
        self.sent = []
        self.saved = main.altium_bridge.execute_command

        async def bridge(command, params):
            self.sent.append((command, params))
            return {"success": True, "result": {"measure": "courtyard", "violation_count": 0, "violations": []}}
        main.altium_bridge.execute_command = bridge

    def tearDown(self):
        main.altium_bridge.execute_command = self.saved

    def test_the_courtyard_measure_is_asked_for_by_name(self):
        r = json.loads(asyncio.run(main.check_placement(None, ["J1"], courtyard=True)))
        self.assertEqual(self.sent, [("check_placement", {"clearance_mils": 6.0, "courtyard": True,
                                                          "designators": ["J1"]})])
        self.assertEqual(r["measure"], "courtyard")
        asyncio.run(main.check_placement(None))
        self.assertEqual(self.sent[-1][1], {"clearance_mils": 6.0, "courtyard": False})

    def test_a_refused_request_reaches_no_bridge(self):
        r = json.loads(asyncio.run(main.check_placement(None, ["J1"], clearance_mils=0)))
        self.assertFalse(r["success"])
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
