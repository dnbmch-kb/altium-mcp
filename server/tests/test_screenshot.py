"""get_screenshot's print path (pure Python; the bridge is faked): the OutJob it hands the bridge, the PDF
rasterised, a blank print and a capture identical to the previous one refused with the image attached.

Run from server/:  python -m unittest tests.test_screenshot -v
"""
import asyncio
import configparser
import json
import os
import random
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import main  # noqa: E402
import screenshot  # noqa: E402


def board_picture(seed=1):
    rng = random.Random(seed)
    img = Image.new("RGB", (600, 300), "white")
    d = ImageDraw.Draw(img)
    for _ in range(40):
        x, y = rng.randrange(560), rng.randrange(260)
        d.rectangle((x, y, x + 30, y + 30), fill=(rng.randrange(256), rng.randrange(256), rng.randrange(256)))
    return img


class OutJobTest(unittest.TestCase):
    def ini(self, kind):
        cfg = configparser.ConfigParser(interpolation=None, strict=False)
        cfg.optionxform = str
        cfg.read_string(screenshot.outjob(kind).replace("{LAYERS}", ""))   # the bridge fills it
        return cfg

    def test_a_pcb_print_published_to_the_request_folder_without_opening_it(self):
        cfg = self.ini("pcb")
        group, publish = cfg["OutputGroup1"], cfg["PublishSettings"]
        self.assertEqual((group["OutputMedium1"], group["OutputMedium1_Type"]), ("PDF", "Publish"))
        self.assertEqual(group["OutputType1"], "PCB Print")
        self.assertEqual(group["OutputDocumentPath1"], "{DOC}")
        self.assertIn("{AREA}", group["Configuration1_Item1"])
        self.assertIn("Record=PcbPrintOut", group["Configuration1_Item2"])
        self.assertEqual(publish["OutputFilePath1"], "{DIR}shot.pdf")
        self.assertEqual((publish["OpenOutput1"], publish["PromptOverwrite1"], publish["FitPCBPrintSizeToDoc1"]),
                         ("0", "0", "1"))
        self.assertIn("\r\n{LAYERS}\r\n", screenshot.outjob("pcb"))

    def test_a_schematic_print_has_no_layers_or_area(self):
        text = screenshot.outjob("sch")
        self.assertIn("OutputType1=Schematic Print", text)
        self.assertNotIn("{LAYERS}", text)
        self.assertNotIn("{AREA}", text)
        with self.assertRaises(ValueError):
            screenshot.outjob("3d")


class PictureTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_the_pdf_is_rasterised_to_the_asked_width(self):
        pdf = self.dir / "shot.pdf"
        board_picture().save(pdf)
        img = screenshot.render_pdf(pdf, 1200)
        self.assertEqual(max(img.size), 1200)
        self.assertGreater(screenshot.colours(img), 2)

    def test_a_blank_print_counts_one_colour(self):
        self.assertEqual(screenshot.colours(Image.new("RGB", (100, 100), "white")), 1)

    def test_an_identical_capture_is_named_with_the_previous_one(self):
        what = {"kind": "pcb", "document": "C:/b/A.PcbDoc"}
        self.assertIsNone(screenshot.compare_with_last(self.dir, board_picture(1), what))
        self.assertIsNone(screenshot.compare_with_last(self.dir, board_picture(2), what))
        previous = screenshot.compare_with_last(self.dir, board_picture(2), what)
        self.assertEqual(previous["document"], "C:/b/A.PcbDoc")
        self.assertIn("pixel-identical", screenshot.identical_error(previous))


class GetScreenshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.saved = (main.altium_bridge.execute_command, main.EXCHANGE_DIR)
        main.EXCHANGE_DIR = self.dir
        self.sent = []
        self.picture = board_picture

        async def bridge(command, params, files=None, ctx=None, **kw):
            self.sent.append((command, params, files))
            mail = self.dir / "mail" / "1-1-1"
            mail.mkdir(parents=True, exist_ok=True)
            self.picture().save(mail / "shot.pdf")
            return {"success": True, "mail": str(mail), "result": {
                "success": True, "view_type": params["view_type"], "document": "C:/b/A.PcbDoc",
                "project": "C:/b/A.PrjPcb", "pdf": str(mail / "shot.pdf"), "zoomed_component_count": 1,
                "missing_designators": "U9", "layers": "BottomLayer,TopLayer,TopOverlay"}}
        main.altium_bridge.execute_command = bridge

    def tearDown(self):
        main.altium_bridge.execute_command, main.EXCHANGE_DIR = self.saved
        self.tmp.cleanup()

    def shot(self, **kw):
        return asyncio.run(main.get_screenshot(None, **kw))

    def test_a_board_comes_back_printed_with_what_was_printed(self):
        meta, image = self.shot(view_type="pcb", zoom_to=["U1", "U9"], width=800)
        meta = json.loads(meta)
        self.assertTrue(meta["success"], meta)
        command, params, files = self.sent[0]
        self.assertEqual((command, params), ("take_view_screenshot", {"view_type": "pcb", "designators": ["U1", "U9"]}))
        self.assertIn(b"OutputType1=PCB Print", files[screenshot.TEMPLATE])
        self.assertEqual(meta["missing_designators"], ["U9"])
        self.assertEqual(meta["layers"], ["BottomLayer", "TopLayer", "TopOverlay"])
        self.assertEqual(max(meta["width"], meta["height"]), 800)
        self.assertEqual(image.data[:4], b"\x89PNG")

    def test_the_same_picture_twice_is_refused_the_second_time(self):
        self.assertTrue(json.loads(self.shot(view_type="pcb")[0])["success"])
        meta = json.loads(self.shot(view_type="pcb")[0])
        self.assertFalse(meta["success"])
        self.assertTrue(meta["identical_to_previous"])

    def test_a_blank_print_fails(self):
        self.picture = lambda: Image.new("RGB", (400, 200), "white")
        meta = json.loads(self.shot(view_type="sch")[0])
        self.assertFalse(meta["success"])
        self.assertIn("blank", meta["error"])

    def test_refusals_never_reach_altium(self):
        for kw, msg in (({"view_type": "3d"}, "view_type is"),
                        ({"view_type": "pcb", "document": "A.PcbDoc"}, "full path"),
                        ({"view_type": "sch", "zoom_to": ["U1"]}, "PCB print only")):
            with self.subTest(msg=msg):
                self.assertIn(msg, json.loads(self.shot(**kw))["error"])
        self.assertEqual(self.sent, [])


if __name__ == "__main__":
    unittest.main()
