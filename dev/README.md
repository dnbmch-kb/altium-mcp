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

## The mailbox

Every altium-mcp server on the machine shares one Altium and one folder, `C:\Users\Public\altium_mcp\`
(about 20 server processes from registered sessions on 2026-10-23); the layout is in the README § How It Works
and in [`../server/mailbox.py`](../server/mailbox.py). What keeps it unambiguous:

- **One folder per request.** `mail\<id>\` holds the request, the spec files its command reads (`edit_spec.txt`,
  `annotate_spec.txt`, `circuit_spec.txt`) and everything the script writes for it (its `temp_*.json`, a build's
  `pin_map.txt`, a print's `shot.OutJob` and `shot.pdf`): the script's `ROOT_DIR` is that folder while it serves
  the request. Two servers never write the same file, and a spec is written with its request, under the lock.
- **The sender in the id, the id in the answer.** `<pid>-<ms>-<seq>`; `response.json` echoes `request_id`, and the
  server compares it.
- **Only the lock holder's requests run.** `Altium_API.pas` `Run` reads the lock's pid and takes from `queue\` only
  requests whose id starts with it, by renaming the entry to `mail\<id>\taken` (`ServeQueue`, up to 20 in one
  run, oldest first, so a RunScript that Altium dropped is covered by the next run). A request left in the queue by
  a server that gave up or died is never run later: the next server under the lock moves it to its folder as
  `expired` (`mailbox.expire_others`), and a server that gives up withdraws its own entry when the bridge has not
  taken it yet (`withdrawn`).
- **The answer appears whole, once the bridge is done**: `response.tmp`, renamed to `response.json` at the end of
  the run, after the libraries the command opened (`OpenLibraryDocument`, closed by `CloseOpenedLibraries` before
  the answer) and then the bridge's own script project were closed; `closed.txt` beside it says how each open copy
  under `bridge\` went. So the server holds the lock until the run is over.
- **Progress, not a clock.** The script appends `seq|elapsed ms|step|count|total` to `progress.log` (`BridgeProgress`
  in `other_utils.pas`: the file created once for the request and held open, each record appended and flushed
  through that handle, which shares reading, so a reader holding the file, the server or a watcher, fails no write;
  a file recreated per record raised EFCreateError mid-pass while a watcher held it, ugv-pcb SCB 2026-10-26). The
  server reads the file only when its size or time changed, gives up after `STALL_SECONDS` (120)
  without a record (`STALL_BY_COMMAND`: 900 for `run_output_jobs`, 300 for a print), and says what it saw: not
  taken (withdrawn, nothing ran) or the last record, Altium hung, a box. `status.json` carries the call in flight
  (state queued / running / answered / stalled, the last record) for a watchdog; MCP progress notifications go to
  a client that sent a progress token.
- **Altium's boxes.** Every second the server looks for a box of `X2.EXE` (`#32770`, `TMessageForm`) that was not
  there when the call began. Before the take it is Altium refusing the RunScript ("Another script executing now"):
  pictured as `refused_N.png`, closed, the RunScript sent again (up to 5 times). After the take it holds our
  script: seen twice, pictured as `dialog_N.png` and closed, and the script gets 10 s more to report before the
  call gives up.
- **The single-file protocol until every server restarts.** A server started before the mailbox writes
  `request.json` at the root (after deleting `response.json`) and holds the lock as `altium-mcp <command>`; a
  mailbox server holds it as `altium-mcp <command> (mailbox)`. `Run` answers the root request only when nobody's
  queued request was served, the lock's owner names no mailbox, `response.json` is absent and `request.json` is
  younger than 130 s (`ServeLegacy`), so an abandoned root request never runs again. A print request without its
  OutJob template gets the old focus-and-zoom answer (`TakeViewFocus`), which such a server grabs a window after.
  Drop both once no server older than the mailbox runs.

Deploying a bridge change goes live for every server at its next call: they all run
`C:\Projects\altium-mcp\server\main.py` and stage the bridge from this checkout's files by their hash. So the
script is developed in a worktree (its `dev/call_tool.py` stages its own copy) and reaches the main checkout only
after it compiled and served both protocols live.

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

