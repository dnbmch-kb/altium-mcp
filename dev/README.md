# Development harness

## Safety rules

- **Never save anything on the shared library drive.** It hosts the
  company's shared symbol/footprint libraries and component database.
  Reading is fine (config, symbol scans, SELECT queries); writing is not.
- A script that opens a library to read a symbol makes that library the
  *current document*. Never register or add objects while a library is
  current - verify the target is a schematic (`ObjectID` 32 = `eSchDoc`;
  33 = `eSchLib`) and abort otherwise. `place_part_from_spec.pas` does this.

Tools for developing new Altium API functionality. Not part of the MCP server.

## One script at a time

Altium runs one script at a time, and several tools drive the same running instance: this server, these dev
tools, and outside this repo `c:/Projects/tools/altium/altium_run.py` (altium-lib-factory's board templates and
library batch, laser-calib's footprint library). Each holds the lock in [`../server/script_lock.py`](../server/script_lock.py)
(the same protocol as `c:/Projects/tools/altium/altium_lock.py`) from its RunScript until the script's output
arrives; a busy Altium is reported with the holder's name, pid and start. The dialog closers here touch only
windows owned by `X2.EXE` (`capture_window.owner_exe`), never another application's.

## Calling a tool from a shell

`dev/call_tool.py` calls any server tool without an MCP client, through FastMCP's own `call_tool`: the same
validation and the same lock. It serves a session whose MCP servers loaded before altium was registered, a
subagent that inherited none, and scripts; `server/main.py` is imported per run, so a tool edit needs no restart.
Run it with the server's venv: `%USERPROFILE%\.altium-mcp\venv\Scripts\python.exe dev/call_tool.py --list`,
`… <tool> --doc`, `… <tool> '{"arg": 1}'` or `--args-file`; an image result goes to `--out`.

## Why

Altium has no headless test mode for DelphiScript, and its failure modes are
hostile to automation:

- A runtime error **kills the script and wedges the script executor** - every
  later `RunScript` silently does nothing until Altium restarts.
- Plain `try/except` does **not** catch runtime conversion errors; the error
  escapes to the enclosing block and still kills the script.
- Compile/runtime errors surface only as modal dialogs, with no log or
  traceback, and their text is often unreadable programmatically.

## Recovering a wedged executor

- `python dev/unwedge.py` dispatches Altium's own Stop Debugging process into
  the running instance:

      X2.EXE -REditScript:Stop

  Same channel the runner uses to launch scripts, so it needs no window focus
  and no synthetic keystrokes. It then **verifies** recovery by running a probe
  script and waiting for output - a wedge is defined by scripts silently doing
  nothing, so anything less is a guess. Ctrl+F3 remains only as a fallback; it
  reaches the debugger solely when the script editor is the active document,
  which is why it used to fail silently while reporting success.
- The runner calls this automatically on every wedge and says whether the
  executor actually came back.
- `--auto-restart` force-restarts Altium as a last resort (~60-90s, kills
  X2.EXE, unsaved Altium work is lost).

## Screenshots are a standing TOP priority

**SOLVED - use dev/render_sheet.py.** Headless render via Select All +
Sch:Copy: Altium puts a properly framed metafile on the clipboard (the same
mechanism that pastes schematics into Word); the script extracts and
rasterizes it to PNG. No window, no foreground, no paint state - always
CURRENT content. Overwrites the user clipboard; avoid mid copy-paste.

    python dev/render_sheet.py <sheet-path> <out.png> [width]

Everything below documents why window capture cannot work here and remains
only for capturing Altium UI state (dialogs, panels).

If schematic screenshots break, fixing them comes before whatever else is in
flight. They are the only check that sees TEXT OVERLAP - net labels,
parameters and designators colliding with wires and each other - which every
geometric audit here misses, and which otherwise puts the user back in the
review loop.

The verified recipe (`capture_window.capture_document`):

1. `Client.ShowDocument` the target sheet, then zoom to fit - this switches
   the visible tab AND retitles the owning frame to the document name.
2. Capture the frame whose TITLE CONTAINS THE DOCUMENT NAME - never the first
   "Altium Designer" window found. Several frames exist; the first match
   repeatedly produced convincing screenshots of the WRONG document while
   every API said the right one was current.

**Root cause, measured:** Altium renders a schematic view ONLY when it has
actually been displayed on screen. A document shown via Client.ShowDocument
while Altium is a background window gets its tab selected and the frame
retitled, but the view surface is never painted - PrintWindow then returns
either the previous document's pixels (convincing and WRONG - check the title
block inside the drawing, it names the file) or black. Every API-level signal
(GetCurrentSchDocument, tab highlight, frame title) says the right document is
current while the pixels are stale.

