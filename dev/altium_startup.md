# Altium's startup: what it loads, what was changed, how to undo it

What Altium Designer 25.6.2 on this box (zbook-16g9) does between `X2.EXE` starting and the bridge's first
answer, read from Altium's own configuration and logs and measured over twelve launches on 2026-10-23, and
every preference changed to make that shorter and quieter, with its old value. Preferences live under
`HKCU\Software\Altium\Altium Designer {9A5728EE-2059-46DE-A248-E7E4AD513F11}\DesignExplorer\Preferences`
(written `…\Preferences` below). The whole `HKCU\Software\Altium` tree as it stood before any change is in
`dev/altium_prefs_backup_2026-10-23.reg`, kept out of git: its `AltiumPortal\Account` key holds the account's
session tokens.

## Measured: before and after

Twelve launches from a closed Altium, 17:14–17:50, nothing cancelled unless the row says so. Times run from
X2's creation. "Bridge" is from the bridge's own log, launch to answer (`dev/call_tool.py get_project_info`).
"Done" is `DXP_Startup.log`'s "Before Application.Run". "Before" is the old preferences, with the day's last
project group (CTRL, LED240P, TEMPMODUL and a staged script project) and its open documents to restore.

| Launch | Main window shown, answering | Sign In | Bridge | Done | Read from X: |
|---|---|---|---|---|---|
| before, bridge 1 (after a clean exit) | 6.7 s, 8.8 s | — | **24.5 s** | 23.4 s | 1.5 MB |
| before, bridge 2 (after a kill) | 7.6 s, 27.7 s | 28.5 s | timeout at 120 s | — | 1.5 MB |
| before, bare 1 | **62.7 s**, 65.1 s | — | | **63.8 s** | 21.7 MB |
| before, bare 2 | **55.8 s**, 58.1 s | — | | **55.4 s** | 21.7 MB |
| after, bridge 1 (after a kill) | 5.6 s, 9.7 s | 26.3 s | timeout at 120 s | — | 1.5 MB |
| after, bridge 2 (after a kill; Sign In cancelled as `altium_run.cancel_sign_in` does) | 4.8 s, 4.9 s | 26.4 s | 28.2 s | 27.1 s | 1.5 MB |
| after, bridge 3 (after a clean exit) | 5.0 s, 20.0 s | — | **21.4 s** | 20.3 s | 1.5 MB |
| after, bare 1 (after a kill) | **5.7 s**, 5.7 s | 27.0 s | | — | 1.5 MB |
| after, bare 2 (after a clean exit) | **5.2 s**, 20.7 s | — | | **20.8 s** | 1.5 MB |
| final relaunch through altium-mcp (after a clean exit) | 5.1 s, 20.6 s | — | **21.3 s** | 20.3 s | 1.5 MB |

Two launches in between: the old values with only the Home page and the project group off (46 s to the window,
the restored documents alone) and the sign-in test below. No bare launch, before or after, dropped below 5 % of
one core for 3 s within 180 s.

- **The bare launch went from 56–63 s to 5–6 s to the main window**, and Altium's own startup from 55–64 s to
  21 s: no Home page, no project group, no restored documents. X: reads fell from 21.7 MB in 828 reads to 1.5 MB
  in 5.
- **The bridge launch went from 24.5 s to 21.3–21.4 s.** An Altium started by `-RScriptingSystem:RunScript` loads
  no document of the last project group even with the old values (both before-bridge launches: the Home page and
  "Free Documents", while the bare launches logged 37 document loads). So the bridge only gains the Home page.
- **What remains in every start is the licence.** Altium runs its command line, the bridge's RunScript, only
  once it holds the licence. Before that it waits 15.8–17.5 s for the account's silent sign-in (the gap after
  "Server Altium.Edp.ComponentSearch.Plugin started", ending at "Starting server SIM"). When the silent sign-in
  fails, the "Sign In" window comes at 26–28 s and everything waits on it.

## The licence, and the Sign In

Altium's License Management page (`X2.EXE -RClient:Licensing`, 17:26) reads: Altium Designer Standard, In
Use, Seats 1/1, License Info **On-Demand · Single Site · Perpetual**, subscription renewal 6/23/2025 Expired
(buttons Roam and Release). The licence is perpetual and served on demand from the account, not a standalone
licence file. The `…_Security\LIC-LL-<date>-<n>-R10.alf` file is its lease: the 17:26 sign-in replaced
`LIC-LL-20261022-51253856` with `LIC-LL-20261023-51314921`. "Subscription Expired" in the title is the licence's
state label, cosmetic, and no preference hides it.

