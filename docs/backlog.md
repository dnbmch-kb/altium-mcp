# Backlog

Open items only; a fixed one is deleted. Found on the MCB capture (`ugv/dss/mcb-pcb` `8ddb931`, 2026-10-23), each
worked around by a script in that session. A row fixed on a branch stays until its live proof.

- **A placed symbol's art comes out unlocked** (house rule, operator 2026-10-24: a placed component's graphics carry `ISNOTACCESIBLE=T`, its pins, designator and parameters never). `ReplicateLibSymbol` (`server/AltiumScript/schematic_utils.pas:1642-1678`) and the `SchObjectFactory`/`AddSchObject` builders (`:249-565`) copy a symbol out of its SchLib, and library primitives carry no flag; the scripting API has no accessor for it (`Obj.IsNotAccesible` wedges the executor, castellated-modules 2026-10-24). Parts Altium loads itself come out locked (the dnbm IntLib parts on laser-calib BUCK after Update From Libraries). Fix, to try first: place through Altium's own loader, `SchServer.LoadComponentFromDatabaseLibrary(DbLib, table, key)` for house.DbLib rows, and check the placed art in the file; fallback: run `c:/Projects/tools/altium/lock_art.py` on the sheet after a place batch. `sch_check.py` reports any leftover as `unlocked art`.
- **No-ERC markers placed by script do not suppress Altium's ERC on Input pins** (ugv-pcb PWRBASE, AD 25.6.2, 2026-10-30): the 15 markers `edit_schematic` placed at open Input pins' ends (EN, SYNC, SNS± of castellated receptacles, TPS16861 DNC, SHT35 nRESET) read back live (IsActive, SuppressAll, Thin Cross, at the end Altium's own "Unconnected Pin" warning names), yet "Nets containing floating input pins" and "Unconnected objects in net" stay on every one, and a marker created with `SchObjectFactory(eNoERC, eCreate_Default)` at one pin end suppressed nothing either on a fresh compile. Untested: a hand-placed marker on the same pin (ugv-pcb `docs/plans/pwrbase_board.md` § Next 1a, an open check for the operator); if that one suppresses, the script objects lack a property.
- **`get_schematic_data`, `get_screenshot` and `run_output_jobs` leave the documents they open open**: `GetSchematicData` (`server/AltiumScript/schematic_utils.pas`) opens every sheet of the focused project with `Client.OpenDocument('SCH', …)`, an open one included, and closes none; `PrintView` (`server/AltiumScript/other_utils.pas`) opens a named `document` that is not open and leaves it open; `run_output_jobs` leaves an `outjob_path` it opened open (`RunOutputJobs`). Fix: open through `OpenLibraryDocument`, which then lists every document a read opened, not only libraries, and `CloseOpenedLibraries` closes them before the answer; a document an edit tool leaves unsaved stays open. Done: after each call `c:/Projects/tools/altium/closedocs.py` on those paths reports every one `not_open`.
- **`edit_schematic` leaves its sheet open and attached in memory to the focused project** (the live proofs of
  2026-10-30: a scratch sheet drawn under a focused scratch PrjPcb made `closeproject.py` refuse that project as
  "attached"). Fix: the bridge opens the sheet as a free document, or detaches it from the project when the call ends;
  done when a close of the focused project after an `edit_schematic` call succeeds without a flag. `build_schematic` the
  same: three sheets it saved under a focused scratch PrjPcb (2026-10-30) answered `detached_from: ""` and stayed attached
  to it in memory, so `closeproject.py` named them `attached` until a script took each out with
  `DM_RemoveSourceDocument(<full path>)`; its detach (`schematic_utils.pas`, after `DM_CreateNewDocument`) did not run.
- **`sheet_plan.py`'s `stub_for` sizes a label's stub at 60 mil per character plus 200** (`sheet_plan.py:83`), while
  `rename_label` sizes the redrawn stub from Altium's own measure of the text (the house rule: a stub as long as its
  text), so the planner and a rename disagree. Fix: one measure - the planner takes the stroke font's advance
  (`tools/altium` measures 0.98 x height per character, box 1.54 x height) and the rename the same.
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
