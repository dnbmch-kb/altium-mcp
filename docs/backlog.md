# Backlog

Open items only; a fixed one is deleted. Found on the MCB capture (`ugv/dss/mcb-pcb` `8ddb931`, 2026-10-23), each
worked around by a script in that session. A row fixed on a branch stays until its live proof.

- **`build_schematic` places a symbol with its art unlocked** (house rule, operator 2026-10-24: a placed component's graphics carry `ISNOTACCESIBLE=T`, its pins, designator and parameters never). `ReplicateLibSymbol` (`server/AltiumScript/schematic_utils.pas`) copies a symbol out of its SchLib without the flag the library primitives carry, and the scripting API has no accessor for the flag (`Obj.IsNotAccesible` wedges the executor, castellated-modules 2026-10-24). `ISch_Component.UpdatePrimitivesAccessibility`, its only accessibility call, sets exactly that: `edit_schematic` calls it on every copy once it is registered on the sheet (`schematic_edit.pas` `EdPutAt`), and a scratch PWRBASE drawn by schgen through it (2026-11-03) carried the flag on 1596 of 1596 symbol graphics and on none of 856 pins, 221 designators and 3068 parameters, as the committed sheet `lock_art.py` had locked, `sch_check.py` 0 findings. Fix: the same call in `BuildCircuitFromSpec` after its `RegisterSchObjectInContainer`. Done when a sheet `build_schematic` saves reads 0 `unlocked art`.
- **The place call's remaining cost is its libraries** (the scratch PWRBASE draw of 2026-11-03, `mail/54180-1793701997636-1/progress.log`): 221 parts from 17 SchLibs in one call, 71 s from request to answer; the first part from each library 1.64 s median (28.8 s in all: the library opened and shown in its editor), every other part 0.07 s mean (0.27 s at most), and 24.3 s from the save record to the answer (the sheet's save, then `CloseOpenedLibraries` closing the 17; not split). Fix to try: open a SchLib without showing it (`Client.OpenDocument`, then `SchServer.GetSchDocumentByPath`, as upstream's `PlaceSchComponent` reads one) and time the open and the close apart. Done when that call's libraries cost a few seconds, read from its `progress.log`.
- **No-ERC markers placed by script do not suppress Altium's ERC on Input pins** (ugv-pcb PWRBASE, AD 25.6.2, 2026-10-30): the 15 markers `edit_schematic` placed at open Input pins' ends (EN, SYNC, SNS± of castellated receptacles, TPS16861 DNC, SHT35 nRESET) read back live (IsActive, SuppressAll, Thin Cross, at the end Altium's own "Unconnected Pin" warning names), yet "Nets containing floating input pins" and "Unconnected objects in net" stay on every one, and a marker created with `SchObjectFactory(eNoERC, eCreate_Default)` at one pin end suppressed nothing either on a fresh compile. Untested: a hand-placed marker on the same pin (ugv-pcb `docs/plans/pwrbase_board.md` § Next 1a, an open check for the operator); if that one suppresses, the script objects lack a property.
- **`get_schematic_data`, `get_screenshot` and `run_output_jobs` leave the documents they open open**: `GetSchematicData` (`server/AltiumScript/schematic_utils.pas`) opens every sheet of the focused project with `Client.OpenDocument('SCH', …)`, an open one included, and closes none; `PrintView` (`server/AltiumScript/other_utils.pas`) opens a named `document` that is not open and leaves it open; `run_output_jobs` leaves an `outjob_path` it opened open (`RunOutputJobs`). Fix: open through `OpenLibraryDocument`, which then lists every document a read opened, not only libraries, and `CloseOpenedLibraries` closes them before the answer; a document an edit tool leaves unsaved stays open. Done: after each call `c:/Projects/tools/altium/closedocs.py` on those paths reports every one `not_open`.
- **`edit_schematic` without `project` leaves its sheet open and attached in memory to the focused project** (the
  live proofs of 2026-10-30: a scratch sheet drawn under a focused scratch PrjPcb made `closeproject.py` refuse that
  project as "attached"; 2026-11-03: a scratch PWRBASE opened so, its project not open, lost 217 of its 221 unique ids). A call naming the
  sheet's `project` opens it inside that one (tools' schgen does). Fix for the rest: the call refuses a sheet whose
  project it is not told, or opens it as a free document; done when a close of the focused project after an
  `edit_schematic` call succeeds without a flag. `build_schematic` the
  same: three sheets it saved under a focused scratch PrjPcb (2026-10-30) answered `detached_from: ""` and stayed attached
  to it in memory, so `closeproject.py` named them `attached` until a script took each out with
  `DM_RemoveSourceDocument(<full path>)`; its detach (`schematic_utils.pas`, after `DM_CreateNewDocument`) did not run.
- **`rename_label` moves a label onto a neighbouring part's body and does not refuse** (dss-r0-aiv CTRL, 2026-11-14,
  on a parked copy): J301's pin 2 carried a 700 mil stub with the label LB1_A at its free end; renamed LB1_P, the
  stub was drawn anew as long as the text and the label moved with it under F301's body (`sch_check.py`: `overlap
  net label LB1_P x F301 body at (11372, 13100)`), where the op's contract is a refusal with the old text kept.
  The batch drew the label new where the old one stood through schgen's draw script instead (`DELETE`, `LABEL`).
  Fix: the redrawn stub's label box tested against part bodies as a placement's connections are, or a `keep_stub`
  flag that renames the text alone. Done: that rename refused or its stub kept, `sch_check.py` with no overlap.
- **`sheet_plan.py`'s `stub_for` sizes a label's stub at 60 mil per character plus 200** (`sheet_plan.stub_for`), while
  `rename_label` sizes the redrawn stub from Altium's own measure of the text (the house rule: a stub as long as its
  text), so the planner and a rename disagree. Fix: one measure - the planner takes the stroke font's advance
  (`tools/altium` measures 0.98 x height per character, box 1.54 x height) and the rename the same.
- **`sheet_plan` leaves the power ports on one side of a part in more than one column** (house checklist 18, the supply
  symbols on one side of a part, on a pin end or at their own stub's end, in one column; `sch_style.py` `columns`): each
  power run takes its own first clean spot, so a port that steps out stands alone (ugv-pcb PWRBASE's J16 in the replay,
  2026-10-31: its left side's GND ports at x 42000, BAT at 42400; the drawn sheet's U15 left side the same). Fix: once a
  part's power runs are planned, the ports at their stub ends on one side move out to the farthest one's column wherever
  that spot passes the same tests, else stay. Done: J16's left side in one column in the replay, and `columns` on the
  next PWRBASE redraw naming no side the planner could align.
- **`Mailbox.answer` fails a call when it opens `response.json` while the answer is landing** (`server/mailbox.py`): it
  takes only `FileNotFoundError` for "no answer yet", so a Windows `PermissionError` on that open ends `wait` and the
  call with it. Seen in `tests/test_mailbox.py` `WaitTest` (2026-10-31: in one of five runs of the whole offline suite,
  two of its cases raised `PermissionError: [Errno 13]` on the fake bridge's `response.json`, renamed into place by
  `os.replace` as the bridge renames its answer; six runs of the module alone passed). Fix: a `PermissionError` on the
  answer reads as no answer yet, and the next poll reads it whole. Done: the whole offline suite passes run after run,
  and a `WaitTest` case that holds `response.json` open across a poll waits instead of failing.
- **`replace` onto mapped pin ends labels them twice and reports the new pins as errors** (live 2026-10-30, a
  TC33X pot replaced by a RESISTOR whose pin ends land on the pot's mapped ends): `EdLabelOldEnd` puts a label on
  each old end although the old stub already carries that net's label, the plan then finds no spot for the new pins
  (`RV1.1 (ND1): every spot for its connection touches label ND1; nothing drawn`, the same for ND3, so `success`
  false) and `sch_check.py` reports `net label ND3 x net label ND3`; the nets read back right. Fix: an old end whose
  wiring carries the net's label gets none, and a new pin whose end sits on wiring of its own net needs no stub and
  is no error. Done: that replace answers only the unmapped pin's error and `sch_check.py` shows no label overlap.
- **A board command with no board current opens the focused project's board** (`EnsureDocumentFocused`,
  `server/AltiumScript/other_utils.pas`): `check_placement` with no PcbDoc open, sent as a compile smoke call
  (2026-10-30), opened the operator's focused `LED240.PcbDoc` and checked against it; `closedocs.py` closed it again.
  Every command in its PCB list does the same, and acts on whichever board that is. Fix: a board command without a
  `board` argument refuses when no board is current, naming the focused project, and opens nothing. Done: that call
  answers the refusal and `closedocs.py` on the project's board reports `not_open`.
- **`delete_component` with `dead_ends` costs about 3.8 s per op on a 203-part sheet** (ugv-pcb PWRBASE's redraw,
  2026-10-31, measured from the call's `progress.log`, `mail/17804-1793407550870-1/`: the delcomp ops 19 to 33 of 1797
  at 3.78 s each, 83.9 s to 136.8 s; ops 4 to 10 about 2.3 s each), so clearing the sheet's 203 parts takes about
  13 min at that rate, Altium's window hung throughout; the call's 1590 `delete` ops after them never ran (a
  `--recover` watchdog killed Altium after op 33). Per part, `EdDeleteComponent` (`server/AltiumScript/schematic_edit.pas`)
  walks every wire of the sheet on each pass of its dead-end sweep and tests each touched wire's vertices with
  `EdLiveAt`; which step costs the time is not measured. Fix: the per-op cost found and cut. Done: a clear of that
  sheet, timed from its `progress.log`, names the cost removed and the per-op time left.
- **`edit_schematic`'s `delete` finds top-level objects only** (`EdFindUid`, `server/AltiumScript/schematic_edit.pas`),
  so a sheet symbol's entry (RECORD 16) cannot be deleted; laser-calib MAIN_A's ten DNP parts off three sheets
  (2026-11-06) took a sandbox script for it (the symbol's eSheetEntry children iterated, `RemoveSchObject`, saved). Fix:
  `delete` descends into sheet symbols for an entry's id, or a `delete_entry` op. And `delete_component` with
  `dead_ends` left the stubs that met the part's wires at junctions (12 wires and 1 label on RX_XY and FRONTEND_A, each
  an open wire end for `sch_check.py`): a dead end whose only other anchor was a junction of the deleted part's wire is
  a dead end too. Done when the three sheets' deletes leave `sch_check.py` without an open wire end.
- **`dev/unwedge.py` can report STILL WEDGED while Altium's executor runs** (2026-10-23 16:18: STILL WEDGED, and a
  trivial script ran 2 s later): `executor_alive` starts its probe through `cmd.exe` (`subprocess.Popen(...,
  shell=True)`) and waits 45 s for its result file, under the one-script lock both its callers take (`unwedge.py`'s
  `__main__`, `sandbox_runner.py`'s). Cause unread. Fix (untried): the probe names one that never got the executor apart
  from one that ran and wrote nothing.
- **Every X2.EXE line starts through `cmd.exe`, with no no-window flag** (`subprocess.Popen(…, shell=True)`:
  `server/main.py@12a3ce7:570` `AltiumBridge.run_altium_script`, `:1498` `_run_altium_script`; `dev/sandbox_runner.py` `run` and `restart_altium`; `dev/unwedge.py` `stop_launcher` and
  `executor_alive`), against two rules tools' `altium/` holds: no run takes the operator's foreground (its
  `altium_run.start` sends every X2.EXE line through WMI) and no spawn opens a console window (`tools/altium/hazards.md`
  § No console window). Tools' `altium/test_spawns.py` pins its own call sites and scans no file here. Fix: the same
  WMI start here, its `^|` escapes dropped since no shell reads the line, and a spawn test over this repo. Done: no
  `shell=True` left in a launch, the test passing. Tools' `altium/watchdog.py` relaunches Altium through this server's
  `:570` (tools' `altium/backlog.md`), so its X2 comes up inside the watchdog's caller's tree.
- **`dev/unwedge.py` is a Stop line, and its fallback takes the operator's foreground** (`dev/unwedge.py@3ecdcff`):
  `stop_launcher` (`:85`) sends `X2.EXE -REditScript:Stop`, which frees a paused script and wedges one still running
  (tools' `altium/hazards.md` § The in-memory script text and the paused debugger, § A Stop line's Error box), yet
  `dev/README.md` § Recovering a wedged executor offers it for any wedge; when its probe fails, `stop_via_keys`
  (`:99-127`) raises Altium's window with `ShowWindow` and `SetForegroundWindow` and types Ctrl+F3 by `SendKeys`,
  against the rule that no agent run takes the operator's foreground (`#Notes/guides/pcb_workbench.md` § Working in
  the shared Altium); its starts go through `cmd.exe` (`:85`, `:139`, the row above). Fix: `stop_via_keys` goes;
  `unwedge.py` sends its Stop line only while a script Error box stands (a paused script) and otherwise says a script
  still running needs Altium restarted; the README section says the same. Done: no `SetForegroundWindow` or `SendKeys`
  left in `dev/`, and `unwedge.py` with no script Error box up sends nothing.
- **`run_altium_script` leaves the tree dirty after every run**: `_run_altium_script` (`server/main.py@12a3ce7:1419`)
  writes the call's script into the tracked `server/SandboxScript/Sandbox.pas` in place (`:1479`) and runs the tracked
  `Sandbox.PrjScr` (`:1495-1498`), which Altium saves its own sections into. Fix: each run on a staged copy of the
  script project, as tools' `altium/altium_run.py` stages a project. Done: `git status` clean after a call.
## `build_schematic` attaches the new sheet to whatever project is focused

Making LED_INJECT's sheet while the collector strip's project was focused attached `LED_INJECT.SchDoc` to
`COLLECTOR_STRIP.PrjPcb` in memory (2026-10-31; ugv-pcb's LED_INJECT run), so `closeproject.py` refused the strip until
a one-off `DM_RemoveSourceDocument` script took it off. Fix: `build_schematic` takes the target project by path (or
refuses while the focused project is another's) and reads back which project holds the new sheet.
