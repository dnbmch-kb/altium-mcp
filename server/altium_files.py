"""Text files exchanged with the Altium scripts.

Altium's TStringList.SaveToFile writes in the Windows ANSI code page, and
LoadFromFile reads that code page unless the file starts with a byte-order
mark. When the ANSI code page is UTF-8 (the "Beta: Use Unicode UTF-8"
region setting) SaveToFile also writes a UTF-8 byte-order mark, which
json.loads rejects (issue #20). So read the way LoadFromFile does, and write
UTF-8 with a byte-order mark, which LoadFromFile reads correctly whatever the
code page. Python's own default encoding is not used: it follows neither
rule (UTF-8 mode, or Python 3.15+, makes it UTF-8 on every machine).

A DelphiScript source is the exception: it is written in the ANSI code page,
the way Altium's own editor saves one, so a character the code page lacks is
refused by name instead of arriving as two characters or a question mark.
"""
import codecs
from pathlib import Path


def read_altium_text(path) -> str:
    """Text of a file written by the Altium scripts."""
    data = Path(path).read_bytes()
    if data.startswith(codecs.BOM_UTF8):
        text = data[len(codecs.BOM_UTF8):].decode("utf-8", errors="replace")
    else:
        text = data.decode("mbcs", errors="replace")  # the ANSI code page
    return text.replace("\r\n", "\n")


def write_altium_text(path, text: str) -> None:
    """Write text for the Altium scripts to read."""
    Path(path).write_text(text, encoding="utf-8-sig")


def ansi_script(text: str) -> bytes:
    """A DelphiScript source in the ANSI code page. ValueError names every character the code page cannot carry
    (line, column, code point): write it as Chr(<code>) in the script instead."""
    try:
        return text.encode("mbcs")
    except UnicodeEncodeError:
        pass
    bad = []
    for n, line in enumerate(text.split("\n"), 1):
        for c, ch in enumerate(line, 1):
            try:
                ch.encode("mbcs")
            except UnicodeEncodeError:
                bad.append(f"line {n} column {c} U+{ord(ch):04X} {ch!r}")
    raise ValueError("the script holds characters the Windows ANSI code page cannot carry, which Altium would not "
                     "read back; write each as Chr(<code>): " + "; ".join(bad))
