# Altium's startup: what it loads, what was changed, how to undo it

What Altium Designer 25.6.2 on this box (zbook-16g9) does between `X2.EXE` starting and the bridge's first
answer, read from Altium's own configuration and logs, and every preference changed to make that shorter and
quieter, with its old value. Preferences live under
`HKCU\Software\Altium\Altium Designer {9A5728EE-2059-46DE-A248-E7E4AD513F11}\DesignExplorer\Preferences`
(written `…\Preferences` below). The whole `HKCU\Software\Altium` tree as it stood before any change is in
`dev/altium_prefs_backup_2026-10-23.reg`, kept out of git: its `AltiumPortal\Account` key holds the account's
session tokens.

## How Altium keeps these settings

- **A preference is a registry value, mostly the text `"0"` or `"1"`**, under the server and page that own it.
  The page's checkbox is named in the server's form resources, so a value maps to its label with no guessing:
  `Check_OpenTasks` "Open Home page on start" is `Open tasks if no page open`, `Check_ShowStartup` "Show
  startup screen" is `Suppress Startup Screen` (inverted), `Check_ReopenWorkspace` "Reopen Last Project Group"
  is `Reopen Workspace` (System > General, `WorkspaceManager.dll`); `CheckBox_SigninAtStartup` "Sign me in
  when I start Altium Designer" is `AltiumPortal\Account\SigninAtStartup` (`AltiumPortalCache.dll`,
  `WorkspaceManager.dll`).
- **A clean exit rewrites the preference keys from memory.** At the exit of 2026-10-23 01:46:20
  (`DXP_Shutdown.log`) Altium wrote 39 keys within a second, `Client Preferences`, `Workspace Preferences`,
  `IntegratedLibrary`, `SCH` and `AdvPCB` among them. A value edited while Altium runs is therefore undone by
  File > Exit and kept by a kill (`taskkill /F` writes nothing): edit with Altium closed. `AltiumPortal\Account`
  is not rewritten at exit (last written 2026-10-07).