**SOLVED - `get_screenshot` prints.** A board or sheet is printed by Altium (PCB Print or Schematic Print in an
OutJob that joins the document's project for the print, published to PDF, rasterised with `pypdfium2`), so the
picture is the document in memory, never a window (README § Both). The server's venv needs `pypdfium2`
(`uv pip install --python %USERPROFILE%\.altium-mcp\venv\Scripts\python.exe pypdfium2==5.14.0`; the launcher's
list and `requirements.txt` carry it). The window grab is kept for Altium's dialogs (`view_type: "dialog"`).

**Also: dev/render_sheet.py.** Headless render via Select All +
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
or add more there. `List1` and `List2` start as empty lists that `Run` frees after the experiment; one an
experiment creates in their place it frees itself.

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
- **A bottom part is mirrored top to bottom, then turned counter-clockwise by its `Rotation`**: pad 1 of
  IC_SOT23_5 read back on the bottom at 0, 90 and 180 (scratch board, 2026-10-23). A part placed in a frame that
  mirrors left to right (KiCad's) takes its angle + 180; 90 and 270 alone cannot tell + 180 from 360 - angle.
- **`IntToStr(Obj.I_ObjectAddress)` pauses the script**: an object address goes to `SendMessage` and compares with
  `<>`, but does not convert to text (edit_schematic's first live two-pass run, 2026-10-23).
- **A design rule's enable flag is `DRCEnabled`**; the saved `ENABLED` field follows it, `Enabled` is not stored
  (raw-head adapter, 2026-10-23).
- **A routine is known only below its declaration in its own unit.** `TakeViewScreenshot` calling `PrintView`
  further down `other_utils.pas` raised "Undeclared identifier: PrintView" when the call ran (a box, the script
  ended); a routine of another unit of the project resolves wherever it is (the bridge's first print, 2026-10-23).
- **Altium refuses a RunScript while a script runs**, with the box "Another script executing now. Scripting system
  is not able to execute more than one script simultaneously."; the run is not queued, and the box holds Altium
  until it closes (a bridge call that took the lock as the previous script was returning, 2026-10-23).
- **An OutJob resolves its documents only inside its project.** A free OutJob naming a board by full path or by
  name answered "Missing file: ..." (a box) and published nothing; added to the board's project
  (`Prj.DM_AddSourceDocument`) it published the PDF in about 1 s, and `DM_RemoveSourceDocument` plus the project's
  `IServerDocument.Modified` put back left the project as it was, its file untouched. A free document (in the Free
  Documents project) prints nothing, the OutJob joined or not, and no box. `WorkspaceManager:Print` with
  `Action=PublishToPDF|ObjectKind=OutputBatch|OutputMedium=<container>|DisableDialog=True`, the OutJob focused;
  `OpenOutput=0` keeps the PDF closed (scratch copy of Elch TEMPMODUL, 2026-10-23).
- **A PCB Print draws its first layer record on top**: `TopOverlay, TopLayer, BottomLayer, ...` shows the top pour
  over the bottom, the reverse order the bottom over the top. Its `PrintArea=SpecificArea` corners are absolute
  internal units (`Rect.Left` of a component as it is); the same numbers in mils printed an empty page. The record
  names a layer without spaces: `TopLayer`, `MidLayer1`, `BottomLayer`, `InternalPlane1`, `MultiLayer`,
  `TopOverlay`, `KeepOutLayer`, `Mechanical<n>`; `Layer2String` gives `Top Layer` and `Mechanical Layer 1`, and the
  V6 id of every mechanical layer above 16 is 72, so mechanical layers are named by their index (same board).
- **`FindFiles(folder, mask, faAnyFile, False, list)`** lists a folder's files with their paths in upper case;
  `RenameFile` moves a file into another folder and answers False when the source is gone, which is the bridge's
  take (2026-10-23).

## Findings

- [`SCHEMATIC_CONVENTIONS.md`](SCHEMATIC_CONVENTIONS.md) - **read before
  generating any schematic.** Pin connection points, tap gaps, ground length,
  rail placement, net-label attachment, parameter text, mirroring, and the
  verification rules. Every entry is a mistake that was made and corrected.
- [`SHEET_DUPLICATION.md`](SHEET_DUPLICATION.md) - copying a schematic sheet.
  The document iterator recurses, so `Container.ObjectId = 32` (not
  `ObjectId`) is what identifies the objects a sheet actually owns.
