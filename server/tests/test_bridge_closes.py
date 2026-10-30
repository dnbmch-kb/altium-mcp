"""The bridge closes what it opens: checked on the DelphiScript sources (pure Python; the live proof runs in Altium).

Run from the repo root:  python -m unittest server.tests.test_bridge_closes -v

A read tool opens a library only through OpenLibraryDocument, which lists it only when it opened it; the request
closes that list before every answer, a failed command's too. Run closes the bridge's own script project last and
the sandbox closes its own: a script runs on after its project closes (#Notes/guides/altium_scripting.md), but
only Altium's routines are known to, so nothing of the project is called from the closing loop on. The answer is
renamed into place after the close, so a server holds the lock until the bridge is done.
"""
import re
import unittest
from pathlib import Path

SERVER = Path(__file__).resolve().parent.parent
BRIDGE = SERVER / "AltiumScript"
SANDBOX = SERVER / "SandboxScript" / "Sandbox.pas"
CLOSE = "WorkspaceManager:CloseObject"
# The tools that write into a library: it is their output, left open for the operator to review and save
LIBRARY_WRITERS = {"CreateFootprintsBatch", "CreateSymbolsBatch"}
HEADER = re.compile(r"^(?:function|procedure)\s+(\w+)", re.M | re.I)


def strip(text, strings):
    """Pascal text without comments; with strings=True its string literals blanked too."""
    out, i, n = [], 0, len(text)
    while i < n:
        if text[i] == "'":
            j = text.find("'", i + 1)
            j = n - 1 if j < 0 else j
            out.append("''" if strings else text[i:j + 1])
            i = j + 1
        elif text.startswith("//", i):
            j = text.find("\n", i)
            i = n if j < 0 else j
        elif text[i] == "{":
            j = text.find("}", i + 1)
            i = n if j < 0 else j + 1
        else:
            out.append(text[i])
            i += 1
    return "".join(out)


def unit(path):
    return strip(path.read_bytes().decode("cp1252").replace("\r\n", "\n"), strings=False)


def routines(text):
    """{name: text} of a unit's routines, each from its header to the next one."""
    heads = list(HEADER.finditer(text))
    return {m.group(1): text[m.start():heads[k + 1].start() if k + 1 < len(heads) else len(text)]
            for k, m in enumerate(heads)}


def declared(text):
    """The routines a unit declares and the names of its unit-level constants and variables."""
    first = HEADER.search(text)
    names = set(routines(text))
    for line in text[:first.start() if first else len(text)].splitlines():
        m = re.match(r"\s*(\w+(?:\s*,\s*\w+)*)\s*[:=]", line)
        if m:
            names.update(n.strip() for n in m.group(1).split(","))
    return {n.lower() for n in names}


def bridge_units():
    project = (BRIDGE / "Altium_API.PrjScr").read_text(encoding="latin-1")
    return {p: unit(BRIDGE / p) for p in re.findall(r"^DocumentPath=(\S+\.pas)\s*$", project, re.M)}


def called_after_the_close(run):
    """The names Run's text uses from the loop holding its first project close to its end."""
    at = run.index(CLOSE)
    loop = max(m.start() for m in re.finditer(r"\bfor\b", run[:at], re.I))
    return {n.lower() for n in re.findall(r"[A-Za-z_]\w*", strip(run[loop:], strings=True))}


class LibraryCloseTest(unittest.TestCase):
    def test_a_read_tool_opens_a_library_only_through_open_library_document(self):
        for name, text in bridge_units().items():
            for routine, body in routines(text).items():
                if re.search(r"Client\.OpenDocument\(\s*'(?:SchLib|PcbLib)'", body, re.I):
                    self.assertIn(routine, LIBRARY_WRITERS, f"{name} {routine} opens a library outside the record")

    def test_open_library_document_lists_a_library_only_when_it_opened_it(self):
        # the open and the listing in one block, entered only when the document was not open before
        body = routines(unit(BRIDGE / "other_utils.pas"))["OpenLibraryDocument"]
        self.assertRegex(body, re.compile(r"Result := FindOpenDocument\(Path\);\s*if \(Result = nil\) then\s*begin"
                                          r"(?:(?!\bend\b).)*Client\.OpenDocument\(Kind, Path\)"
                                          r"(?:(?!\bend\b).)*OpenedLibraries\.Add\(Path\)", re.S))

    def test_every_answer_comes_after_the_library_close(self):
        # an exception can come from anywhere in the try block, so the except branch closes on its own
        body = routines(unit(BRIDGE / "Altium_API.pas"))["HandleRequest"]
        answers = [m.start() for m in re.finditer(r"\bWriteResponse\(", body)]
        self.assertGreaterEqual(len(answers), 4)
        for at in answers:
            last = max(re.finditer(r"\b(CloseOpenedLibraries|ExecuteCommand|except)\b", body[:at], re.I),
                       key=lambda m: m.start())
            self.assertEqual(last.group(1), "CloseOpenedLibraries", f"an answer at {at} skips the close")


class ScriptProjectCloseTest(unittest.TestCase):
    def test_the_bridge_calls_nothing_of_its_project_once_it_closes_it(self):
        units = bridge_units()
        ours = set().union(*(declared(text) for text in units.values()))
        used = called_after_the_close(routines(units["Altium_API.pas"])["Run"])
        self.assertEqual(used & ours, set())

    def test_the_sandbox_calls_nothing_of_its_unit_once_it_closes_it(self):
        text = unit(SANDBOX)
        self.assertEqual(called_after_the_close(routines(text)["Run"]) & declared(text), set())

    def test_the_answer_is_renamed_into_place_after_the_close(self):
        run_ = routines(unit(BRIDGE / "Altium_API.pas"))
        self.assertNotIn("RenameFile(", run_["WriteResponse"])
        self.assertLess(run_["Run"].index(CLOSE), run_["Run"].index("RenameFile("))


if __name__ == "__main__":
    unittest.main()