- **Every `X2.EXE -R…` call overwrites `DXP_Startup.log`** (`%LOCALAPPDATA%\Altium\Altium Designer {GUID}\`)
  with a four-line log of the second process that hands its command to the running one, so the full startup
  log of a cold start survives only until the next bridge or runner call. `DXP_SplashScreen.log` keeps the last
  real start's server timeline (each line's first number is the time since the line before it, so a step's cost
  is the number on the line after it).

## Where a cold start's time goes

The bridge's own log (`server/altium_mcp.log`) holds every call's launch and answer. A warm call answers in
1.0–1.5 s (`get_project_info`) or 2–7 s (`open_project`); a call that started Altium took 26.5 s (2026-10-22
17:30), 27.0 s (10-23 00:32), 24.0 s (08:30), 21.9 s (12:44), 37.1 s (13:11), 35.1 s (13:28) and 58.9 s (14:37),
and two `open_project` calls timed out at the bridge's 120 s (00:55, 09:24; whether Altium was starting then was
not logged). So about 24 s of the usual 26 s is Altium starting and the bridge's first run in it, and the
outliers are 10–35 s more.

The 14:37:34 start, from Altium's own files:

| After start | What |
|---|---|
| +4.4 s | embedded Chromium starts (`BrowserCache\lockfile`; `CEF_debug.log` 14:37:38.787) |
| +6.8 s | splash phase done (`DXP_SplashScreen.log`): 3.9 s of server starts, the Schematic Editor 703 ms the largest |
| +32.6 s | the account's avatar is downloaded (`UserProfileImages\*.png`): the automatic sign-in completed |
| +33.6 s | the licence file `…_Security\LIC-LL-20261022-51253856-R10.alf` and `Security.dat` are rewritten, and `NetworkLicenseUsage` |
| +51.6 s | an embedded page's frame times out (`CEF_debug.log` "connection timeout") |
| +58.8 s | an embedded `my.altium.com` page (`check_logged_in=1`) finishes loading |
| +58.9 s | the bridge's `open_project` answer |

Between +6.8 s and the answer nothing on disk says what Altium waited on; the window's measured launches (below)
copy the full `DXP_Startup.log` of a cold start and trace the X: share and Altium's network peers during it.

## Inventory

| Setting (Altium's label) | Key, value | Was | House need | Done |
|---|---|---|---|---|
| Open Home page on start | `…\Client\Client Preferences`, `Open tasks if no page open` | `1` | none: the Home page is the Engagement server ("News and learning materials"), a web page in embedded Chromium | `0` |
| Reopen Last Project Group | `…\WorkspaceManager\Workspace Preferences`, `Reopen Workspace` | `1` | none: it reopened `%APPDATA%\Altium\…\LastWorkspace\Project Group 1.DsnWrk`, the factory's five `Generate_All_*.PrjScr`; every house tool opens what it needs, and Altium runs the in-memory text of a script project it holds open | `0` |
| Show startup screen | `…\Client\Client Preferences`, `Suppress Startup Screen` | `0` | none | `1` |
| Restore open documents | `…\Client\Client Preferences`, `Restore open documents` | `1` | moot with no project group reopened | kept |
| Sign me in when I start | `…\AltiumPortal\Account`, `SigninAtStartup` | `1` | none if the licence is standalone | held for the window |
| Network activity | `…\Client\Client Preferences`, `AllowedNetworkActivity` | 10 areas | see below | 2 removed |
| Check for updates | `…\ExtensionManager\AutoUpdatePrefs`, `UpdatesCheckFrequency` | `0` = Never (`TUpdateCheckFrequency`: Never, EveryDay, Every3days, EveryWeek, Every2Weeks, EveryMonth, OnStartUp) | none | already off |
| Product Improvement Program | `…\ProductImprovementProgram\General`, `Enabled` | `0` | none | already off |
| Altium Content Vault | `…\EDMSInterface\Vaults`, `Enabled0` | `1` | the Components panel's online parts and Manufacturer Part Search; no house tool uses them | the operator's call |
| Desktop layout | `…\Client\Client Preferences`, `Autosave Tools Layout` | `1` | the panels as left | kept |
| Default printer | Windows | Microsoft Print to PDF, `LegacyDefaultPrinterMode` 1 (Windows does not manage it) | the house setting (`c:/Projects/tools/altium/README.md`) | confirmed |
| "Subscription Expired" in the title | none | | a licence-state label (`X2.EXE`'s strings: No License, Subscription Expired, Trial, Viewer); no preference hides it, and the licence is not touched | — |

**Network activity** (System > Network Activity; Altium's hint for each, from `WorkspaceManager.dll`):
Altium Portal (sign-in; unchecking it also unchecks Cloud and Global Update), Altium Cloud, Data Management
Server (vaults and workspaces: the Content Vault above), Built-In Browser (online help and search), License
Server ("all License Server related traffic": kept), Parts Providers (suppliers), Global Update Service,
Sending Crash Reports, Publishing (output-job destinations), Product Improvement. Global Update Service and
Product Improvement were removed: both features were already off. Parts Providers, Altium Cloud and Data
Management Server serve the Components panel's online search and are the operator's call.

## Installed libraries

Fifteen, from `…\IntegratedLibrary\Loaded Libraries`, all active. Read cost is a plain Python read of the
file on 2026-10-23 16:3x (X: is `\\czukornas2\3TB`, reached over Tailscale at 100.67.82.98: about 120 ms per
file operation, 5.4 MB/s). Whether Altium reads each at launch or at first use is what the window's SMB trace
shows.

| # | Library | Where | Size | Read | Note |
|---|---|---|---|---|---|
| 0 | Simulation Generic Components.IntLib | Mixed Simulation extension | 0.66 MB | <1 ms | the house does not simulate |
| 1 | Celestial Altium Library DbLib | `C:\altium-library`, database `db.altiumlibrary.com,1433` (SQL Server, internet) | 0.49 MB | <1 ms | 311 tables; the running Altium held a connection to it (16:41) |
| 2 | blib.PcbLib | `X:\#sync\bembedded\altiumLibs` | 29.9 MB | 5.6 s, twice | legacy, read-only |
| 3 | blib.SchLib | same | 1.4 MB | 7.5 s first, 0.4 s again | legacy, read-only |
| 4 | Cap-Gen.DbLib (legacy) | `X:\#dmech\CAD\AltiumLibs\Components-Generic` | 10 kB + workbook 0.24 MB | 0.4 + 0.2 s | 6 tables, Excel through ACE OLEDB over SMB |
| 5 | Res-Gen.DbLib (legacy) | same | 20 kB + workbook 1.25 MB | 0.4 + 0.4 s | 15 tables |
| 6, 7 | Res-Gen.DbLib, Cap-Gen.DbLib (house) | `altium-lib-factory\house` | <5 kB + workbooks | <1 ms | same file names as 4 and 5 (`installed_libs.py` flags it) |
| 8–14 | dnbm (12.4 MB), Res-Gen, Cap-Gen, CSI (3.6 MB), Molex (21.1 MB), Molex sealed, TE DEUTSCH IntLibs | `altium-lib-factory\house\*\Project Outputs for *` | | ≤7 ms | |

**Altium holds every installed IntLib open**: on 2026-10-23 16:4x each of the seven house IntLibs refused a
rename (a handle opened for delete, sharing everything, got a sharing violation), while their
SchLib, PcbLib, DbLib and workbook files were free. That handle, not the documents alone, is why the library
batch needs Altium closed (`altium-lib-factory/tools/build_all_libraries.py`).

## Extensions

Twenty optional extensions are installed (`C:\ProgramData\Altium\Altium Designer {GUID}\Extensions\
ExtensionsRegistry.xml`). An extension's server starts at launch when its `.ins` says `SystemExtension = True`;
the rest load when first used. The cost is from the 14:37 `DXP_SplashScreen.log`.

| Extension | At launch | Cost | House |
|---|---|---|---|
| Tasklist (Altium Workflow server, Tasklist panel) | yes | 375 ms + 391 ms | not used: managed-project workflows |
| VaultExplorer (Explorer) | yes (in the splash log; its `.ins` sets no flag) | 422 ms + 16 ms | not used: managed content |
| Altimade Manufacturing | yes | 125 ms | not used: ordering service |
| Mixed Simulation | yes | 94 ms | not used; owns library 0 |
| Product Improvement Program | yes | 16 ms | off already |
| MCAD CoDesigner | lazily (initialised 11 min after the 14:37 start) | — | not used; logs every project focus change (541 lines on 2026-10-23 in `%LOCALAPPDATA%\Altium\Altium CoDesigner\AltiumDesignerExtension.log`) |
| Draftsman, Signal Integrity Analysis, xDX Designer Importer, PDF3D Exporter, Component Template Editor, Single Component Editor, Component From Supplier Search, Schematic symbol generation tool, Project History, VCS Provider Git and SVN | no | — | not used, or rarely |
| IPC2581, KiCad Importer, IPC Footprint Generator | no | — | used (IPC-2581 is the net export, README) or kept for imports |

Altium has no switch that disables an extension and keeps it installed: Extensions & Updates only removes one.
Nothing was changed here. The candidates the house never uses are Tasklist, Altimade, Mixed Simulation, Product
Improvement Program and MCAD CoDesigner, together about 1.0 s at launch. Taking them out of the launch would mean
editing `SystemExtension = True` to `False` in each one's `.ins` under that folder. That edits a program file,
not a preference, so it waits for the operator's yes or veto.

## Changed on 2026-10-23

Written at 16:47 with Altium running (another session's live work), so the change takes effect at the next
start only if that Altium is killed rather than exited; the window re-applies it with Altium closed.

| Key | Value | Old | New |
|---|---|---|---|
| `…\Client\Client Preferences` | `Open tasks if no page open` | `1` | `0` |
| `…\Client\Client Preferences` | `Suppress Startup Screen` | `0` | `1` |
| `…\Client\Client Preferences` | `AllowedNetworkActivity` | `AltiumPortal;AltiumCloud;AltiumVault;Browser;LicenseServer;Suppliers;GlobalUpd;CrashReports;Publishing;ProductImprovement` | the same without `GlobalUpd` and `ProductImprovement` |
| `…\WorkspaceManager\Workspace Preferences` | `Reopen Workspace` | `1` | `0` |
| `.gitignore` | `dev/altium_prefs_backup_*.reg` | | added |

To undo one, with Altium closed (`K` = `HKCU\Software\Altium\Altium Designer {9A5728EE-2059-46DE-A248-E7E4AD513F11}\DesignExplorer\Preferences`):

```
reg add "K\Client\Client Preferences" /v "Open tasks if no page open" /t REG_SZ /d 1 /f
reg add "K\Client\Client Preferences" /v "Suppress Startup Screen" /t REG_SZ /d 0 /f
reg add "K\Client\Client Preferences" /v "AllowedNetworkActivity" /t REG_SZ /d "AltiumPortal;AltiumCloud;AltiumVault;Browser;LicenseServer;Suppliers;GlobalUpd;CrashReports;Publishing;ProductImprovement" /f
reg add "K\WorkspaceManager\Workspace Preferences" /v "Reopen Workspace" /t REG_SZ /d 1 /f
```

or all of it: `reg import dev\altium_prefs_backup_2026-10-23.reg` with Altium closed. That import also restores
every other value as it stood at 16:46, the installed library list included.

## Held: the Sign In

`SigninAtStartup` `1` makes Altium sign in to the account at every start. The WPF "Sign In" window ("Please
sign in with your browser") is, as far as the files show, that sign-in when it cannot finish silently; the window's
launches show whether `0` removes it. A script waits behind it: `altium_run.py` and the watchdog cancel it, the
bridge does not. The 14:37 start signed in silently, and the licence file was rewritten one second
later. The licence file is encrypted, and its name carries `LL`, which could stand for a leased (on-demand)
seat, which needs the account. Against that, runs after a cancelled Sign In have worked licensed (the factory
on 2026-10-21, the runner's cancels since the night of 2026-10-22). So `0` is set only after Altium's own
License Management page (`X2.EXE -RClient:Licensing`) shows the licence in use as standalone, in the window. If
it shows on-demand, the value stays `1`.

## Ideas, by seconds saved

1. **No sign-in at start** (held, above): the Sign In window holds every script until it is cancelled, and the
   bridge cannot cancel it, so a bridge call that starts Altium into it waits out its 120 s (two `open_project`
   calls timed out on 2026-10-23, cause not logged). The 14:37 start signed in silently and answered at 58.9 s, after
   the sign-in (+32.6 s), the licence refresh (+33.6 s) and an embedded `my.altium.com` page (+58.8 s). The gain
   runs from nothing (a sign-in that overlaps the rest) to a 96 s timeout; the window measures it.
2. **Keep Altium running** (warm start): a warm `get_project_info` answers in 1.0–1.5 s against about 24 s
   cold, so each start avoided saves about 23 s. Idle it holds memory, not CPU: 6.4 GB private for X2 and 0.6 GB
   for ten Chromium subprocesses after a day's work, on a 64 GB box, and 0.67 s of CPU in 15 s with other
   sessions working. The house rule that the library batch starts from a closed Altium would change, which needs
   idea 3.
3. **Keep Altium open across a library batch**: the batch closes Altium because Altium holds the installed
   IntLibs open. The window tests whether `IntegratedLibraryManager.UninstallLibrary` of one IntLib releases its
   file. If it does, the batch uninstalls the IntLibs it rebuilds, closes its projects (`FactoryClose` already
   does), rebuilds, and installs them again, without an exit. That saves one start (about 24 s) and a manual
   close per batch.
4. **The X: libraries off the installed list**: blib.PcbLib takes 5.6 s to read over the tailnet, blib.SchLib up
   to 7.5 s, and the legacy DbLibs read their workbooks over SMB. A tailnet outage turns each of these into an SMB
   timeout. That saves up to about 14 s per load, if the window's trace shows Altium reading them at launch.
   `altium-lib-factory/docs/backlog.md` § Native delivery already plans to retire the legacy generic DbLibs and
   blib. A board that still uses them would take them as project libraries.
5. **No Home page and no project-group restore** (done): one fewer embedded web page (the 14:37 answer came
   0.1 s after an embedded `my.altium.com` page finished loading), and five script projects not reopened. The
   window measures the gain.
6. **The optional extensions' launch servers**: about 1.0 s (table above), and only by editing program files.
7. **The Celestial DbLib**: an internet SQL connection held all day; its cost at launch is unknown until the
   window's trace. It stays while the operator places Celestial parts through Altium.
8. **Network activity trimmed** (done): no measurable time; two fewer services Altium may call.

There is no launch switch that skips the project-group restore. `X2.EXE` holds no such switch, and the only
shortcut (`Altium Designer.lnk`) passes no arguments. The preference above is the lever, so the bridge's launch
line is unchanged.

## Measuring a launch

Each measured launch starts from a closed Altium and cancels nothing. It records when X2 was created and when
the main window (`TDocumentForm`, title "… - Altium Designer …") first answers `WM_NULL` within 200 ms. Then
either the bridge's first `get_project_info` answer (`dev/call_tool.py`) or X2 idling below 5 % of one core for
3 s. It also records every Sign In or dialog by first sight, Altium's TCP peers, and the X: share's SMB counters
(`typeperf "\SMB Client Shares(\czukornas2\3TB)\Read Bytes/sec"`, 1 s). It copies `DXP_Startup.log` and
`DXP_SplashScreen.log` before any other `-R` call overwrites them, then ends with `taskkill /F /T`, which
leaves the preferences as set.
