"""What the bridge carries to Altium, at file level (pure Python, no Altium needed).

The operator's test string goes through each channel a tool writes: request.json, the edit and circuit
specs, the sandbox script. LoadFromFile decodes a file that starts with a UTF-8 byte-order mark as UTF-8, so
those carry every character; a script is read in the ANSI code page, so it carries what that code page can
and refuses the rest by name.

Run from server/:  python -m unittest tests.test_bridge_text -v
"""
import asyncio
import codecs
import ctypes
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

import main  # noqa: E402
from altium_files import ansi_script  # noqa: E402

TEXT = "Fühler 25 °C ß"
CP1252 = ctypes.windll.kernel32.GetACP() == 1252


def load_from_file(data: bytes) -> str:
    """How TStringList.LoadFromFile decodes a file: UTF-8 after a byte-order mark, else the ANSI code page."""
    if data.startswith(codecs.BOM_UTF8):
        return data[len(codecs.BOM_UTF8):].decode("utf-8")
    return data.decode("mbcs")


class BridgeTextTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.saved = {k: getattr(main, k) for k in ("EXCHANGE_DIR", "SANDBOX_PAS")}
        self.saved_run = main.altium_bridge.run_altium_script
        main.EXCHANGE_DIR = self.dir                 # the mailbox in a scratch folder

    def tearDown(self):
        for k, v in self.saved.items():
            setattr(main, k, v)
        main.altium_bridge.run_altium_script = self.saved_run
        self.tmp.cleanup()

    def test_request_json_carries_the_text_into_every_line_parser(self):
        async def no_altium():
            return False
        main.altium_bridge.run_altium_script = no_altium
        asyncio.run(main.altium_bridge._execute_command_locked(
            "place_components", {"label": TEXT, "items": [TEXT + "|1|2"]}, {}, 1, None))
        (request,) = self.dir.glob("mail/*/request.json")
        data = request.read_bytes()
        self.assertTrue(data.startswith(codecs.BOM_UTF8))
        lines = load_from_file(data).splitlines()
        # a scalar for JSONLineValue and an array item for the strip-the-quotes parsers: both hold the text itself
        self.assertIn(f'  "label": "{TEXT}",', lines)
        self.assertIn(f'    "{TEXT}|1|2"', lines)
        self.assertNotIn("\\u", data.decode("utf-8-sig"))

    def test_edit_spec_carries_the_text(self):
        sheet = self.dir / "A.SchDoc"
        sheet.write_bytes(b"x")

        async def no_altium():
            return False
        main.altium_bridge.run_altium_script = no_altium
        asyncio.run(main.edit_schematic(None, str(sheet), [{"op": "rename_net", "old": "T1", "new": TEXT}]))
        (spec,) = self.dir.glob("mail/*/edit_spec.txt")
        self.assertIn(f"RENAME_NET|T1|{TEXT}", load_from_file(spec.read_bytes()).splitlines())

    @unittest.skipUnless(CP1252, "needs ANSI code page 1252")
    def test_a_script_is_written_in_cp1252(self):
        self.assertEqual(ansi_script(f"S1 := '{TEXT}';"), f"S1 := '{TEXT}';".encode("cp1252"))

    def test_a_character_outside_the_code_page_is_refused_by_name(self):
        if not CP1252:
            self.skipTest("needs ANSI code page 1252")
        with self.assertRaisesRegex(ValueError, r"line 2 column 8 U\+03A9"):
            ansi_script("x := 1;\nS1 := 'Ω';")

    def test_run_altium_script_writes_the_sandbox_in_the_code_page_and_refuses_before_altium(self):
        if not CP1252:
            self.skipTest("needs ANSI code page 1252")
        main.SANDBOX_PAS = self.dir / "Sandbox.pas"
        main.SANDBOX_PAS.write_bytes(self.saved["SANDBOX_PAS"].read_bytes())
        launched = []
        saved_popen = main.subprocess.Popen
        main.subprocess.Popen = lambda *a, **k: launched.append(a)
        try:
            r = json.loads(asyncio.run(main._run_altium_script(None, "S1 := 'Ω';", 0)))
            self.assertFalse(r["success"])
            self.assertIn("U+03A9", r["error"])
            self.assertEqual(launched, [])
            asyncio.run(main._run_altium_script(None, f"ResultText := '{TEXT}';", 0))
            self.assertEqual(len(launched), 1)
        finally:
            main.subprocess.Popen = saved_popen
        self.assertIn(f"ResultText := '{TEXT}';".encode("cp1252"), main.SANDBOX_PAS.read_bytes())


if __name__ == "__main__":
    unittest.main()
