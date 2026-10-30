# Altium MCP Server

TLDR: Use Claude to control or ask questions about your Altium project.
This is a Model Context Protocol (MCP) server that provides an interface to interact with Altium Designer through Python. The server allows for querying and manipulation of PCB designs programmatically.

## Example commands
- Run all output jobs
- Create a symbol for the part in the attached datasheet and use the currently open symbol as a reference example.
- Create a schematic symbol from the attached MPM3650 switching regulator datasheet and make sure to strictly follow the symbol placement rules. (Note: Need to open a schematic library. Uses `server\symbol_placement_rules.txt` in the extension's install folder as pin placement rules. Please modify for your own preferences; an extension update replaces the file, so keep a copy.)
- Create an op-amp symbol with a triangle body like the other op-amps in my library (symbols support line/polygon/arc/ellipse/label body graphics, not just rectangles)
- Find me the LM358 symbol in my opamp library and open it
- Create a multi-part symbol for a quad op-amp from the attached LM324 datasheet (creates parts A, B, C, D with shared V+/V- power pins)
- Create a PCB footprint for the SMD part in the attached datasheet and add it to my open PcbLib
- Duplicate my selected layout. (Will prompt user to now select destination components. Supports Component, Track, Arc, Via, Polygon, & Region)
- Show all my inner layers. Show the top and bottom layer. Turn off solder paste.
- Get me all parts on my design made by Molex
- Give me the description and part number of U4
- Place the selected parts on my pcb with best practices for a switching regulator, then verify clearances and show me a screenshot of the result
- Give me a list of all IC designators in my design
- Get me all length matching rules
- Check my project is ready for fabrication: unsaved documents, ERC errors, and which layers my Gerber output job plots
- Draw a schematic of an LED with a current-limiting resistor on a new sheet, using symbols from my library

## Installing the MCP Server
The easiest way to install is to use Claude Code, point it to this repo and ask it to install it for you. Or alternatively, see below.

[Watch on YouTube](https://youtu.be/HKQMK-hluLs)

1. Make sure Claude has Python 3.10+ installed: `drop down > File > Settings > Extensions > Advanced > Python`. If not, install Python and add it to PATH.
2. Download the `altium-mcp.dxt` desktop extension file from [releases](https://github.com/coffeenmusic/altium-mcp/releases)
3. In Claude Desktop on Windows: `drop down > File > Settings > Extensions > Advanced > Install Extension...` Select the .dxt file

You shouldn't need to restart Claude and you should now see altium-mcp in the tool menu near the search bar.

![altium-mcp in the tools menu](assets/extension.jpg)

## Creating a new .dxt (For Developers)

### Bootstrap Venv (Recommended)

This approach ships a small bootstrap script (`start_server.py`) that creates a virtual environment and pip-installs dependencies on the user's machine at first launch. The .dxt is tiny (~60 KB) and works across any Python 3.10+ version.

The older approach of bundling pre-compiled packages in `server/lib/` is no longer recommended — it breaks when the user's Python version doesn't match the version used to build the bundled `.pyd` files (e.g. pydantic_core compiled for 3.11 fails on 3.13).

**How it works:**

1. `start_server.py` (at the repo root) looks for a ready virtual environment in `%USERPROFILE%\.altium-mcp\venvs\<key>`, where `<key>` is a hash of the pinned requirements and the Python version
2. If it isn't ready, it starts a **detached** builder process (`start_server.py --build`) that creates the venv and pip-installs the pinned dependencies, then waits for it. Claude Desktop kills a server that has not answered within about 60 seconds, and restarts servers freely; a build that ran inside the launcher died with it. The detached builder finishes regardless, so a restarted launcher finds the venv ready or a build still in progress.
3. A lock file (holding the builder's PID) makes sure only one build runs at a time; other launchers wait for it. A lock whose owner has exited is taken over immediately.
4. It then launches `server/main.py` using the venv's Python
5. First launch takes ~20-30 seconds; subsequent launches are instant

The venv, `config.json` and `build.log` live in `%USERPROFILE%\.altium-mcp`, not in the extension folder, because Claude Desktop replaces the extension folder on every update. The slow build therefore happens once per machine (and again only when the requirements or Python version change), and a hand-picked Altium path survives updates. AppData is deliberately avoided: Claude Desktop is an MSIX package, and writes its child processes make under AppData are redirected into `...\Packages\Claude_...\LocalCache`, so a venv built from a terminal and one built by the server would end up in different places. Set the `ALTIUM_MCP_HOME` environment variable to use a different folder. A `config.json` left beside `server/main.py` by an older version is migrated automatically on first start.

On a slow network the first build can exceed the client's 60-second startup timeout. The build carries on in the background, so restarting the extension once it has finished is enough; `build.log` shows progress.

**Build steps:**

1. Make sure `start_server.py` exists at the repo root
2. Make sure `server/main.py` does NOT have the old `site.addsitedir` hack at the top
3. Update `manifest.json`: set `entry_point` to `start_server.py`, remove any `env`/`PYTHONPATH` fields, and use `manifest_version: "0.3"`
4. Package the DXT — either use `dxt pack` or manually zip and rename:
```powershell
Compress-Archive -Path manifest.json, start_server.py, pyproject.toml, server -DestinationPath altium-mcp.zip
Rename-Item altium-mcp.zip altium-mcp.dxt
```

**Do NOT include `server/lib/` or `server/.venv/` in the .dxt.** The whole point is that these are created on the user's machine.

**`start_server.py`:** use the file at the repo root as is. It is no longer reproduced here, so the two cannot drift apart.

**`manifest.json` server section:**
```json
"server": {
    "type": "python",
    "entry_point": "start_server.py",
    "mcp_config": {
      "command": "python",
      "args": ["${__dirname}/start_server.py"]
    }
  }
```

### Pitfalls

These are hard-won lessons from debugging DXT builds. Violating any of these will produce errors that are difficult to diagnose.

1. **Do NOT use `os.execv()` in `start_server.py`.** The DXT installs to a path containing spaces (`Claude Extensions`). On Windows, `os.execv` splits the path at the space and fails. Use `sys.exit(subprocess.call([...]))` instead.

2. **Pin `mcp` to `==1.5.0`.** Using `>=1.5.0` pulls in the latest version, which has breaking API changes (`FastMCP.__init__()` dropped the `description` kwarg). The server code was written against 1.5.0.

3. **Do NOT use `manifest_version: "0.4"` with `"type": "uv"`.** Claude Desktop does not support it yet. You will get `Invalid manifest: server: Required`. Use `"type": "python"` with `manifest_version: "0.3"`.

4. **After removing the `site.addsitedir` hack from `main.py`, fix the `pathlib.Path` reference.** The hack included `import pathlib` at the top of the file. The logging setup later uses `pathlib.Path(...)` which will throw `NameError` once that import is gone. Change it to `Path(...)` — the `from pathlib import Path` import already exists in the file, just make sure it comes before the logging setup.

5. **Do NOT bundle `server/lib/` in the DXT.** That was the old approach and defeats the purpose of the venv bootstrap.

### Legacy: Bundled server/lib (Not Recommended)

This approach bundles all dependencies in `server/lib/` and sets `PYTHONPATH` to point to it. It produces a much larger .dxt (~17 MB) and **only works if the user's Python version matches the version used to compile the bundled packages**.

1. Populate packages: `python -m pip install --no-cache-dir --target server/lib -r requirements.txt`
2. Set the manifest `entry_point` to `server/main.py` and add `"env": {"PYTHONPATH": "${__dirname}/server/lib"}` to `mcp_config`
3. Package: `npm install -g @anthropic-ai/dxt && dxt pack`

### DXT Resources
- [Desktop Extensions](https://www.anthropic.com/engineering/desktop-extensions)
- [Desktop Extensions Github](https://github.com/anthropics/dxt)
- [Getting Started with DXT](https://support.anthropic.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop)
- [Python DXT Example Code](https://github.com/anthropics/dxt/tree/main/examples/file-manager-python)
- [DXT Manifest](https://github.com/anthropics/dxt/blob/main/MANIFEST.md)


## Configuration

When launching claude for the first time, the server will automatically try to locate your Altium Designer installation. It will search for all directories that start with `C:\Program Files\Altium\AD*` and use the one with the largest revision number. If it cannot find any, you will be prompted to select the Altium executable (X2.EXE) manually when you first run the server. Altium's DelphiScript scripting is used to create an API between the mcp server and Altium. 

## Available Tools

The server provides several tools to interact with Altium Designer:

### Output Jobs
- `get_output_job_containers`: Using currently open .OutJob file, reads all available output containers
- `run_output_jobs`: Run containers of an OutJob (`outjob_path`, or the focused OutJob) and get back the files each one wrote, with their sizes. The tool once reported success on Elch's TEMPMODUL while writing nothing (2026-10-23); the two scripts that generated LED240's and Elch's sets show the OutJob first and pass no output base path, and the tool now does the same. Each container's output folder (its output base path under the project's folder) is listed before and after; a container that wrote nothing fails with the reason. The OutJob is read as saved. Live 2026-10-23: dss-r0-aiv's LED240P JLC container listed its 14 files with sizes (Gerber layers, drill, pick and place, reports).
- `get_outjob_settings`: Read any `.OutJob` file from disk (no Altium needed, the OutJob does not have to be open) and return its containers (output paths, file name expression, add-to-project) and outputs (connected containers, variant, document) with decoded settings: Gerber units/format/origin and the plotted and "add to all plots" layers decoded from Altium layer IDs, NC Drill, IPC-D-356 test points, pick and place and print layers. Use it to audit fabrication settings before generating. Reads the saved file, so unsaved changes in Altium are not visible.

### Project & Validation
- `open_project`: Open a `.PrjPcb` (or find it open) and its schematic sheets; reports whether it became the focused project, which `get_erc_violations` and the project tools act on.
- `get_project_info`: Focused project's parameters, documents with their state (`closed` / `open` / `modified` = unsaved changes in memory), schematic sheet count and variants with their variations (e.g. Not Fitted parts). Read-only; use it to confirm nothing is unsaved before generating outputs.
- `get_project_components`: Every component of the compiled project with its schematic Comment, footprint, library reference and sheet (optionally all parameters). Use it for BOM work: the instance Comment is where generic passives keep their value, which PCB-side tools and ActiveBOM may not show.
- `get_erc_violations`: Compile the focused project in memory (nothing is saved) and list compiler/ERC violations with their level (fatal / error / warning). Violations are read from the project object - the per-document `DM_ViolationCount` does not exist in recent Altium versions.
- `get_board_summary`: Fabrication-oriented summary of the focused PCB in mm: board outline (bounding box, size, vertices) and origin, dimension objects, vias by hole size, components per side, primitive counts per layer (free vs. inside components, e.g. to see which mechanical layers hold the assembly outlines) and the free texts on both overlays.
- `get_component_primitives`: Every primitive of the given components with its layer (pads with size and solder/paste mask expansion, arcs, tracks, texts). Catches footprints that were flipped to the bottom side but kept part of their copper or mask opening on the top (e.g. fiducials).

### Component Information
- `get_all_designators`: Get a list of all component designators in the current board
- `get_all_component_property_names`: Get a list of all available component property names
- `get_component_property_values`: Get the values of a specific property for all components
- `get_component_data`: Get detailed data for specific components by designator
- `get_component_pins`: Get pin information for specified components; each pad's `dx`/`dy` is its offset in the footprint's own frame (a bottom pad un-mirrored in y, as Altium mirrors a bottom part top to bottom; read back at 0, 90 and 180 on a scratch board, 2026-10-23)

### Schematic/Symbol
- `get_schematic_data`: Get schematic data for specified components
- `create_schematic_symbol` ([YouTube](https://youtu.be/MMP7ZfmbCMI)): Passes pin list with pin type & coordinates to Altium script. Supports multi-part symbols (e.g. quad op-amps) via a `part_count` parameter and an `owner_part_id` field on each pin (use 0 for shared power/GND pins), active-low pin name overbars by placing a backslash after each overbarred character (e.g. `R\E\S\E\T\` renders as `RESET` with overbar), per-pin length and name/designator visibility, and an optional `graphics` list (lines, polylines, polygons, rectangles, arcs, ellipses, labels) for non-rectangular bodies like op-amp triangles and diode glyphs. When creating a symbol, the agent is instructed to first look up a similar symbol in an available library as a style reference (skipped gracefully if no library is available).
- `get_symbol_primitives`: Inventory a .SchLib (every symbol with per-type primitive counts) or dump one symbol's complete geometry (all graphics + pin details, in mils; `symbol_name="*"` dumps every symbol). Used to gap-analyze a library, to give the agent a reference example when drawing a new symbol, and to verify recreations. The symbol creator round-trips a complete production library set exactly - 514/514 symbols across IC, connector, misc, and passives libraries (dump -> recreate -> dump -> diff, zero differences). Footprint/model links (implementations) are metadata outside the graphics round-trip.
- `create_symbols_batch`: Create many symbols in one script run from a plain-text spec file (bulk imports/migrations) - far faster and more robust than one create call per symbol.
- `build_schematic`: Build a wired schematic on a new sheet from a circuit description: parts from a `.SchLib` (full path; `.IntLib` is not supported; a library the call opens is closed again at its end, one open before stays open), wires, junctions, net labels, power ports and notes, in mils. Returns every placed pin's true connection point, so a second call can route wires from measured coordinates, and lists any part it could not place. The new sheet is kept out of your project. With `save_path` (the full path of a `.SchDoc` that does not exist yet) it is saved there and closed, so a project that lists that path opens it.
- `edit_schematic`: Edit an existing sheet in one batch: delete objects or parts (with the wires they leave connecting nothing), rename labels and nets, attach or detach a pin, place a part from a `.SchLib` linked to an IntLib or DbLib, replace a part, relink one to another library record, No-ERC markers, sheet-entry connections. Wires between given pin ends and power ports at chosen points are the `wire`, `junction` and `port` ops; every other connection is a stub with a net label or power port, placed from the placed symbols in the house style (`server/sheet_plan.py`, rules in `dev/SCHEMATIC_CONVENTIONS.md` sections 7 and 13-15): a horizontal label on a stub sized to its text, at the far end on a left pin; GND as power ground pointing down with its name hidden, a supply as a bar pointing up, never sideways, moved out or jogged until it touches no body, pin or text and shared by neighbouring pins on one power net; never a spot touching another net. A sheet `build_schematic` creates gets snap 50, grid 100 and hotspot 20 mil. A whole sheet goes in one call: the bridge reports each op, and the call waits as long as it reports (live 2026-10-23: 60 house passives, each pin on a net, placed in 75 s, 270 connection objects drawn and the sheet saved in 1.3 s, 80 s end to end, every net read back from the saved file). `connections` returns each one's boxes, `overlaps` whatever still touches. A part linked to an IntLib or DbLib takes every model from that library ("Use Component Library"), as a hand-placed part does, and `placed` reads each model back; a model left on "any library" made the raw-head adapter's ECO search every installed library for 40 minutes and freeze Altium (2026-10-23). A part without a database row (no `design_item`, so its design item is the symbol's name) has no row to give it a footprint: on Use Component Library the ECO reported `Footprint Not Found RES_0603_1608` on 47 of CTRL's passives (dss-r0-aiv, 2026-10-24). Such an op takes `footprint_library`, the full path of the `.PcbLib` holding `footprint`: that model takes the footprint from the library by its path (`MODELDATAFILE0` in the file) with Use Component Library off, and `placed` reads its `library_path` back; with `design_item` it is refused. A model left with neither is an error. A `place`, `replace` or `relink` op's `description` sets the part's own Description (`COMPONENTDESCRIPTION` in the file), as `build_schematic`'s does. Altium keeps no parameter named Description: a DbLib column mapped to one lands in the component description, and the bridge wrote it as a parameter that never reached the saved sheet, so a part linked to a DbLib row at placement kept the symbol's generic description (dss-r0-aiv CTRL, 2026-10-24). A `Description` key in `parameters` now sets the component description too; given with `description`, the two must match (live 2026-10-24: a Cap-Gen row placed with `description` read back from the saved sheet as its `COMPONENTDESCRIPTION`, its parameter beside it). An op it cannot carry out changes nothing and is listed in `errors`. A `des:` reference names every record of its designator, so a multi-part part's parts (CTRL's U804_G1, three records) are relinked in one op, and `attach_pin`, `detach_pin` and `no_erc_pins` act on each record that shows the pin; `replace` refuses a designator naming more than one record, with the count. `removed` lists every object the call took off the sheet by unique id, kind and text: a part's dead-end wires (`delete_component` with `dead_ends`) and a detached pin's stub take every label and power port lying on them, mid-stub as at the end, unless another wire or a pin is under it. Every `.SchLib` a call opens to copy a symbol from is closed again at its end, saving nothing, a failed op's too; a library open before the call stays open. Read the saved sheet back for connectivity. Live 2026-10-23 on a scratch sheet (seven house parts, a Res-Gen row, a mirrored 1x3 header): every label and port where `server/sheet_plan.py` planned it, every model on Use Component Library, the `wire`, `junction` and `port` ops as given, a style-4 ground drawing no name, a comment `Fühler 25 °C ß` read back from the saved file; `c:/Projects/tools/altium/sch_check.py` reports the stub end beyond a label on a right-pointing pin as an open wire end (the house style runs the stub past the text).
- `replace_component`: Swap a placed part for another symbol, pins mapped by function (`pin_map` old -> new, a net for every mapped pin): the old pin ends keep their nets through labels, the new part takes the old origin and is never moved. Where it does not fit there - a pin the call names missing on the new symbol, its body over another part's, a pin end on anything but the old end of the pin mapped onto it - the op is refused, naming each, and nothing changes; an old pin connected but in no `pin_map` entry is an error naming it, its wiring left without a pin.
- `annotate_schematic`: Set designators by unique id across sheets; `schematic_edit.auto_designators` computes the usual assignment (next free number per prefix, top to bottom, left to right) for review first.
- `get_symbol_placement_rules`: Create symbol's helper tool that reads `server\symbol_placement_rules.txt` in the extension's install folder to get pin placement rules for symbol creation.
- `get_library_symbol_reference`: Create symbol's helper tool to use an open library symbol as an example to create the symbol
- `search_library_symbol`: Search for a symbol by name in a schematic library (.SchLib) and navigate to it. Supports partial name matching. Will open the library file in Altium if a path is provided, or show a file picker if not; a library the call opened is closed again at its end, one open before stays open on the symbol it navigated to.

![Symbol Creator](assets/symbol_creator.gif)

### Layout Operations
- `get_all_nets`: Returns a list of unique nets from the pcb
- `create_net_class` ([YouTube](https://youtu.be/89booqRbnzQ)): Create a net class from a list of nets
- `get_pcb_layers`: Get detailed layer information including electrical, mechanical, layer pairs, etc.
- `get_pcb_layer_stackup`: Gets stackup info like dielectric, layer thickness, etc.
- `set_pcb_layer_visibility` ([YouTube](https://youtu.be/XaWs5A6-h30)): Turn on or off any group of layers. For example turn on inner layers. Turn off silk.
- `get_pcb_rules`: Gets the rule descriptions for all pcb rules in layout.
- `get_selected_components_coordinates`: Get position, rotation, layer, and footprint information for currently selected components
- `move_components`: Move specified components by X and Y offsets
- `set_component_position`: Set one component's absolute position and rotation
- `place_components`: Batch absolute placement - place any number of components (x, y, rotation, `side` top or bottom) in a single transaction / one undo step, on the current board or the `board` it is given. A bottom part's rotation is that of its footprint mirrored left to right and then turned (KiCad's convention); Altium's flip mirrors top to bottom, so the tool stores the angle + 180 (raw-head adapter, 2026-10-23; settled against 360 - angle by pad 1 read back on the bottom at 0, 90 and 180, scratch board, 2026-10-23). Every part is read back after the transaction (board file, x, y, side, rotation and Altium's own) and one that does not hold its request is an error: on LED240 the tool once reported R1 at 180° while the board kept 0°. The board is not saved.
- `check_placement`: Verify a placement - finds overlaps and clearance violations against every other component on the board using true primitive-to-primitive distances (designator text excluded). Run after placing; a screenshot is not verification.
- `check_orientation`: Advisory check for 2-pad passives whose rotation could be improved (e.g. a decoupling cap with its GND pad facing away from the IC). Geometry only - review suggestions with judgement; parts like pull-ups don't care.
- `get_net_connections`: For the nets touching a set of components, list every pad on each net board-wide with per-net airline (MST) lengths - the data behind connectivity-driven placement (shorten critical nets, see off-cluster loads and filter banks).
- `layout_duplicator` ([YouTube](https://youtu.be/HD-A_8iVV70)): Starts layout duplication assuming you have already selected the source components on the PCB.
- `layout_duplicator_apply`: Action #2 of `layout_duplicator`. Agent will use part info automatically to predict the match between source and destination components, then will send those matches to the place script.

The cool thing about layout duplication this way as opposed to with Altium's built in layout replication, is that the exact components don't have to match because the LLM can look through the descriptions and understand which components match and which don't have a match. That's something that can't really be hard coded.
![Placement Duplicator](assets/placement_duplicator.gif)

### PCB Footprint Library
- `create_pcb_footprint`: Create a new PCB footprint in the currently active .PcbLib document. Supports SMD pads (Rect, Round, Oval shapes) defined in mm relative to the component origin. Auto-generates a courtyard on Mech 15 and silkscreen with a pin 1 indicator (gap in the top-left corner), or accepts explicit courtyard dimensions. Contributed by [coffeedust](https://github.com/coffeedust) ([PR #7](https://github.com/coffeenmusic/altium-mcp/pull/7)).
- `get_footprint_primitives`: Inventory a .PcbLib (per-footprint primitive counts) or dump complete footprint geometry - pads with full stack/hole detail, tracks, arcs, fills, texts, regions - in mils. The reference source when recreating or verifying footprints. 3D bodies are excluded as models. A library the call opens is closed again at its end, as `get_symbol_primitives` does; one open before stays open.
- `create_footprints_batch`: Create many footprints in one script run from a plain-text spec file: SMD + through-hole pads (holes, slots, plating, rotation, full pad stack), tracks, arcs, fills, texts, and regions on any layer. Round-trip verified against complete production SMD and through-hole libraries.

### Both
- `get_screenshot`: A picture of a PCB or schematic document, printed by Altium itself: the bridge adds an OutJob holding one PCB Print or Schematic Print to the document's own project for the length of the print, publishes it to PDF in the request's folder, takes it out again and puts back the project's modified flag and the focus; the server rasterises the PDF (`pypdfium2`) and returns the image. The print shows the document as it is in memory, unsaved changes included, whatever Altium's window shows: a window grab held the previously shown document's pixels while the tab and title named the requested one (2026-10-23). `document` names the `.PcbDoc` or `.SchDoc` (default: the focused one of `view_type`), `zoom_to` prints the bounding box of those components plus a margin instead of the whole board, `width` sets the picture's longer side. A board prints the layers it displays, top side over bottom. A picture pixel-identical to the previous capture (any server, any kind; `last_capture.json` beside the mailbox) and a blank print come back with `success: false` and the image. `view_type: "dialog"` grabs Altium's topmost dialog from its window, the one thing without a print. A free document (in no project) does not print: an OutJob resolves no document outside a project. Live 2026-10-23 on a copy of Elch's TEMPMODUL board and sheet: the board in 4-7 s, two components by `zoom_to`, a sheet in 4-5 s, a part moved by `place_components` (not saved) shown moved, the same print twice refused the second time.

### Scripting / Development
- `run_altium_script`: Run a DelphiScript snippet in an **isolated sandbox script project** and get back a step-by-step log, the script's result, and - when a script dies - the exact statement that killed it. Altium has no headless test mode: a runtime error leaves the script paused in the debugger with no dialog, after which every later run silently does nothing until the debugger is stopped (Ctrl+F3) or Altium restarts. This tool detects that state and reports it. Because the sandbox is a separate script project, a crash can never break the other MCP tools. A run that finishes closes the sandbox project, so the next run reads its unit fresh from disk and the operator's workspace keeps no sandbox. Useful for developing and verifying new Altium API code before building a tool around it. While waiting it closes blocking Altium dialogs (only windows owned by `X2.EXE`) and returns their titles in `dismissed_dialog_titles` - close your own Altium dialogs before running scripts, since an open dialog of yours may be closed too.
- `ensure_altium_script_skill`: Check whether the [altium-script skill](https://github.com/coffeenmusic/altium-scripts-skill) (Altium DelphiScript API reference, examples, and conventions) is installed, and install it on request. Skills load at client startup, so a newly installed skill appears after a restart.

### Server Status
- `get_server_status`: Check the status of the MCP server, including paths to Altium and script files

## How It Works

The server communicates with Altium Designer using a scripting bridge and a mailbox folder, `C:\Users\Public\altium_mcp\`, which every altium-mcp server on the machine (one per client session) and the one running Altium share. One script runs in Altium at a time, so a server holds the one-script lock (`server/script_lock.py`) for a whole call.

1. It writes the request into a folder of its own, `mail\<id>\` (`request.json` with the command, its parameters, `request_id` and `sender_pid`, beside the spec files the command reads), then queues it as `queue\<id>.req`. The id is `<sender pid>-<milliseconds>-<sequence>`.
2. It runs Altium with the `Altium_API.PrjScr` script, from a copy under `bridge\` whose folder is named by the hash of the script files: Altium runs the in-memory text of a script project it already has open, so an edited script gets a new copy. The run closes the copy at its end, with any copy an older run left open, so the operator's workspace keeps none of them and the next run opens it from disk.
3. The script takes the queued requests of the server that holds the lock, each by renaming its queue entry into its folder (`taken`), so a request is taken once and a request left behind by a server that gave up is never run. It writes progress records to the folder's `progress.log` (`seq|elapsed ms|step|count|total`, one per op of an edit, symbol, footprint, output container or print step) and its answer, which echoes `request_id`, to `response.tmp`. The libraries a command opened are closed before its answer, a failed command's too, and the bridge's own script project after it: Altium lets a script close the project it runs from and runs on (`Altium_API.pas` `Run`). Only then is each answer renamed into place whole as `response.json`, with `closed.txt` beside it naming each copy `CLOSED`, `STILL OPEN` or `REFUSED`, so a server holds the lock until the bridge is done.
4. The server waits for its own `response.json` and accepts only its own id. It waits as long as records keep landing and gives up when none lands for 120 s (900 s for an output container, 300 s for a print: one blocking Altium process each), not after a fixed time. A box Altium shows during the call is pictured into the request's folder and closed; one shown before the bridge took the request is Altium refusing the RunScript while another script finishes, and the RunScript is sent again. Each call rewrites `status.json` (id, pid, command, state, the last record) for a watchdog, and reports progress to the client when it asks for it.

```
C:\Users\Public\altium_mcp\
  altium_script.lock      who runs a script now
  queue\<id>.req          a request waiting for the bridge
  mail\<id>\              request.json, spec files, taken, progress.log, response.json, closed.txt, a print's shot.pdf
  status.json             the call in flight
  last_capture.json       get_screenshot's previous picture, for the identity check
  bridge\<hash>\          the staged script copies
  request.json, response.json   the single-file protocol of servers started before the mailbox
```

A server started before the mailbox writes `request.json` at the root and waits 120 s for `response.json` there; the bridge still answers it while such a server holds the lock (its lock owner names no `(mailbox)`), until every server has restarted. Request folders are deleted after a day. `server/mailbox.py` holds the protocol and its layout; `dev/README.md` § The mailbox has the details.

Files exchanged with the scripts are read and written the way Altium does: in the Windows ANSI code page, or as UTF-8 when the file starts with a byte-order mark.

### What the bridge carries

- **To Altium, every character.** `request.json` and the spec files (`edit_spec.txt`, `annotate_spec.txt`, `circuit_spec.txt`) are UTF-8 with a byte-order mark and their text unescaped, so `TStringList.LoadFromFile` decodes them and every value reaches the script as typed, including the array items and values the line parsers take apart. Checked at file level with "Fühler 25 °C ß" (`server/tests/test_bridge_text.py`) and live (2026-10-23): an `edit_schematic` comment came back from the saved sheet as typed.
- **A script, the ANSI code page.** `run_altium_script` writes the sandbox unit in the ANSI code page (cp1252 here), the way Altium's editor saves a script; a character that code page lacks is refused before Altium runs, by line, column and code point: write it as `Chr(<code>)`. Live 2026-10-23: `ResultText := 'Fühler 25 °C ß'` came back intact, `'Ω'` was refused in a second without reaching Altium.
- **From Altium, the ANSI code page.** The scripts' `SaveToFile` writes the response in the ANSI code page, so a character outside it comes back as `?`.
- **The file holding a character is not the board drawing it.** Altium's stroke fonts carry IBM code page 437 while Altium hands them the ANSI code, so a silk `ä` plots as `Σ` and `·` as `╖` on the Gerbers although the PcbDoc holds the right character; any text with a character beyond ASCII takes a TrueType font (Elch TEMPMODUL, 2026-10-23).

## Facts the boards found

Each one cost a session; the date and the board say where.

- **A bridge call waits while the bridge reports progress** (`server/mailbox.py`), and gives up after 120 s without a record; it waited a fixed 120 s before, so a batch longer than that reported a timeout while Altium finished and saved it, and an `edit_schematic` call took about 15 parts (25 s on S31DEV, 2026-10-22). 60 parts in one call: 80 s, live 2026-10-23.
- **Altium refuses a RunScript while another script is still running** with a modal box of its own ("Another script executing now. Scripting system is not able to execute more than one script simultaneously."), which then holds Altium until it is closed; it does not queue the run. It met a bridge call that took the lock as the previous holder's script was still returning (2026-10-23); the server closes the box and sends the RunScript again, and holds the lock 0.3 s after an answer.
- **Altium may open a Sign In window when it starts** (a WPF window titled "Sign In", class `HwndWrapper[X2.EXE;...]`); a script run waits behind it until it is closed by its Cancel button. `c:/Projects/tools/altium/altium_run.py` cancels it (tools 18c4fdb, from the night of 2026-10-22); the bridge does not. The licence is perpetual but served on demand (License Management: On-Demand · Single Site · Perpetual), so every start waits 16–17 s for the account's silent sign-in before it runs the bridge's RunScript, and the window is that sign-in failing; turning off `AltiumPortal\Account\SigninAtStartup` does not stop it. All four starts after a clean exit (`X2.EXE -RClient:QuitFromEDAClient`) signed in silently, four of seven after a kill did not (2026-10-23). The startup measurements and the preferences changed that day with their old values (no Home page, no project group, no restored documents, no splash: a bare start's window 56–63 s to 5–6 s, the bridge's cold answer 24.5 s to 21.3 s): [`dev/altium_startup.md`](dev/altium_startup.md).
- **A document opened by a relative path appears twice**: Altium names a document by the path it was opened with, so the same file under a second spelling opens a second copy and asks to reload. Pass absolute paths (raw-head adapter, 2026-10-23).
- **An Altium started with `CLAUDE_PROJECT_DIR` in its environment rewrites the house library entries**: it saves an installed library's path with the most specific environment variable put in, so a library under `C:\Projects` was stored as `%AltiumSystemLibrary%%CLAUDE_PROJECT_DIR%\...`, which an Altium started without the variable cannot resolve (the raw-head adapter's ECO: Footprint Not Found). Nine such entries stood beside absolute copies of two of them (scratch slot, 2026-10-23). The bridge, `run_altium_script`, the dev sandbox and `dev/unwedge.py` now start Altium without it (`altium_files.altium_env`). The repair: in the running Altium `IntegratedLibraryManager.UninstallLibrary(path)` with the path `InstalledLibraryPath(i)` returns (resolved), then `InstallLibrary(absolute path)` for each house IntLib and DbLib; Altium writes the list to `HKCU\Software\Altium\Altium Designer {GUID}\DesignExplorer\Preferences\IntegratedLibrary\Loaded Libraries` (`LibraryN`, `LibraryRelativePathN`, `LibraryActivatedN`, `LibraryViewSettingsN`) only when it saves its preferences, so with Altium closed that key was rewritten to the same list (`c:/Projects/tools/altium/installed_libs.py` reads it back). The proof of a footprint lookup is the ECO: `FindDatafileInStandardLibs` resolves only a standalone PcbLib named by its path, `FindDatafileEntityLibraryPath` returned nothing for a footprint inside an IntLib, and `FindDatafileInStandardLibs(..., True, ...)` searched for minutes until Altium was killed.
- **A sheet's in-memory copy outlives `Client.CloseDocument` while its project is open**: an edit that paused half-way was still in the next `OpenDocument` and in the project's compile until Altium restarted (scratch slot, 2026-10-23).
- **Altium's Gerber X2 carries no net names** here (file and aperture attributes only, no `%TO.N`); IPC-2581 carries them on each padstack (`net="+5V"`), so a net export to check takes IPC-2581 (scratch slot, 2026-10-23).
- AD25 API and ECO facts (component kinds, Mechanical parts in Update PCB, `Repeat()` entries, via tenting, a rule's enable flag): [`dev/README.md`](dev/README.md) § AD25 facts.

## References
- Get scripts' project path from Jeff Collins and William Kitchen's stripped down version
- BlenderMCP: I got inspired by hearing about MCP being used in Blender and used it as a reference. https://github.com/ahujasid/blender-mcp
- Used CopyDesignatorsToMechLayerPair script by Petar Perisin and Randy Clemmons for reference on how to .Replicate objects (used in layout duplicator)
- Petar Perisin's Select Bad Connections Script: For understanding how to walk pcb primitives (track, arc, via, etc) connected to a pad
- Matija Markovic and Petar Perisin Distribute Script: For understanding how to properly let the GUI know when I've updated tracks' nets
- Petar Perisin's Room from Poly: Used as reference to detect poly to pad overlap since I couldn't get more tradition methods to work.
- Petar Perisin's Layer Panel Script: Used as reference for getting layers and changing layer visibility
- Jeff Collins has an XIA_Release_Manager.pas script that taught me the art of the Output Job. See his post on the Altium Forums: https://forum.live.altium.com/#/posts/189423

## Contributors
- [daescobarh](https://github.com/daescobarh) — project validation tools (`get_project_info`, `get_project_components`, `get_erc_violations`, `get_board_summary`, `get_component_primitives`, `get_outjob_settings`) and safer dialog dismissal ([PR #18](https://github.com/coffeenmusic/altium-mcp/pull/18))
- [coffeedust](https://github.com/coffeedust) — `create_pcb_footprint` tool for PcbLib automation ([PR #7](https://github.com/coffeenmusic/altium-mcp/pull/7))
- [fwolter](https://github.com/fwolter) — Fix JSON parsing error when the decimal separator is a comma ([PR #3](https://github.com/coffeenmusic/altium-mcp/pull/3))

## Disclaimer
This is a third-party integration and not made by Altium. Made by [coffeenmusic](https://x.com/coffeenmusic)

# TODO:
- `edit_schematic` place: a part copied out of a .SchLib keeps the library symbol's UniqueId, so every copy of one symbol shares it until Altium reassigns them on the next load (27 of S31DEV's 41 parts, 2026-10-22). Give each placed part a fresh id after `EdReplicate`; until then open and save the sheet once before an ECO.
- Change selection filter:
  - `scripts-libraries\Scripts - PCB\FilterObjects\`
  - `scripts-libraries\Scripts - SCH\SelectionFilter\`
- Show/Hide Panels: `DXP/ReportPCBViews.pas`
- Create rules: `PCB/CreateRules.pas`
- Run DRC: IPCB_Board.RunBatchDesignRuleCheck( 
- Move cursor to position: IPCB_Board.XCursor, IPCB_Board.YCursor 
- Add get schematic & pcb library path for footprint. 
- log response time of each tool
- Add go to schematic sheet
- Go to sheet with component designator
- Board.ChooseLocation(x, y, 'Test');
- Zoom to selected objects:
- Change Schematic Selection Filter: SelectionFilter.pas
- How can I read through components from libraries in Components panel?

TODO Tests:
Need to add the following test units
- `get_pcb_layers` 
- `set_pcb_layer_visibility`
- `layout_duplicator`
