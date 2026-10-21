"""Call any altium-mcp tool from a shell, without an MCP client.

For a session whose MCP servers loaded before altium was registered, a
subagent that inherited none, or a script. The call goes through FastMCP's own
call_tool: the same argument validation, the same tools, the same one-script
lock as a call over MCP. server/main.py is imported fresh on every run, so an
edit to a tool needs no server restart.

Run it with the server's venv:
    PY=%USERPROFILE%\\.altium-mcp\\venv\\Scripts\\python.exe
    $PY dev/call_tool.py --list                       # every tool, first line of its doc
    $PY dev/call_tool.py get_erc_violations --doc     # one tool's doc and argument schema
    $PY dev/call_tool.py get_erc_violations '{"compile": true}'
    $PY dev/call_tool.py place_components --args-file placements.json
    $PY dev/call_tool.py get_screenshot '{"view_type": "pcb"}' --out shot.png

Text results go to stdout; images to --out (default: the exchange folder).
The server's log lines go to stderr.
"""
import argparse
import asyncio
import base64
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "server"))
import main  # noqa: E402  the server module; its __main__ block (mcp.run) does not run


async def run(args) -> int:
    tools = {t.name: t for t in await main.mcp.list_tools()}
    if args.list or not args.tool:
        for name in sorted(tools):
            doc = (tools[name].description or "").strip().splitlines()
            print(f"{name:40} {doc[0] if doc else ''}")
        return 0
    tool = tools.get(args.tool)
    if tool is None:
        print(f"no tool {args.tool!r}; --list shows them", file=sys.stderr)
        return 2
    if args.help_tool:
        print((tool.description or "").strip())
        print(json.dumps(tool.inputSchema, indent=2))
        return 0
    arguments = json.loads(Path(args.args_file).read_text(encoding="utf-8")) if args.args_file \
        else json.loads(args.json or "{}")
    for i, item in enumerate(await main.mcp.call_tool(args.tool, arguments)):
        if item.type == "text":
            print(item.text)
        elif item.type == "image":
            out = Path(args.out) if args.out else main.EXCHANGE_DIR / f"{args.tool}_{i}.png"
            out.write_bytes(base64.b64decode(item.data))
            print(f"image: {out}")
        else:
            print(item.model_dump_json())
    return 0


def parse():
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("tool", nargs="?", help="tool name, as --list prints it")
    p.add_argument("json", nargs="?", help="arguments as one JSON object")
    p.add_argument("--args-file", help="arguments from a JSON file instead")
    p.add_argument("--out", help="where an image result is written")
    p.add_argument("--list", action="store_true", help="list the tools")
    p.add_argument("--doc", dest="help_tool", action="store_true",
                   help="print the tool's doc and argument schema")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(run(parse())))
