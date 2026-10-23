"""get_screenshot's canvas check (pure Python, no Altium needed).

Run from server/:  python -m unittest tests.test_screenshot -v
"""
import os
import random
import sys
import unittest

from PIL import Image

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import main  # noqa: E402


class CanvasTest(unittest.TestCase):
    def test_an_unpainted_canvas_counts_one_colour(self):
        frame = Image.new("RGB", (800, 600), (40, 40, 40))
        frame.paste((240, 240, 240), (0, 0, 800, 60))           # menus and panels around a black canvas
        self.assertEqual(main.canvas_colours(frame), 1)

    def test_a_painted_view_counts_many(self):
        rng = random.Random(1)
        frame = Image.new("RGB", (800, 600))
        frame.putdata([(rng.randrange(256), rng.randrange(256), rng.randrange(256)) for _ in range(800 * 600)])
        self.assertEqual(main.canvas_colours(frame), 17)


if __name__ == "__main__":
    unittest.main()