- **`SigninAtStartup` does not stop the Sign In.** With it set to `0` (`CheckBox_SigninAtStartup`, "Sign me in
  when I start"), the window still came at 69.5 s: the on-demand seat needs the account either way, and with `1`
  the silent sign-in at least succeeds after a clean exit. It stays `1`; nothing licence-related was changed.
- **After a kill, the next start often asks to sign in; after a clean exit it did not.** All four launches that
  followed a clean exit signed in silently. Four of the seven that followed a `taskkill /F` met the Sign In (the
  launch with the sign-in off not counted), and the three that did not were the slow old-preference bare launches
  (46–64 s). A clean exit shows "Altium Designer (25.6.2) - No License" in the
  title as it releases the seat. That the killed instance keeps the one seat (Seats 1/1) is the likely cause, not
  proven. The house kills Altium in `watchdog.py --recover`, `dev/sandbox_runner.py --auto-restart` and
  `dev/unwedge.py --auto-restart`. `X2.EXE -RClient:QuitFromEDAClient` is the clean exit; it asks about unsaved
  documents ("Unsaved Changes", a WPF window).
- **Cancelling the Sign In keeps the licence**: after-bridge 2 cancelled it at 27.4 s and answered at 28.2 s, as
  the runner's cancels have since 2026-10-21. The bridge does not cancel it. Its watch closes `#32770`,
  `TMessageForm` and `TForm_RunScriptParameters` boxes only, so it waits out its 120 s and then reports "no box
  and not hung" while the Sign In is on screen.

## How Altium keeps these settings

- **A preference is a registry value, mostly the text `"0"` or `"1"`**, under the server and page that own it.
  The page's checkbox is named in the server's form resources (`WorkspaceManager.dll`), so a value maps to its
  label with no guessing. System > General: `Check_OpenTasks` "Open Home page on start" is `Open tasks if no page
  open`, `Check_ShowStartup` "Show startup screen" is `Suppress Startup Screen` (inverted), and
  `Check_ReopenWorkspace` "Reopen Last Project Group" is `Reopen Workspace`. System > View > Desktop:
  `Check_RestoreDocuments` "Restore open documents" is `Restore open documents`.
- **A clean exit rewrites the preference keys from memory.** The exit at 2026-10-23 17:13:37 put all four values
  written at 16:47 back to their old ones, and the one at 01:46:20 wrote 39 keys within a second. Edit with
  Altium closed; a kill writes nothing. `AltiumPortal\Account` is not rewritten at exit.
- **Every `X2.EXE -R…` call overwrites `DXP_Startup.log`** (`%LOCALAPPDATA%\Altium\Altium Designer {GUID}\`)
  with the four-line log of the process that hands its command over, so a cold start's full log survives only
  until the next bridge or runner call. `DXP_SplashScreen.log` holds the splash phase, step by step. It is
  written only while the startup screen shows, so with it off that log stops. Each line's first number is the
  time since the line before it, so a step's cost is the number on the line after it.
- **What the old restore cost**, from the before-bare launches' splash log: the Celestial DbLib 12.3–12.4 s (a
  project library of CTRL), `Connector - Header.SchLib` 8.0–10.1 s, the two Würth DbLibs 9 s, MCAD CoDesigner
  2.9 s, the rest of 37 document loads.

## Changed on 2026-10-23

Applied with Altium closed at 17:35 and in force for the running Altium, which writes them back unchanged at its
next clean exit.

| Key | Value | Altium's label | Old | New |
|---|---|---|---|---|
| `…\Client\Client Preferences` | `Open tasks if no page open` | Open Home page on start | `1` | `0` |
| `…\Client\Client Preferences` | `Suppress Startup Screen` | Show startup screen (inverted) | `0` | `1` |
| `…\Client\Client Preferences` | `Restore open documents` | Restore open documents | `1` | `0` |
| `…\WorkspaceManager\Workspace Preferences` | `Reopen Workspace` | Reopen Last Project Group | `1` | `0` |
| `…\Client\Client Preferences` | `AllowedNetworkActivity` | Network Activity | `AltiumPortal;AltiumCloud;AltiumVault;Browser;LicenseServer;Suppliers;GlobalUpd;CrashReports;Publishing;ProductImprovement` | without `GlobalUpd` and `ProductImprovement` (both features were already off) |
| `.gitignore` | `dev/altium_prefs_backup_*.reg` | | | added |

To undo one, with Altium closed (`K` = `HKCU\Software\Altium\Altium Designer {9A5728EE-2059-46DE-A248-E7E4AD513F11}\DesignExplorer\Preferences`):

```
reg add "K\Client\Client Preferences" /v "Open tasks if no page open" /t REG_SZ /d 1 /f
reg add "K\Client\Client Preferences" /v "Suppress Startup Screen" /t REG_SZ /d 0 /f
reg add "K\Client\Client Preferences" /v "Restore open documents" /t REG_SZ /d 1 /f
reg add "K\WorkspaceManager\Workspace Preferences" /v "Reopen Workspace" /t REG_SZ /d 1 /f
reg add "K\Client\Client Preferences" /v "AllowedNetworkActivity" /t REG_SZ /d "AltiumPortal;AltiumCloud;AltiumVault;Browser;LicenseServer;Suppliers;GlobalUpd;CrashReports;Publishing;ProductImprovement" /f
```

or all of it: `reg import dev\altium_prefs_backup_2026-10-23.reg` with Altium closed. That import also restores
every other value as it stood at 16:46, the installed library list included.

Not changed: the licence and `SigninAtStartup` (above), update checks (already Never: `UpdatesCheckFrequency`
`0`), the Product Improvement Program (already off), the desktop layout, and the Altium Content Vault
(`…\EDMSInterface\Vaults`, `Enabled0` `1`). Parts Providers, Altium Cloud and Data Management Server serve the
Components panel's online parts and Manufacturer Part Search, which no house tool uses: the operator's call. The
default printer is Microsoft Print to PDF with Windows' default-printer management off (`LegacyDefaultPrinterMode`
1), the house setting.

## Installed libraries

Fifteen, from `…\IntegratedLibrary\Loaded Libraries`, all active and resolving to absolute paths in the running
Altium (17:52). X: is `\\czukornas2\3TB`, reached over Tailscale (100.67.82.98): about 120 ms per file
operation, 5.4 MB/s.

| # | Library | Where | Size, plain read | Note |
|---|---|---|---|---|
| 0 | Simulation Generic Components.IntLib | Mixed Simulation extension | 0.66 MB | the house does not simulate |
| 1 | Celestial Altium Library DbLib | `C:\altium-library`; database `db.altiumlibrary.com,1433` (SQL Server, internet) | 0.49 MB | 311 tables; Altium connects at 7–13 s of every start, and nothing waits on it unless a project needs it |
| 2 | blib.PcbLib | `X:\#sync\bembedded\altiumLibs` | 29.9 MB, 5.6 s | legacy, read-only |
| 3 | blib.SchLib | same | 1.4 MB, 0.4–7.5 s | legacy, read-only |
| 4, 5 | Cap-Gen.DbLib, Res-Gen.DbLib (legacy) | `X:\#dmech\CAD\AltiumLibs\Components-Generic` | Excel workbooks over SMB, 0.6–0.8 s | 6 and 15 tables |
| 6, 7 | Res-Gen.DbLib, Cap-Gen.DbLib (house) | `altium-lib-factory\house` | <1 ms | same file names as 4 and 5 (`installed_libs.py` flags it) |
| 8–14 | dnbm, Res-Gen, Cap-Gen, CSI, Molex, Molex sealed, TE DEUTSCH IntLibs | `altium-lib-factory\house\*\Project Outputs for *` | ≤7 ms | held open by Altium |

- **Altium holds every installed IntLib open**: each house IntLib refuses to open for delete with full sharing,
  while their SchLib, PcbLib, DbLib and workbook files open freely. That handle is why the library batch needs
  Altium closed (`altium-lib-factory/tools/build_all_libraries.py`).
- **`IntegratedLibraryManager.UninstallLibrary` releases it.** In the running Altium (17:51), TE DEUTSCH's IntLib
  was still held right after the uninstall and free within 10 s. `InstallLibrary` took it back (held again, last
  in the list as before).
- With nothing restored, a start reads 1.5 MB from X: in 5 reads: the X: libraries cost when a design uses them,
  not at launch.

## Extensions, for the operator's veto

Twenty optional extensions are installed (`C:\ProgramData\Altium\Altium Designer {GUID}\Extensions\
ExtensionsRegistry.xml`). An extension's server starts at launch when its `.ins` says `SystemExtension = True`;
the rest load when first used. The cost is from `DXP_SplashScreen.log` (14:37 and 17:14 starts).

| Extension | At launch | Cost | House |
|---|---|---|---|
| Tasklist (Altium Workflow server, Tasklist panel) | yes | 0.6–0.8 s | not used: managed-project workflows |
| VaultExplorer (Explorer) | yes (in the splash log; its `.ins` sets no flag) | 0.4 s | not used: managed content |
| Altimade Manufacturing | yes | 0.13 s | not used: ordering service |
| Mixed Simulation | yes | 0.09 s | not used; owns library 0 |
| Product Improvement Program | yes | 0.02 s | off already |
| MCAD CoDesigner | with a PCB project (2.9 s in the old restore) | — | not used; logs every project focus change (541 lines on 2026-10-23 in `%LOCALAPPDATA%\Altium\Altium CoDesigner\AltiumDesignerExtension.log`) |
| Draftsman, Signal Integrity Analysis, xDX Designer Importer, PDF3D Exporter, Component Template Editor, Single Component Editor, Component From Supplier Search, Schematic symbol generation tool, Project History, VCS Provider Git and SVN | no | — | not used, or rarely |
| IPC2581, KiCad Importer, IPC Footprint Generator | no | — | used (IPC-2581 is the net export, README) or kept for imports |

Altium has no switch that disables an extension and keeps it installed: Extensions & Updates only removes one.
Nothing was changed. The candidates are Tasklist, Altimade, Mixed Simulation, Product Improvement Program and
MCAD CoDesigner, together about 1 s at launch. Taking them out of the launch means editing `SystemExtension =
True` to `False` in each one's `.ins` under that folder. That edits a program file, so it waits for the
operator's yes or veto.

## Ideas, by seconds saved

1. **A standalone licence, or a roamed seat**: the 16–17 s every start waits for the on-demand licence, and the
   Sign In with it, go away. Converting the seat or roaming it (the page's Roam button) is the operator's licence
   decision; nothing was touched.
2. **The bridge cancels the Sign In as the runner does**: a cold call that meets it answers in 28 s instead of
   timing out at 120 s, and its report names the window instead of "no box and not hung". This is the bridge's
   watch in `server/main.py`, the mailbox's code.
3. **End Altium with a clean exit, not a kill**: of the starts after a kill, four of seven met the Sign In; of the
   starts after a clean exit, none of four. This is for `watchdog.py --recover`, `sandbox_runner.py
   --auto-restart` and `unwedge.py --auto-restart`, where Altium still answers.
4. **Keep Altium running** (warm start): a warm `get_project_info` answers in 1.0–1.5 s against 21 s cold.
   Idle, it holds memory, not CPU: 6.4 GB private for X2 and 0.6 GB for ten Chromium subprocesses after a day's
   work, on a 64 GB box.
5. **Keep Altium open across a library batch**, now shown possible: uninstall the IntLibs the batch rebuilds
   (each is released within 10 s), close the batch's projects (`FactoryClose` does), rebuild, and install them
   again. That saves one start and a manual close per batch, and the house rule that the batch starts from a
   closed Altium would change.
6. **Done**: no Home page, no project group, no restored documents, no splash. The bare launch's window comes
   50–57 s sooner, and the bridge answers 3 s sooner.
7. **The optional extensions out of the launch**: about 1 s, and only by editing program files.
8. **The X: libraries and Celestial off the installed list**: nothing at launch once nothing is restored, so no
   gain there. The factory backlog's retirement of the legacy DbLibs and blib stands on its own grounds.

There is no launch switch that skips the project-group restore. `X2.EXE` holds no such switch, and the only
shortcut passes no arguments. The preferences are the lever, so the bridge's launch line is unchanged.

## Measuring a launch

Each launch starts from a closed Altium. It records X2's creation and when the main window (`TDocumentForm`,
title "… - Altium Designer …") is first shown and first answers `WM_NULL` within 200 ms, plus the bridge's first
`get_project_info` answer (the bridge's log). It records every Sign In or dialog by first sight, Altium's TCP
peers (netstat, 1 s) and the X: share's SMB counters (`typeperf "\SMB Client Shares(\czukornas2\3TB)\Read
Bytes/sec"`, 1 s). It copies `DXP_Startup.log` and `DXP_SplashScreen.log` before any other `-R` call overwrites
them. Then it ends with a clean exit (`-RClient:QuitFromEDAClient`) or `taskkill /F /T`, which leaves the
preferences as set.