Getting a real paint requires real activation, and Windows refuses
SetForegroundWindow to background processes. Measured failures:
AttachThreadInput trick (refused), minimize/restore (detaches the document
into a floating window - do NOT do this, it mangles the user's layout),
SetWindowPos TOPMOST (window visible but canvas still unpainted),
SendInput click (foreground still refused).

Untried, ranked for next session:
1. Client:TileAllOpenDocuments - tiling may force every open view to paint
   at once; capture the frame afterwards.
2. Alt-key press via SendInput immediately before SetForegroundWindow (the
   classic foreground-lock release), then paint + capture.
3. Keep exactly ONE schematic open (close others via Modified:=False +
   CloseDocument - verified to work) so the only view MUST be the target;
   combine with 1 or 2.

Also: never name a generated free document like a project sheet. Fresh Altium
restarts number free docs from Sheet1, colliding with the project's
Sheet1..13 - GetDocumentByPath then resolves ambiguously. Dead ends measured
earlier: RedrawToDC blank at all nine combos; TMetafile records outside its
frame.

## Diagnosing failures

1. **Step log** (primary): the last logged line tells you which statement
   died - the one right after it.
2. **Altium window capture** (`dev/capture_window.py`): uses `PrintWindow`,
   which renders the window even when obscured or the desktop is not
   rendering. Screen-scraping (`CopyFromScreen`) returns blanks on
   remote/disconnected sessions - do not use it. When the script editor tab
   is active, its capture shows the paused line highlighted.
3. Silent debugger pauses produce **no dialog at all**; compile errors do.

## Gotcha: the sandbox is standalone

Experiments cannot use constants/helpers defined in the production units
(`REPLACEALL`, `TrimJSON`, `AddJSONProperty`, ...) - those live in
`server/AltiumScript/*.pas`, which this project does not include. Declare
what you need in `Sandbox.pas`. An undefined constant compiles fine and then
kills the script at runtime.

## sandbox_runner.py

Runs an experiment body inside `dev/sandbox/`, a **standalone script project**
that is deliberately separate from the production `Altium_API` project - a
broken experiment can never break the working MCP tooling.

```
python dev/sandbox_runner.py my_experiment.pas [timeout] [--auto-restart]
```

- Injects the body between the BEGIN/END EXPERIMENT markers of `Sandbox.pas`
- `SandboxLog()` flushes to disk after **every** call, so if the script dies
  silently the last logged step identifies the statement that killed it
- Detects and closes both dialog classes Altium uses (`#32770` task dialogs
  and Delphi `TMessageForm`), reporting their text when readable
- `--auto-restart`: on a wedge, force-restarts Altium (kills X2.EXE - unsaved
  Altium work is lost), waits for readiness, and retries the experiment once

Experiment body rules: assign findings to `ResultText`; Pascal has no inline
declarations, so reuse the scratch variables declared in `Sandbox.pas`
(`S1..S5`, `I1..I5`, `B1`, `Obj1..Obj7`, `List1`, `List2`, `IntMan`, `DbDoc`)
or add more there.

**These shared scratch variables are a hazard**, not a convenience. Reusing
one as a loop counter after storing a coordinate in it produced a silently
wrong anchor twice in one session. Track what each holds across a whole
experiment, and prefer distinct names for values that must survive a loop.

Put risky reads (enum properties, anything that might not exist on this
install) at the **end** of an experiment. `SandboxLog` flushes on every call,
so a wedge there still leaves everything useful already on disk.

## AD25 facts

Each found on a board and checked by a readback; the date and the board say where.

- **A component kind lives on the symbol.** A SchLib symbol takes one (`COMPONENTKIND` 1 Mechanical, 4 Net Tie
  without BOM) and the ECO carries it to the board part; setting `ComponentKind` on a PcbLib footprint raises a
  runtime error (altium-lib-factory `e9e7f6b`, 2026-10-23, for dss-r0-aiv's net tie and LED240P's standoff).
- **Update PCB does not add a part that is Mechanical on the sheet only**, and offers a Standard board part whose
  sheet part is Mechanical for removal: send it over Standard, then set Mechanical on both (dss-r0-aiv LED240P,
  2026-10-23).
- **A `Repeat()` sheet entry drawn by script binds nothing**: it compiles as a wire sheet entry on a bus and stays
  unconnected in every channel. Plain entries bind, so a script-built top sheet takes one sheet symbol per
  channel, which gives the same rooms and channel designators (dss-r0-aiv CTRL, 2026-10-23).
- **A via's tenting holds only in manual mask mode**: `SolderMaskExpansionMode := eMaskExpansionMode_Manual`
  before `IsTenting_Top` / `IsTenting_Bottom` (laser-calib raw-head adapter, 2026-10-23).
- **A design rule's enable flag is `DRCEnabled`**; the saved `ENABLED` field follows it, `Enabled` is not stored
  (raw-head adapter, 2026-10-23).

## Findings

- [`SCHEMATIC_CONVENTIONS.md`](SCHEMATIC_CONVENTIONS.md) - **read before
  generating any schematic.** Pin connection points, tap gaps, ground length,
  rail placement, net-label attachment, parameter text, mirroring, and the
  verification rules. Every entry is a mistake that was made and corrected.
- [`SHEET_DUPLICATION.md`](SHEET_DUPLICATION.md) - copying a schematic sheet.
  The document iterator recurses, so `Container.ObjectId = 32` (not
  `ObjectId`) is what identifies the objects a sheet actually owns.
