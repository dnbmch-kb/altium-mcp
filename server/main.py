from mcp.server.fastmcp import FastMCP, Context
from mcp.server.fastmcp.utilities.types import Image as MCPImage
import codecs
import configparser
import hashlib
import json
import shutil
import tempfile
import os
import time
import asyncio
import logging
import subprocess
import tkinter as tk
from tkinter import filedialog
from pathlib import Path
from typing import Dict, Any, Optional
import sys
import win32gui
import win32ui
import win32con
import win32api
import win32process
from PIL import Image
import io
import base64
import glob
import re

from altium_files import altium_env, ansi_script, read_altium_text, write_altium_text
from outjob_parser import outjob_project, output_folder, parse_outjob, produced, snapshot
import mailbox
import schematic_edit
import screenshot
import script_lock
import sheet_plan

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,  # Change to DEBUG for more detailed logs
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),  # Output to console
        logging.FileHandler(str(Path(__file__).with_name('altium_mcp.log')))  # Also log to file
    ]
)
logger = logging.getLogger("AltiumMCPServer")

# Set MCP_DIR to the directory of the current Python file
MCP_DIR = Path(__file__).parent
# config.json lives in a per-user folder OUTSIDE the install directory and
# outside AppData. Claude Desktop replaces the extension folder on every
# update, which used to reset a hand-picked Altium path; and being an MSIX
# package it redirects its child processes' AppData writes into its own
# LocalCache. start_server.py passes the folder it chose in ALTIUM_MCP_HOME.
# LEGACY_CONFIG_FILE is read once to migrate.
def _data_dir() -> Path:
    override = os.environ.get("ALTIUM_MCP_HOME")
    return Path(override) if override else Path.home() / ".altium-mcp"

LEGACY_CONFIG_FILE = MCP_DIR / "config.json"
CONFIG_FILE = _data_dir() / "config.json"
DEFAULT_SCRIPT_PATH = MCP_DIR / "AltiumScript" / "Altium_API.PrjScr"

# The mailbox (mailbox.py has its layout): a fixed folder both this server and
# the Altium DelphiScript resolve on their own. C:\Users\Public is writable by
# all users and exists on every Windows machine, and Altium sees it under the
# same path even when Claude Desktop's MSIX package redirects AppData.
EXCHANGE_DIR = Path("C:/Users/Public/altium_mcp")
EXCHANGE_DIR.mkdir(exist_ok=True)
BRIDGE_STAGE_DIR = EXCHANGE_DIR / "bridge"
# A bridge call waits as long as the bridge reports progress and gives up when
# no record lands for this many seconds. A command whose work is one blocking
# Altium process (an output container, a print) reports nothing inside it.
STALL_SECONDS = 120
STALL_BY_COMMAND = {"run_output_jobs": 900, "take_view_screenshot": 300}
# Altium's modal boxes, which hold a running script until they close
DIALOG_CLASSES = ("#32770", "TMessageForm", "TForm_RunScriptParameters")
# Altium refuses a RunScript while a script is still finishing; it is sent again this many times
RUNSCRIPT_RETRIES = 5
# After the answer lands the bridge script still returns; the lock is held this long more, so the next caller's
# RunScript does not meet it finishing
ANSWER_SETTLE = 0.3


def stage_bridge(project: Path, root: Path = BRIDGE_STAGE_DIR) -> Path:
    """The bridge's script project, copied into a folder named by its content.

    Altium runs the in-memory text of a script project it already has open, not
    the file on disk, so a bridge run in place keeps running the text it first
    loaded after a unit is edited. The folder name is the hash of the project
    file and every unit it lists: unchanged sources reuse the same copy, an edit
    gets a new one. Run closes every copy at the end of each run
    (Altium_API.pas), so Altium opens it from disk for the next. It sits in the
    exchange directory, which Altium sees under the same path even when Claude
    Desktop's MSIX package redirects this process's AppData writes."""
    project = Path(project)
    text = project.read_bytes().removeprefix(codecs.BOM_UTF8).decode("latin-1")
    cfg = configparser.ConfigParser(interpolation=None)
    cfg.read_string(text)
    units = []
    for section in cfg.sections():
        if section.startswith("Document"):
            relative = Path(cfg[section]["DocumentPath"].replace("\\", "/"))
            source = (project.parent / relative).resolve()
            if relative.is_absolute() or not source.is_relative_to(project.parent.resolve()):
                raise ValueError(f"script unit escapes its project directory: {relative}")
            if not source.is_file():
                raise FileNotFoundError(source)
            units.append(relative)
    digest = hashlib.sha256()
    for relative in [Path(project.name)] + units:
        digest.update(relative.as_posix().encode() + b"\0" + (project.parent / relative).read_bytes())
    target = root / digest.hexdigest()[:16]
    if not (target / project.name).is_file():
        root.mkdir(parents=True, exist_ok=True)
        (root / ".parked").touch()
        staging = Path(tempfile.mkdtemp(prefix="staging_", dir=root))
        for relative in [Path(project.name)] + units:
            (staging / relative).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(project.parent / relative, staging / relative)
        staging.rename(target)
    return target / project.name

# Initialize FastMCP server
mcp = FastMCP("AltiumMCP", description="Altium integration through the Model Context Protocol")

class AltiumConfig:
    def __init__(self):
        self.altium_exe_path = ""
        self.script_path = str(DEFAULT_SCRIPT_PATH)
        self.load_config()
    
    def load_config(self):
        """Load configuration, migrating a pre-relocation config.json once."""
        source = CONFIG_FILE if CONFIG_FILE.exists() else LEGACY_CONFIG_FILE
        if source.exists():
            try:
                with open(source, "r") as f:
                    config = json.load(f)
                self.altium_exe_path = config.get("altium_exe_path", "")
                # Only a hand-picked script project is persisted. A saved path
                # that no longer exists (old install folder) falls back to the
                # project shipped beside this file instead of prompting.
                saved_script = config.get("script_path", "")
                if saved_script and os.path.exists(saved_script):
                    self.script_path = saved_script
                logger.info(f"Loaded configuration from {source}")
                if source is LEGACY_CONFIG_FILE:
                    self.save_config()
                else:
                    self._saved = self._as_dict()
            except Exception as e:
                logger.error(f"Error loading configuration: {e}")
                self._create_default_config()
        else:
            logger.info("No configuration file found, creating default")
            self._create_default_config()

    def _create_default_config(self):
        """Create a default configuration file with improved Altium executable discovery"""
        
        # Try to find Altium directories dynamically
        altium_base_path = r"C:\Program Files\Altium"
        altium_exe_path = None
        
        if os.path.exists(altium_base_path):
            # Find all directories that match the pattern AD*
            ad_dirs = glob.glob(os.path.join(altium_base_path, "AD*"))
            
            if ad_dirs:
                # Sort directories by version number (extract the number after "AD")
                def get_version_number(dir_path):
                    match = re.search(r"AD(\d+)", os.path.basename(dir_path))
                    if match:
                        return int(match.group(1))
                    return 0
                
                # Sort directories by version number (highest first)
                ad_dirs.sort(key=get_version_number, reverse=True)
                
                # Try each directory until we find one with X2.EXE
                for ad_dir in ad_dirs:
                    potential_exe = os.path.join(ad_dir, "X2.EXE")
                    if os.path.exists(potential_exe):
                        altium_exe_path = potential_exe
                        break
        
        # Set the found path (or empty string if nothing found)
        self.altium_exe_path = altium_exe_path if altium_exe_path else ""
        
        # Save the configuration
        self.save_config()
    
    def _as_dict(self):
        config = {"altium_exe_path": self.altium_exe_path}
        # The default script project belongs to whichever install is running
        # (extension folder or a dev checkout), so it is never written down:
        # both share this file and must not steal each other's scripts.
        if Path(self.script_path) != DEFAULT_SCRIPT_PATH:
            config["script_path"] = self.script_path
        return config

    def save_config(self):
        """Save configuration to file, only when something changed"""
        config = self._as_dict()
        if config == getattr(self, "_saved", None) and CONFIG_FILE.exists():
            return

        try:
            CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(CONFIG_FILE, "w") as f:
                json.dump(config, f, indent=2)
            self._saved = config
            logger.info(f"Saved configuration to {CONFIG_FILE}")
        except Exception as e:
            logger.error(f"Error saving configuration: {e}")

    def verify_paths(self):
        """Verify that the paths in the configuration exist, prompt for input if they don't"""

        # Initialize variables
        root = None
        paths_verified = True
        
        # Check Altium executable
        if not self.altium_exe_path or not os.path.exists(self.altium_exe_path):
            paths_verified = False
            
            # Before prompting, try an automatic discovery
            altium_base_path = r"C:\Program Files\Altium"
            if os.path.exists(altium_base_path):
                logger.info(f"Attempting automatic discovery in {altium_base_path}")
                # Find all directories that match the pattern AD*
                ad_dirs = glob.glob(os.path.join(altium_base_path, "AD*"))
                
                if ad_dirs:
                    # Sort directories by version number (extract the number after "AD")
                    def get_version_number(dir_path):
                        match = re.search(r"AD(\d+)", os.path.basename(dir_path))
                        if match:
                            return int(match.group(1))
                        return 0
                    
                    # Sort directories by version number (highest first)
                    ad_dirs.sort(key=get_version_number, reverse=True)
                    
                    # Try each directory until we find one with X2.EXE
                    for ad_dir in ad_dirs:
                        potential_exe = os.path.join(ad_dir, "X2.EXE")
                        if os.path.exists(potential_exe):
                            self.altium_exe_path = potential_exe
                            logger.info(f"Automatically found Altium at: {self.altium_exe_path}")
                            print(f"Automatically found Altium at: {self.altium_exe_path}")
                            paths_verified = True
                            break
            
            # If automatic discovery failed, prompt for input
            if not self.altium_exe_path or not os.path.exists(self.altium_exe_path):
                if root is None:
                    import tkinter as tk
                    from tkinter import filedialog
                    root = tk.Tk()
                    root.withdraw()  # Hide the main window
                
                logger.info("Altium executable not found. Prompting user for selection...")
                print(f"Altium executable not found. Searched in:")
                print(f"  - Automatically scanned C:\\Program Files\\Altium\\AD*\\X2.EXE")
                print(f"  - Last known path: {self.altium_exe_path}")
                print("Please select the Altium X2.EXE file...")
                
                self.altium_exe_path = filedialog.askopenfilename(
                    title="Select Altium Executable",
                    filetypes=[("Executable files", "*.exe")],  # Only allow .exe files
                    initialdir="C:/Program Files/Altium"
                )
                
                if not self.altium_exe_path:
                    logger.error("No Altium executable selected. Some functionality may not work.")
                    print("Warning: No Altium executable selected. Automatic script execution will be disabled.")
                    paths_verified = False
        
        # Check script path
        if not os.path.exists(self.script_path):
            paths_verified = False
            
            if root is None:
                import tkinter as tk
                from tkinter import filedialog
                root = tk.Tk()
                root.withdraw()  # Hide the main window
            
            logger.info(f"Script file not found at {self.script_path}. Prompting user for selection...")
            print(f"Script file not found at {self.script_path}. Please select the Altium project file...")
            
            selected_path = filedialog.askopenfilename(
                title="Select Altium Project File",
                filetypes=[("Altium Project files", "*.PrjScr")],  # Changed to PrjScr for script project
                initialdir=str(MCP_DIR)
            )
            
            if selected_path:
                self.script_path = selected_path
            else:
                logger.error("No script file selected. Some functionality may not work.")
                print("Warning: No script file selected. Please make sure to create one.")
                paths_verified = False
        
        # Clean up tkinter root if created
        if root is not None:
            root.destroy()
        
        # Save the updated configuration
        self.save_config()
        
        return paths_verified

class AltiumBridge:
    def __init__(self):
        # Ensure the MCP directory exists
        MCP_DIR.mkdir(exist_ok=True)

        # Load configuration
        self.config = AltiumConfig()
        self.config.verify_paths()

        # Commands share a single request.json/response.json pair, so
        # concurrent tool calls must be serialized or they clobber each other
        self._command_lock = asyncio.Lock()

    async def execute_command(self, command: str, params: Dict[str, Any], files: Dict[str, str] = None,
                              stall: float = None, ctx: Context = None) -> Dict[str, Any]:
        """Run a command in Altium through the mailbox: params go into the request, files (name -> text) into
        the request's folder beside it. Waits while the bridge reports progress, gives up after `stall` seconds
        without a record. The answer carries "mail", the request's folder, where the command's output files are."""
        async with self._command_lock:
            holder = await take_script_lock(f"altium-mcp {command} (mailbox)")
            if holder is not None:
                return {"success": False, "error": script_lock.describe(holder)}
            try:
                return await self._execute_command_locked(command, params, files or {},
                                                          stall or STALL_BY_COMMAND.get(command, STALL_SECONDS), ctx)
            finally:
                script_lock.release()

    async def _execute_command_locked(self, command: str, params: Dict[str, Any], files: Dict[str, str],
                                      stall: float, ctx) -> Dict[str, Any]:
        mail = mailbox.Mail(EXCHANGE_DIR, command, params, files)
        try:
            # under the lock, a queued request of anyone else was left by a server that gave up or died
            expired = mailbox.expire_others(EXCHANGE_DIR, mail.id)
            if expired:
                logger.warning(f"expired requests left in the queue: {expired}")
            mailbox.prune(EXCHANGE_DIR)
            mail.post()
        except OSError as e:
            return {"success": False, "error": f"could not write the request to {mail.folder}: {e}"}
        logger.info(f"Queued {command} as {mail.id}")
        status = {"id": mail.id, "pid": os.getpid(), "command": command, "state": "queued", "since": time.time(),
                  "stall_seconds": stall, "folder": str(mail.folder)}
        mailbox.write_status(EXCHANGE_DIR, status)
        before = {h for h, _, _ in x2_windows(DIALOG_CLASSES)}

        if not await self.run_altium_script():
            mail.withdraw()
            status.update(state="failed", error="the bridge script could not be started")
            mailbox.write_status(EXCHANGE_DIR, status)
            return {"success": False, "error": "Failed to run Altium script"}

        async def on_progress(record, records):
            status.update(state="running", last_progress=time.time(), step=record["step"], count=record["count"],
                          total=record["total"], elapsed_ms=record["elapsed_ms"], records=record["seq"])
            mailbox.write_status(EXCHANGE_DIR, status)
            if ctx is not None and record["total"]:
                try:
                    await ctx.report_progress(record["count"], record["total"])
                except Exception:          # no client request behind this call (dev/call_tool.py), or no token
                    pass

        closed, refused, sightings = [], [], {}

        async def picture_and_close(h, cls, title, name):
            shot = mail.file(name)
            try:
                await asyncio.wait_for(asyncio.to_thread(lambda: grab_window(h).save(shot)), timeout=10)
            except Exception:
                shot = None
            ctypes_user32().PostMessageW(h, 0x0010, 0, 0)        # WM_CLOSE
            return f'{cls} "{title}"' + (f" (picture: {shot})" if shot else "")

        async def watch():
            """A box Altium shows during this call. Before the bridge took the request it cannot come from our
            script: Altium refuses a RunScript while another script is still finishing ("Another script executing
            now", a box of its own, 2026-10-23), so the box is closed and the RunScript sent again, the request
            still queued. After the take it holds our script: seen twice (2 s apart), it is pictured into the
            request's folder and closed, the way the runner does; True when one was."""
            acted = False
            taken = mail.taken()
            for h, cls, title in x2_windows(DIALOG_CLASSES):
                if h in before:
                    continue
                sightings[h] = sightings.get(h, 0) + 1
                if not taken and sightings[h] == 1 and len(refused) < RUNSCRIPT_RETRIES:
                    refused.append(await picture_and_close(h, cls, title, f"refused_{len(refused) + 1}.png"))
                    logger.warning(f"Altium refused the RunScript for {command} ({refused[-1]}); sending it again")
                    await asyncio.sleep(1)
                    await self.run_altium_script()
                elif taken and sightings[h] == 2:
                    closed.append(await picture_and_close(h, cls, title, f"dialog_{len(closed) + 1}.png"))
                    logger.warning(f"closed an Altium box during {command}: {closed[-1]}")
                    acted = True
            return acted

        state, detail = await mailbox.wait(mail, stall, on_progress, watch=watch, watch_every=1.0)
        if state == "stalled":
            withdrawn = mail.withdraw()
            response = {"success": False, "request_id": mail.id, "mail": str(mail.folder),
                        "error": self._stall_error(mail, detail, withdrawn, before, closed, refused)}
            status.update(state="stalled", error=response["error"])
            mailbox.write_status(EXCHANGE_DIR, status)
            logger.error(response["error"])
            return response

        response = parse_response(detail)
        if isinstance(response, dict) and response.get("request_id", mail.id) != mail.id:
            response = {"success": False, "error": f"the answer in {mail.folder} is to request "
                                                   f"{response.get('request_id')}, not {mail.id}"}
        status.update(state="answered", answered=time.time())
        mailbox.write_status(EXCHANGE_DIR, status)
        response["mail"] = str(mail.folder)
        if closed:
            response["altium_boxes_closed"] = closed
        if refused:
            response["runscript_refused"] = refused
        await asyncio.sleep(ANSWER_SETTLE)
        return response

    def _stall_error(self, mail, detail, withdrawn, before, closed, refused) -> str:
        """Why the bridge went quiet, as far as can be seen from here: Altium hung, a box it shows (captured into
        the request's folder and closed when it appeared during this call, the way the runner does), or a
        script of a caller that gave up still running."""
        seconds = detail["seconds"]
        if withdrawn:
            text = (f"the bridge did not take the request within {seconds:g} s, so it was withdrawn and nothing "
                    f"ran")
        else:
            last = detail["last_record"]
            where = (f"after '{last['step']}' ({last['count']} of {last['total']}, {last['elapsed_ms'] / 1000:.0f} s "
                     f"into the run)" if last else "after taking it")
            text = (f"the bridge took the request and reported no progress for {seconds:g} s {where}; Altium may "
                    f"still be working on it, and the lock is released")
        causes = [f"Altium showed {box} during this call, closed" for box in closed]
        causes += [f"Altium refused the RunScript ({box}) and it was sent again" for box in refused]
        hung = [title for h, cls, title in x2_windows(None) if ctypes_user32().IsHungAppWindow(h)]
        if hung:
            causes.append("Windows reports Altium not responding (" + "; ".join(hung[:3]) + ")")
        for i, (h, cls, title) in enumerate(x2_windows(DIALOG_CLASSES)):
            shot = mail.file(f"dialog_{i + 1}.png")
            try:
                grab_window(h).save(shot)
            except Exception:
                shot = None
            if h in before:
                causes.append(f'a box open before this call: {cls} "{title}"' + (f" ({shot})" if shot else ""))
            else:
                ctypes_user32().PostMessageW(h, 0x0010, 0, 0)        # WM_CLOSE
                causes.append(f'Altium showed {cls} "{title}" during this call; its picture is {shot}; closed')
        if not causes and withdrawn:
            causes.append("no box and not hung: a script of a caller that gave up may still run, or the RunScript "
                          "was lost")
        return text + (". " + "; ".join(causes) if causes else "") + f". Request folder: {mail.folder}"

    @staticmethod
    def _resolve_msix_path(virtual_path: str) -> str:
        """Resolve an MSIX-virtualized path to the real filesystem path.

        When Claude Desktop is installed via MSIX (the standard .exe installer
        on modern Windows), file paths are virtualized under AppData\\Roaming\\
        but the real files live at AppData\\Local\\Packages\\Claude_*\\
        LocalCache\\Roaming\\. Child processes of the MSIX app (like Python)
        can see the virtualized paths, but external apps (like Altium) cannot.
        This resolves the path so external processes can find the files.
        """
        appdata = os.environ.get('APPDATA', '')
        if not appdata or not virtual_path.startswith(appdata):
            return virtual_path

        localappdata = os.environ.get('LOCALAPPDATA', '')
        packages_dir = os.path.join(localappdata, 'Packages')
        if not os.path.isdir(packages_dir):
            return virtual_path

        try:
            for item in os.listdir(packages_dir):
                if item.startswith('Claude_'):
                    relative = os.path.relpath(virtual_path, appdata)
                    real_path = os.path.join(packages_dir, item, 'LocalCache', 'Roaming', relative)
                    if os.path.exists(real_path):
                        logger.info(f"Resolved MSIX path: {virtual_path} -> {real_path}")
                        return real_path
        except Exception as e:
            logger.warning(f"Error resolving MSIX path: {e}")

        return virtual_path

    async def run_altium_script(self) -> bool:
        """Run the Altium bridge script"""
        if not os.path.exists(self.config.altium_exe_path):
            logger.error(f"Altium executable not found at: {self.config.altium_exe_path}")
            print(f"Error: Altium executable not found. Please check the configuration.")
            return False

        if not os.path.exists(self.config.script_path):
            logger.error(f"Script file not found at: {self.config.script_path}")
            print(f"Error: Script file not found. Please check the configuration.")
            return False

        try:
            # A copy named by its content, so Altium never runs an old unit text
            # it still holds in memory, in a folder it sees under the same path
            script_path = str(stage_bridge(Path(self.config.script_path)))

            # Command format: "X2.EXE" -RScriptingSystem:RunScript(ProjectName="path\file.PrjScr"|ProcName="ModuleName>Run")
            command = f'"{self.config.altium_exe_path}" -RScriptingSystem:RunScript(ProjectName="{script_path}"^|ProcName="Altium_API>Run")'
            
            logger.info(f"Running command: {command}")
            
            # Start the process
            process = subprocess.Popen(command, shell=True, env=altium_env())
            
            # Don't wait for completion - Altium will run the script and generate the response
            logger.info(f"Launched Altium with script, process ID: {process.pid}")
            return True
        
        except Exception as e:
            logger.error(f"Error launching Altium: {e}")
            return False

def parse_response(response_text: str):
    """The bridge's answer as a dict; a malformed one is repaired where it can be, else reported."""
    logger.info(f"Raw response (first 200 chars): {response_text[:200]}")
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON response: {e}")
        fixed_text = response_text
        if '"[' in fixed_text and ']"' in fixed_text:          # a quoted JSON array
            fixed_text = fixed_text.replace('"[', '[').replace(']"', ']')
        fixed_text = fixed_text.replace('\\"', '"')             # escaped quotes in strings
        try:
            return json.loads(fixed_text)
        except json.JSONDecodeError:
            return {"success": False, "error": f"Invalid JSON response: {e}", "raw_response": response_text[:500]}


def ctypes_user32():
    import ctypes
    return ctypes.windll.user32


def x2_windows(classes=DIALOG_CLASSES):
    """Visible top-level windows of X2.EXE, topmost first: [(hwnd, class, title)]; only those of the given
    window classes, or all with classes None."""
    found = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            cls = win32gui.GetClassName(hwnd)
            if (classes is None or cls in classes) and _window_process_name(hwnd).lower() == "x2.exe":
                found.append((hwnd, cls, win32gui.GetWindowText(hwnd)))
        return True

    try:
        win32gui.EnumWindows(cb, 0)
    except Exception:
        pass
    return found


def grab_window(hwnd) -> Image.Image:
    """A window rendered through the compositor (PrintWindow with PW_RENDERFULLCONTENT), measured in physical
    pixels: Altium is DPI-aware, and a DPI-unaware caller gets a scaled rectangle that crops the capture."""
    import ctypes
    user32 = ctypes.windll.user32
    user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
    previous_dpi = user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(-4))   # per monitor v2
    try:
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        width, height = right - left, bottom - top
        if width <= 0 or height <= 0:
            raise OSError(f"the window has no size ({width}x{height})")
        hwnd_dc = win32gui.GetWindowDC(hwnd)
        mfc_dc = win32ui.CreateDCFromHandle(hwnd_dc)
        save_dc = mfc_dc.CreateCompatibleDC()
        bitmap = win32ui.CreateBitmap()
        bitmap.CreateCompatibleBitmap(mfc_dc, width, height)
        save_dc.SelectObject(bitmap)
        try:
            if not user32.PrintWindow(hwnd, save_dc.GetSafeHdc(), PW_RENDERFULLCONTENT):
                raise OSError("PrintWindow could not render the window")
            info = bitmap.GetInfo()
            return Image.frombuffer("RGB", (info["bmWidth"], info["bmHeight"]), bitmap.GetBitmapBits(True),
                                    "raw", "BGRX", 0, 1)
        finally:
            win32gui.DeleteObject(bitmap.GetHandle())
            save_dc.DeleteDC()
            mfc_dc.DeleteDC()
            win32gui.ReleaseDC(hwnd, hwnd_dc)
    finally:
        user32.SetThreadDpiAwarenessContext(ctypes.c_void_p(previous_dpi))


async def take_script_lock(owner: str, wait: float = 60):
    """Wait up to `wait` seconds for Altium's one-script lock (script_lock.py): None when taken, else the holder."""
    deadline = time.monotonic() + wait
    while (holder := script_lock.try_take(owner)) is not None:
        if time.monotonic() >= deadline:
            script_lock.leave()                     # our wait given up: our line out of the queue
            return holder
        script_lock.waiting(owner)                  # our line in the queue, once; it goes when our turn's lock is taken
        await asyncio.sleep(0.5)
    return None


# Create a global bridge instance
altium_bridge = AltiumBridge()

@mcp.tool()
async def get_all_component_property_names(ctx: Context) -> str:
    """
    Get all available component property names (JSON keys) from all components
    
    Returns:
        str: JSON array with all unique property names
    """
    logger.info("Getting all component property names")
    
    # Execute the command in Altium to get component data
    response = await altium_bridge.execute_command(
        "get_all_component_data", 
        {}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting component data: {error_msg}")
        return json.dumps({"error": f"Failed to get component data: {error_msg}"})
    
    # Get the component data
    components_data = response.get("result", [])
    
    if not components_data:
        logger.info("No component data found")
        return json.dumps({"error": "No component data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(components_data, str):
            components_list = json.loads(components_data)
        else:
            components_list = components_data
            
        # Extract all unique property names from all components
        property_names = set()
        for component in components_list:
            property_names.update(component.keys())
        
        # Convert set to sorted list for consistent output
        property_list = sorted(list(property_names))
        
        logger.info(f"Found {len(property_list)} unique property names")
        return json.dumps(property_list, indent=2)
    except Exception as e:
        logger.error(f"Error processing component data: {e}")
        return json.dumps({"error": f"Failed to process component data: {str(e)}"})

@mcp.tool()
async def get_component_property_values(ctx: Context, property_name: str) -> str:
    """
    Get values of a specific property for all components
    
    Args:
        property_name (str): The name of the property to get values for
    
    Returns:
        str: JSON array with objects containing designator and property value
    """
    logger.info(f"Getting values for property: {property_name}")
    
    # Execute the command in Altium to get component data
    response = await altium_bridge.execute_command(
        "get_all_component_data", 
        {}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting component data: {error_msg}")
        return json.dumps({"error": f"Failed to get component data: {error_msg}"})
    
    # Get the component data
    components_data = response.get("result", [])
    
    if not components_data:
        logger.info("No component data found")
        return json.dumps({"error": "No component data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(components_data, str):
            components_list = json.loads(components_data)
        else:
            components_list = components_data
            
        # Extract the property values along with designators
        property_values = []
        for component in components_list:
            designator = component.get("designator")
            if designator and property_name in component:
                property_values.append({
                    "designator": designator,
                    "value": component.get(property_name)
                })
        
        logger.info(f"Found {len(property_values)} components with property '{property_name}'")
        return json.dumps(property_values, indent=2)
    except Exception as e:
        logger.error(f"Error processing component data: {e}")
        return json.dumps({"error": f"Failed to process component data: {str(e)}"})
    
@mcp.tool()
async def get_symbol_placement_rules(ctx: Context) -> str:
    """
    Get schematic symbol placement rules from a local configuration file
    
    Returns:
        str: JSON object with rules for placing pins on schematic symbols
    """
    logger.info("Getting symbol placement rules")
    
    # Define the rules file path in the MCP directory
    rules_file_path = MCP_DIR / "symbol_placement_rules.txt"
    
    # Check if the rules file exists
    if not rules_file_path.exists():
        logger.info("Symbol placement rules file not found, suggesting creation")
        
        # Default rules content
        default_rules = (
            "Only place pins on the left and right side of the symbol. "
            "Place power rail pins at the upper right, ground pins in the bottom left, "
            "no connect pins in the bottom right, inputs on the left, outputs on the right, "
            "and try to group other pins together by similar functionality (for example, SPI, I2C, RGMII, etc.). "
            "Always separate groups by 100mil gaps unless there is extra spacing, then space out groups equal distance from each other. "
        )
        
        # Create a helpful message for the user
        message = {
            "success": False,
            "error": f"Rules file not found at: {rules_file_path}",
            "message": f"Let the user know that they can optionally update the file {rules_file_path} with custom symbol placement rules. "
                      f"Suggested content: {default_rules}"
        }
        
        return json.dumps(message, indent=2)
    
    # Read the rules file if it exists
    try:
        with open(rules_file_path, "r") as f:
            rules_content = f.read()
        
        logger.info("Successfully read symbol placement rules file")
        
        # Return the rules with a message about how to modify them
        result = {
            "success": True,
            "message": f"Modify {rules_file_path} with custom symbol placement instructions",
            "rules": rules_content
        }
        
        return json.dumps(result, indent=2)
        
    except Exception as e:
        logger.error(f"Error reading symbol placement rules file: {e}")
        return json.dumps({
            "success": False,
            "error": f"Failed to read rules file: {str(e)}"
        }, indent=2)

@mcp.tool()
async def get_library_symbol_reference(ctx: Context) -> str:
    """
    Get the currently open symbol from a schematic library to use as reference for creating a new symbol.
    This tool should be used before creating a new symbol to understand the structure of existing symbols.
    
    Returns:
        str: JSON object with the reference symbol data including pins, their types, positions, and orientations
    """
    logger.info("Getting library symbol reference data")
    
    # Execute the command in Altium to get symbol reference data
    response = await altium_bridge.execute_command(
        "get_library_symbol_reference", 
        {}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting symbol reference: {error_msg}")
        return json.dumps({"error": f"Failed to get symbol reference: {error_msg}"})
    
    # Get the symbol reference data
    symbol_data = response.get("result", {})
    
    if not symbol_data:
        logger.info("No symbol reference data found")
        return json.dumps({"error": "No symbol reference data found or no symbol is currently selected in the library"})
    
    logger.info(f"Retrieved symbol reference data")
    return json.dumps(symbol_data, indent=2)

@mcp.tool()
async def search_library_symbol(ctx: Context, symbol_name: str, library_path: str = "") -> str:
    """
    Search for a symbol by name in a schematic library (.SchLib) and navigate to it.
    Supports partial name matching (case-insensitive). Returns all matches and navigates
    to the best match (exact match preferred, otherwise first partial match).

    This tool will automatically open the library file in Altium if a path is provided,
    so no SchLib needs to be open beforehand.

    Args:
        symbol_name (str): Name or partial name of the symbol to search for
        library_path (str): Full file path to the .SchLib file (e.g. "C:\\Libraries\\MyParts.SchLib").
                           The tool will open this file in Altium if it is not already open,
                           and close it again at the end of the call; a library open before
                           stays open, on the symbol it navigated to.
                           If empty, uses the currently open library.
                           If no library is open and no path is provided, ask the user for the file path.

    Returns:
        str: JSON object with search results including matches, navigated symbol, and full symbol list
    """
    logger.info(f"Searching for symbol: {symbol_name} in library: {library_path or '(current)'}")

    # Execute the command in Altium
    params = {"symbol_name": symbol_name}
    if library_path:
        params["library_path"] = library_path

    response = await altium_bridge.execute_command(
        "search_library_symbol",
        params
    )

    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error searching for symbol: {error_msg}")
        return json.dumps({"error": f"Failed to search for symbol: {error_msg}"})

    # Get the result data
    result = response.get("result", {})

    if not result:
        logger.info("No search results returned")
        return json.dumps({"error": "No results returned from symbol search"})

    logger.info(f"Symbol search complete. Found: {result.get('found', False)}")
    return json.dumps(result, indent=2)

@mcp.tool()
async def create_schematic_symbol(ctx: Context, symbol_name: str, description: str, pins: list, part_count: int = 1, graphics: list = None) -> str:
    r"""
    Before executing, run get_symbol_placement_rules first.

    For Altium API guidance while scripting, use the "altium-script" skill
    (ensure_altium_script_skill reports whether it is installed).

    Also look for a similar existing symbol to use as a style reference
    before drawing: search a company/library .SchLib for a comparable part
    (same category - op-amp, comparator, MCU, regulator, diode...) with
    search_library_symbol, then dump it with get_symbol_primitives and
    mirror its conventions (body style, pin lengths, pin name visibility,
    grid spacing, glyph shapes). This produces symbols consistent with the
    user's library. If no symbol library is available to reference, that is
    fine - skip this step and proceed with the defaults below; do not treat
    a missing reference as an error.

    Create a new schematic symbol in the current library with the specified pins
    Instructions: pins should be grouped together via function and only placed on
                  the left and right side in 100 mil increments

    Pin name inversion/overbar: To show an overbar on a pin name (for active-low signals),
                  place a backslash after EACH character that should be overbarred.
                  Examples: R\E\S\E\T\ renders as RESET with overbar.
                           C\S\/A0 renders as CS with overbar followed by /A0 without overbar.
                  Do NOT use ~{...} or other notation — only the backslash-per-character format works in Altium.

    Args:
        symbol_name (str): Name of the symbol to create
        description (str): Description of the schematic symbol
        pins (list): List of pin data in format
                    "pin_number|pin_name|pin_type|pin_orientation|x|y[|owner_part_id[|length[|show_name[|show_designator]]]]"
                    Pin types: eElectricHiZ, eElectricInput, eElectricIO, eElectricOpenCollector,
                               eElectricOpenEmitter, eElectricOutput, eElectricPassive, eElectricPower
                    Pin orientations: eRotate0 (right), eRotate90 (down), eRotate180 (left), eRotate270 (up)
                    X,Y coordinates in mils
                    owner_part_id (optional): Part number the pin belongs to (1-based).
                               Use 0 for pins shared across all parts (e.g. power/GND).
                               Defaults to 1 if omitted. Only needed for multi-part symbols.
                    length (optional): pin length in mils (default 300)
                    show_name / show_designator (optional): 1 or 0 to show/hide
                               the pin name / number (e.g. op-amp pins often hide names)
        part_count (int): Number of parts in the symbol (default 1).
                         Use >1 for multi-part symbols like quad op-amps or hex buffers.
        graphics (list, optional): Explicit body graphics. When given, the
                    default auto-sized body rectangle is NOT drawn - the
                    graphics fully define the symbol body (triangles for
                    op-amps, diode glyphs, etc.). Entry formats (coordinates
                    in mils; part = owner part id, 1-based; width 0-3 =
                    zero/small/medium/large; solid 1 or 0):
                    - "line|part|width|x1|y1|x2|y2"
                    - "polyline|part|width|x1|y1|x2|y2|..." (any number of vertices)
                    - "polygon|part|width|solid|x1|y1|x2|y2|..." (closed/filled shape)
                    - "rectangle|part|width|solid|x1|y1|x2|y2"
                    - "arc|part|width|cx|cy|radius|start_angle|end_angle" (degrees CCW from 3 o'clock)
                    - "elliptical_arc|part|width|cx|cy|radius|secondary_radius|start_angle|end_angle"
                    - "ellipse|part|width|solid|cx|cy|radius|secondary_radius"
                    - "label|part|x|y|text" (free text annotation)
                    Tip: to reproduce an existing symbol's style, dump it first
                    with get_symbol_primitives and mirror its primitives.

    Returns:
        str: JSON object with the result of the component creation
    """
    logger.info(f"Creating schematic symbol: {symbol_name} with {len(pins)} pins, {part_count} part(s)")

    params = {
        "symbol_name": symbol_name,
        "description": description,
        "part_count": part_count,
        "pins": pins
    }
    if graphics:
        params["graphics"] = graphics

    # Execute the command in Altium to create the symbol
    response = await altium_bridge.execute_command(
        "create_schematic_symbol",
        params
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error creating symbol: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to create symbol: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})
    
    logger.info(f"Symbol {symbol_name} created successfully with {len(pins)} pins")
    return json.dumps(result, indent=2)

@mcp.tool()
async def get_schematic_data(ctx: Context, cmp_designators: list) -> str:
    """
    Get schematic data for components in Altium
    
    Args:
        cmp_designators (list): List of designators of the components (e.g., ["R1", "C5", "U3"])
    
    Returns:
        str: JSON object with schematic component data for requested designators
    """
    logger.info(f"Getting schematic data for components: {cmp_designators}")
    
    # Execute the command in Altium to get schematic data
    response = await altium_bridge.execute_command(
        "get_schematic_data",
        {}  # No parameters needed for this command in the Altium script
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting schematic data: {error_msg}")
        return json.dumps({"error": f"Failed to get schematic data: {error_msg}"})
    
    # Get the schematic data
    schematic_data = response.get("result", [])
    
    if not schematic_data:
        logger.info("No schematic data found")
        return json.dumps({"error": "No schematic data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(schematic_data, str):
            schematic_list = json.loads(schematic_data)
        else:
            schematic_list = schematic_data
        
        # Filter components by designator
        components = []
        missing_designators = []
        
        for designator in cmp_designators:
            found = False
            for component in schematic_list:
                if component.get("designator") == designator:
                    components.append(component)
                    found = True
                    break
            
            if not found:
                missing_designators.append(designator)
        
        result = {
            "components": components,
        }
        
        if missing_designators:
            result["missing_designators"] = missing_designators
            logger.info(f"Some designators not found in schematic data: {missing_designators}")
        
        logger.info(f"Found schematic data for {len(components)} components")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error processing schematic data: {e}")
        return json.dumps({"error": f"Failed to process schematic data: {str(e)}"})
    
@mcp.tool()
async def get_pcb_layers(ctx: Context) -> str:
    """
    Get detailed information about all layers in the current Altium PCB
    
    Returns:
        str: JSON object with detailed layer information including copper layers, 
             mechanical layers, and special layers with their properties
    """
    logger.info("Getting detailed PCB layer information")
    
    # Execute the command in Altium to get all layers data
    response = await altium_bridge.execute_command(
        "get_pcb_layers",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting PCB layers: {error_msg}")
        return json.dumps({"error": f"Failed to get PCB layers: {error_msg}"})
    
    # Get the layers data
    layers_data = response.get("result", [])
    
    if not layers_data:
        logger.info("No PCB layers found")
        return json.dumps({"message": "No PCB layers found in the current document"})
    
    logger.info(f"Retrieved PCB layers data")
    return json.dumps(layers_data, indent=2)

@mcp.tool()
async def set_pcb_layer_visibility(ctx: Context, layer_names: list, visible: bool) -> str:
    """
    Set visibility for specified PCB layers
    
    Args:
        layer_names (list): List of layer names to modify (e.g., ["Top Layer", "Bottom Layer", "Mechanical 1"])
        visible (bool): Whether to show (True) or hide (False) the specified layers
        
    Returns:
        str: JSON object with the result of the operation
    """
    logger.info(f"Setting layers visibility: {layer_names} to {visible}")
    
    # Execute the command in Altium to set layer visibility
    response = await altium_bridge.execute_command(
        "set_pcb_layer_visibility",
        {
            "layer_names": layer_names,
            "visible": visible
        }
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error setting layer visibility: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to set layer visibility: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})
    
    logger.info(f"Layer visibility set successfully")
    return json.dumps(result, indent=2)

@mcp.tool()
async def get_component_data(ctx: Context, cmp_designators: list) -> str:
    """
    Get all data for components in Altium
    
    Args:
        cmp_designators (list): List of designators of the components (e.g., ["R1", "C5", "U3"])
    
    Returns:
        str: JSON object with all component data for requested designators
    """
    logger.info(f"Getting data for components: {cmp_designators}")
    
    # Execute the command in Altium to get all component data
    response = await altium_bridge.execute_command(
        "get_all_component_data",
        {}  # No parameters needed for this command in the Altium script
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting component data: {error_msg}")
        return json.dumps({"error": f"Failed to get component data: {error_msg}"})
    
    # Get the component data
    component_data = response.get("result", [])
    
    if not component_data:
        logger.info("No component data found")
        return json.dumps({"error": "No component data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(component_data, str):
            component_list = json.loads(component_data)
        else:
            component_list = component_data
        
        # Filter components by designator
        components = []
        missing_designators = []
        
        for designator in cmp_designators:
            found = False
            for component in component_list:
                if component.get("designator") == designator:
                    components.append(component)
                    found = True
                    break
            
            if not found:
                missing_designators.append(designator)
        
        result = {
            "components": components,
        }
        
        if missing_designators:
            result["missing_designators"] = missing_designators
            logger.info(f"Some designators not found: {missing_designators}")
        
        logger.info(f"Found data for {len(components)} components")
        return json.dumps(result, indent=2)
    except Exception as e:
        logger.error(f"Error processing component data: {e}")
        return json.dumps({"error": f"Failed to process component data: {str(e)}"})

@mcp.tool()
async def get_selected_components_coordinates(ctx: Context) -> str:
    """
    Get coordinates and positioning information for selected components in Altium layout
    
    Returns:
        str: JSON array with positioning data (designator, x, y, rotation, width, height)
    """
    logger.info("Getting coordinates for selected components")
    
    # Execute the command in Altium to get selected components coordinates
    response = await altium_bridge.execute_command(
        "get_selected_components_coordinates",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting selected components coordinates: {error_msg}")
        return json.dumps({"error": f"Failed to get selected components coordinates: {error_msg}"})
    
    # Get the components coordinates data
    components_coords = response.get("result", [])
    
    if not components_coords:
        logger.info("No selected components found")
        return json.dumps({"message": "No components are currently selected in the layout"})
    
    logger.info(f"Retrieved positioning data for selected components")
    return json.dumps(components_coords, indent=2)

@mcp.tool()
async def get_all_designators(ctx: Context) -> str:
    """
    Get all component designators from the current Altium board
    
    Returns:
        str: JSON array of all component designators on the current board
    """
    logger.info("Getting all component designators")
    
    # Execute the command in Altium to get all component data
    response = await altium_bridge.execute_command(
        "get_all_component_data",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting component data: {error_msg}")
        return json.dumps({"error": f"Failed to get component data: {error_msg}"})
    
    # Get the component data
    component_data = response.get("result", [])
    
    if not component_data:
        logger.info("No component data found")
        return json.dumps({"error": "No component data found"})
    
    try:
        # Parse the data if it's a string
        if isinstance(component_data, str):
            component_list = json.loads(component_data)
        else:
            component_list = component_data
        
        # Extract designators
        designators = [comp.get("designator") for comp in component_list if "designator" in comp]
        
        logger.info(f"Found {len(designators)} designators")
        return json.dumps(designators)
    except Exception as e:
        logger.error(f"Error processing component data: {e}")
        return json.dumps({"error": f"Failed to process component data: {str(e)}"})

@mcp.tool()
async def get_component_pins(ctx: Context, cmp_designators: list) -> str:
    """
    Get pin data for components in Altium

    Args:
        cmp_designators (list): List of designators of the components (e.g., ["R1", "C5", "U3"])

    Returns:
        str: JSON array, one entry per component with its placement info
             (x/y in mils relative to the board origin, rotation in degrees
             counterclockwise, layer) and a "pins" list. Per pin:
             - x/y: absolute pad position (mils, relative to board origin)
             - dx/dy: pad offset from the component origin in the footprint's
               rotation-0 frame. To predict a pad position for a planned
               placement: on the bottom negate dy (Altium mirrors a bottom part
               top to bottom), rotate (dx, dy) counterclockwise by Altium's
               rotation (the component's "rotation" here), then add the planned
               component x/y. In place_components' convention (Altium's rotation
               - 180 on the bottom) that is: negate dx, rotate by the convention's
               angle (pad 1 read back on a scratch board, 2026-10-23).
             - rotation: the pad's own rotation (NOT the component rotation)
             - net, layer, width, height, shape
    """
    logger.info(f"Getting pin data for components: {cmp_designators}")
    
    # Execute the command in Altium to get pin data
    response = await altium_bridge.execute_command(
        "get_component_pins",
        {"designators": cmp_designators}  # Pass the list of designators
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting pin data: {error_msg}")
        return json.dumps({"error": f"Failed to get pin data: {error_msg}"})
    
    # Get the components pins data
    pins_data = response.get("result", [])
    
    if not pins_data:
        logger.info(f"No pin data found for designators: {cmp_designators}")
        return json.dumps({"message": "No pin data found for the specified components"})
    
    logger.info(f"Retrieved pin data for components")
    return json.dumps(pins_data, indent=2)

SANDBOX_DIR = MCP_DIR / "SandboxScript"
SANDBOX_PAS = SANDBOX_DIR / "Sandbox.pas"
SANDBOX_PRJ = SANDBOX_DIR / "Sandbox.PrjScr"
SANDBOX_LOG = EXCHANGE_DIR / "sandbox_log.txt"
SANDBOX_RESULT = EXCHANGE_DIR / "sandbox_result.json"
SANDBOX_BEGIN = "// === BEGIN EXPERIMENT"
SANDBOX_END = "// === END EXPERIMENT"


def _window_process_name(hwnd) -> str:
    """Executable file name of the process that owns a window ('' if unknown)."""
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return ""
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    handle = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(len(buf))
        if kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            return os.path.basename(buf.value)
        return ""
    finally:
        kernel32.CloseHandle(handle)


def _dismiss_altium_dialogs():
    """Close Altium modal popups that would otherwise block a script run.

    Altium uses two kinds: Win32 task dialogs (#32770) and Delphi TMessageForm
    error/warning boxes. It also shows "Select Item To Run"
    (TForm_RunScriptParameters) when it cannot find the script project; that
    one stays open and blocks every later script run. Only windows owned by
    the Altium process (X2.EXE) are touched, so dialogs of other applications
    are never closed.

    Returns (handle, class name, title) of the windows that were closed, so
    the caller can tell the user (a closed dialog may have been one the user
    had open).
    """
    try:
        import ctypes
        from ctypes import wintypes
    except ImportError:
        return []
    user32 = ctypes.windll.user32
    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, lparam):
        if not user32.IsWindowVisible(hwnd):
            return True
        cls = ctypes.create_unicode_buffer(64)
        user32.GetClassNameW(hwnd, cls, 64)
        if cls.value not in ("#32770", "TMessageForm", "TForm_RunScriptParameters"):
            return True
        n = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(n + 1)
        user32.GetWindowTextW(hwnd, buf, n + 1)
        title = buf.value
        if cls.value == "TMessageForm" and title not in ("Error", "Warning", "Information", "Confirm"):
            return True
        if _window_process_name(hwnd).lower() != "x2.exe":
            return True
        found.append((hwnd, cls.value, title))
        return True

    user32.EnumWindows(cb, 0)
    for h, _, _ in found:
        user32.PostMessageW(h, 0x0010, 0, 0)
    return found


async def _run_altium_script(ctx: Context, script: str, timeout_seconds: int = 120) -> str:
    """
    Run a DelphiScript snippet inside an isolated Altium sandbox and report
    what happened, step by step.

    Use this to develop and verify Altium API code before relying on it.
    Altium has no headless test mode and its failure modes are hostile: a
    runtime error leaves the script PAUSED IN THE DEBUGGER with no dialog,
    after which every later script run silently does nothing until the
    debugger is stopped (Ctrl+F3) or Altium is restarted. This tool detects
    that state and reports exactly which statement died.

    The script runs in a SEPARATE script project, so a crash here can never
    break the other MCP tools. The project is closed at the end of every run
    that finishes (the last steps say so), so the next run reads its unit
    fresh from disk and the operator's workspace keeps no sandbox.

    Writing the script:
    - Call SandboxLog('...') before each risky statement. The log is flushed
      after every call, so the last logged line identifies what failed.
    - Assign findings to the string variable ResultText - it is returned.
    - DelphiScript has NO inline variable declarations. Reuse the provided
      scratch variables: S1..S3 (String), I1..I3 and B1 (Integer),
      Obj1..Obj5 (IDispatch), List1 (an empty TStringList the sandbox frees
      after the script: never free it), IntMan, DbDoc.
    - try/except does NOT catch runtime errors such as bad conversions or
      invalid API calls, so it cannot be relied on to keep a script alive.
    - The sandbox is standalone: helpers and constants from the production
      units (TrimJSON, AddJSONProperty, ...) are NOT available; REPLACEALL is.
    - Never register objects into a library document and never write to shared
      or network library paths. Verify the target document kind first
      (ObjectID 32 = schematic, 33 = symbol library).

    For API guidance - interfaces, object models, worked examples - use the
    "altium-script" skill. ensure_altium_script_skill reports whether that
    skill is installed and can install it.

    Args:
        script (str): DelphiScript statements to execute (body only).
        timeout_seconds (int): How long to wait for completion (default 120).

    Returns:
        str: JSON with success, the step log, the script's ResultText, and on
             failure the last step reached plus whether Altium's script
             executor is now wedged and needs recovery.
    """
    logger.info(f"run_altium_script: {len(script.splitlines())} lines")

    if not SANDBOX_PAS.exists() or not SANDBOX_PRJ.exists():
        return json.dumps({"success": False,
                           "error": f"sandbox project missing at {SANDBOX_DIR}"})

    try:
        src = SANDBOX_PAS.read_bytes().decode("mbcs")
        pre, rest = src.split(SANDBOX_BEGIN, 1)
        marker_line, rest = rest.split("\n", 1)
        _, post = rest.split(SANDBOX_END, 1)
        body = "\n".join("        " + ln if ln.strip() else ln
                          for ln in script.strip("\n").splitlines())
        # Altium reads a script without a byte-order mark in the ANSI code page
        SANDBOX_PAS.write_bytes(ansi_script(
            pre + SANDBOX_BEGIN + marker_line + "\n" + body + "\n        " + SANDBOX_END + post))
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": f"could not inject script: {e}"})

    for f in (SANDBOX_LOG, SANDBOX_RESULT):
        if f.exists():
            try:
                f.unlink()
            except OSError:
                pass

    # Altium runs outside Claude Desktop's MSIX package and cannot see the
    # virtualized AppData path this process sees (issue #19)
    sandbox_prj = AltiumBridge._resolve_msix_path(str(SANDBOX_PRJ))
    cmd = (f'"{altium_bridge.config.altium_exe_path}" -RScriptingSystem:RunScript('
           f'ProjectName="{sandbox_prj}"^|ProcName="Sandbox>Run")')
    subprocess.Popen(cmd, shell=True, env=altium_env())

    start = time.time()
    # Keyed by window: a dialog that ignores WM_CLOSE (Yes/No boxes do) is
    # found again on every pass and must be reported once
    dismissed = {}
    while not SANDBOX_RESULT.exists() and time.time() - start < timeout_seconds:
        await asyncio.sleep(0.5)
        if time.time() - start > 6:
            for h, c, t in _dismiss_altium_dialogs():
                dismissed[h] = (c, t)

    dialogs = list(dismissed.values())
    titles = [t for _, t in dialogs]
    steps = []
    if SANDBOX_LOG.exists():
        steps = read_altium_text(SANDBOX_LOG).splitlines()

    if SANDBOX_RESULT.exists():
        result_text = read_altium_text(SANDBOX_RESULT).strip()
        return json.dumps({"success": True, "result": result_text, "steps": steps,
                           "dialogs_dismissed": len(dialogs), "dismissed_dialog_titles": titles}, indent=2)

    if steps:
        return json.dumps({
            "success": False,
            "error": "script started but did not finish",
            "last_step_reached": steps[-1],
            "diagnosis": "The statement AFTER the last step is what crashed or paused the script.",
            "executor_wedged": True,
            "recovery": "Altium's script executor is now blocked. Recover by running this "
                        "shell command (sends the debugger Stop process to the running "
                        "Altium): \"<altium_exe>\" -REditScript:Stop  -- then retry.",
            "steps": steps,
            "dialogs_dismissed": len(dialogs), "dismissed_dialog_titles": titles}, indent=2)

    if any(c == "TForm_RunScriptParameters" for c, _ in dialogs):
        return json.dumps({
            "success": False,
            "error": "Altium could not find the sandbox script project",
            "diagnosis": f"Altium was asked to run {sandbox_prj} and showed 'Select Item To "
                         "Run' instead, so it cannot open that path. The script executor is "
                         "not wedged.",
            "executor_wedged": False,
            "dialogs_dismissed": len(dialogs), "dismissed_dialog_titles": titles}, indent=2)

    return json.dumps({
        "success": False,
        "error": "script never started",
        "diagnosis": "Usually a COMPILE error in the script, or a previously paused "
                     "script blocking execution.",
        "executor_wedged": True,
        "recovery": "A previously paused script may be blocking execution. Recover by "
                    "running this shell command: \"<altium_exe>\" -REditScript:Stop  "
                    "-- then retry. If it still fails, the script itself has a COMPILE error.",
        "dialogs_dismissed": len(dialogs), "dismissed_dialog_titles": titles}, indent=2)


async def run_altium_script(ctx: Context, script: str, timeout_seconds: int = 120) -> str:
    holder = await take_script_lock("altium-mcp run_altium_script")
    if holder is not None:
        return json.dumps({"success": False, "error": script_lock.describe(holder)})
    try:
        return await _run_altium_script(ctx, script, timeout_seconds)
    finally:
        script_lock.release()


run_altium_script.__doc__ = _run_altium_script.__doc__  # the tool's description
mcp.tool()(run_altium_script)


@mcp.tool()
async def ensure_altium_script_skill(ctx: Context, install: bool = False) -> str:
    """
    Check whether the "altium-script" skill is installed, and optionally
    install it.

    That skill documents the Altium DelphiScript API - interfaces, object
    models, worked examples, conventions, and how to discover undocumented
    processes - and is the reference to consult before writing scripts for
    run_altium_script or debugging Altium API calls.

    Source: https://github.com/coffeenmusic/altium-scripts-skill

    Installing writes into the user's skills directory, so it only happens when
    install=True is passed explicitly. Skills load at client startup, so a
    newly installed skill becomes available after restarting the client.

    Args:
        install (bool): Install the skill if missing (requires git on PATH).

    Returns:
        str: JSON with installed (bool), the path checked, and the next step.
    """
    skill_dir = Path.home() / ".claude" / "skills" / "altium-script"
    repo = "https://github.com/coffeenmusic/altium-scripts-skill"

    if (skill_dir / "SKILL.md").exists():
        return json.dumps({"installed": True, "path": str(skill_dir),
                           "note": "Use the altium-script skill for API guidance."},
                          indent=2)

    if not install:
        return json.dumps({
            "installed": False,
            "path": str(skill_dir),
            "source": repo,
            "next_step": "Call again with install=true to clone it, or install manually "
                         "into the path above."}, indent=2)

    try:
        skill_dir.parent.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(["git", "clone", "--depth", "1", repo, str(skill_dir)],
                              capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            return json.dumps({
                "installed": False, "path": str(skill_dir),
                "error": (proc.stderr or proc.stdout)[:400],
                "hint": f"Requires git on PATH; otherwise download {repo} and extract "
                        "to the path above."}, indent=2)
        return json.dumps({"installed": True, "path": str(skill_dir), "source": repo,
                           "next_step": "Restart the client so the skill is loaded."},
                          indent=2)
    except Exception as e:
        return json.dumps({"installed": False, "path": str(skill_dir),
                           "error": str(e)[:300]}, indent=2)


@mcp.tool()
async def get_footprint_primitives(ctx: Context, library_path: str = "", footprint_name: str = "") -> str:
    """
    Read the primitives of footprints in a PCB library (.PcbLib).

    Modes:
    - footprint_name omitted: inventory of every footprint with per-type
      primitive counts (pads, tracks, arcs, fills, texts, regions, vias,
      component_bodies)
    - footprint_name given (exact, case-insensitive): full geometry dump -
      pads (position, rotation, layer, sizes/shape per stack, hole size/
      type/width/rotation, plating), tracks, arcs, fills, texts, regions
      (outline vertices). Coordinates in mils; shapes and hole types as raw
      Altium enum ints; layers as names. 3D component bodies are models,
      not 2D primitives, and are excluded.
    - footprint_name "*": full dump of every footprint

    Use as the reference when recreating or validating footprints, and to
    survey what a library requires.

    Args:
        library_path (str, optional): Full path to the .PcbLib. Omit to use
            the currently focused PCB library (an already-open library is
            only focused, never reloaded, and stays open; one the call opens is
            closed again at its end).
        footprint_name (str, optional): Exact footprint name, or "*".

    Returns:
        str: JSON - inventory: {library_name, footprint_count, footprints:
             [{name, description, <type counts>}]}; dump: primitives list
             per footprint
    """
    logger.info(f"Getting footprint primitives (library={library_path}, footprint={footprint_name})")

    response = await altium_bridge.execute_command(
        "get_footprint_primitives",
        {"library_path": library_path, "footprint_name": footprint_name}
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        return json.dumps({"success": False, "error": f"Failed to get footprint primitives: {error_msg}"})

    result = response.get("result", {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

@mcp.tool()
async def create_footprints_batch(ctx: Context, spec_file: str) -> str:
    """
    Create many PCB footprints in a single Altium script run.

    The batch equivalent of create_pcb_footprint with far broader coverage:
    through-hole and SMD pads (full pad stack, holes, slots, plating,
    rotation), tracks, arcs, fills, texts, and regions on any layer.
    Verified by exact round-trips of complete production footprint
    libraries. Prefer this for bulk imports/migrations; use
    get_footprint_primitives on an existing footprint to learn the exact
    field conventions.

    Args:
        spec_file (str): Path to a plain-text spec file, one record per line
            (coords in mils, layers as names, shapes/hole types as raw
            Altium enum ints, booleans as 1/0):
            FPLIB|<path to .PcbLib>   (optional first line: opens/focuses)
            FOOTPRINT|<name>|<description>
            PAD|name|x|y|rot|layer|plated|hole_size|hole_type|hole_width|hole_rot|top_x|top_y|top_shape[|corner_pct[|mode|mid_x|mid_y|mid_shape|bot_x|bot_y|bot_shape]]
            TRACK|x1|y1|x2|y2|width|layer
            ARC|cx|cy|radius|start_angle|end_angle|width|layer
            FILL|x1|y1|x2|y2|rotation|layer
            TEXT|x|y|size|width|rotation|layer|mirror|ttf|text
            REGION|layer|kind|x1|y1|x2|y2|...

    Returns:
        str: JSON with created count, primitive_errors, failed names
    """
    logger.info(f"Creating footprints batch from {spec_file}")

    response = await altium_bridge.execute_command(
        "create_footprints_batch",
        {"spec_file": spec_file}
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        return json.dumps({"success": False, "error": f"Failed batch footprint creation: {error_msg}"})

    result = response.get("result", {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

def circuit_spec(parts: list, wires: list = None, junctions: list = None, net_labels: list = None,
                 power_ports: list = None, notes: list = None, save_path: str = None) -> list:
    """build_schematic's arguments as the bridge's pipe-delimited spec lines; ValueError names what is wrong."""
    lines = []
    if save_path is not None:
        target = Path(save_path)
        if not target.is_absolute() or target.suffix.lower() != ".schdoc":
            raise ValueError(f"save_path must be the full path of a .SchDoc: {save_path}")
        if not target.parent.is_dir():
            raise ValueError(f"save_path's folder does not exist: {target.parent}")
        if target.exists():
            raise ValueError(f"save_path exists; build_schematic writes a new sheet only: {save_path}")
        lines.append(f"SAVE|{target}")
    for p in parts:
        for key in ("designator", "symbol_library", "symbol", "x", "y"):
            if key not in p:
                raise ValueError(f"part missing required key '{key}': {p}")
        lines.append("PART|{}|{}|{}|{}|{}|{}|{}|{}".format(
            p["designator"], p["symbol_library"], p["symbol"],
            p.get("design_item_id", ""), int(p["x"]), int(p["y"]),
            int(p.get("orientation", 0)), 1 if p.get("mirror") else 0))
        if p.get("comment"):
            lines.append(f"COMMENT|{p['comment']}")
        if p.get("footprint"):
            lines.append(f"FOOTPRINT|{p['footprint']}")
        if p.get("description"):
            lines.append(f"DESCRIPTION|{p['description']}")
        for name, val in (p.get("parameters") or {}).items():
            lines.append(f"PARAM|{name}|{val}")

    # The bridge can only read .SchLib files, and asked to open a path that
    # does not exist Altium creates an empty library of that name instead -
    # which used to give an empty sheet reported as success (issue #19)
    for lib in sorted({str(p["symbol_library"]) for p in parts}):
        if not lib.lower().endswith(".schlib") or not os.path.isfile(lib):
            raise ValueError("symbol_library must be the full path of an existing "
                             ".SchLib file (integrated libraries such as .IntLib "
                             f"are not supported): {lib}")

    for route in (wires or []):
        if len(route) < 4 or len(route) % 2:
            raise ValueError(f"wire needs an even count of >=4 coords: {route}")
        lines.append("WIRE|" + "|".join(str(int(v)) for v in route))
    for j in (junctions or []):
        lines.append(f"JUNCTION|{int(j['x'])}|{int(j['y'])}")
    for n in (net_labels or []):
        lines.append("NETLABEL|{}|{}|{}|{}".format(
            int(n["x"]), int(n["y"]), int(n.get("orientation", 0)), n["text"]))
    for pw in (power_ports or []):
        lines.append("POWER|{}|{}|{}|{}|{}|{}".format(
            int(pw["x"]), int(pw["y"]), int(pw.get("orientation", 3)),
            int(pw.get("style", 5)), pw["text"],
            1 if pw.get("show_net_name") else 0))
    for nt in (notes or []):
        lines.append(f"NOTE|{int(nt['x'])}|{int(nt['y'])}|{nt['text']}")
    return lines


@mcp.tool()
async def build_schematic(ctx: Context, parts: list, wires: list = None,
                          junctions: list = None, net_labels: list = None,
                          power_ports: list = None, notes: list = None,
                          save_path: str = None) -> str:
    """
    Build a wired schematic on a NEW sheet from a circuit description.

    READ dev/SCHEMATIC_CONVENTIONS.md BEFORE CALLING. This tool places exactly
    what it is given; it does not lay out or correct spacing. The conventions
    file records the drafting rules that make the result readable, each one
    learned by getting it wrong. The ones that most often produce a plausible
    but wrong sheet:

      * A pin's connection point is NOT Pin.Location - it is PinLength further
        along the pin. Do not guess coordinates. Call with parts only first,
        read the returned pin_map, then call again with wires routed from those
        measured coordinates.
      * Never run a riser in a pin's connection column; it drives through every
        pin sharing that column.
      * Give a shunt part one grid of wire between its pin and the node it taps,
        rather than putting the junction on the pin.
      * A net label that does not TOUCH its wire names nothing.
      * A shunt part occupies the band below its rail. Do not route another net
        through that band - the wire will pass through the symbol body.
      * A ground port's "GND" label is always drawn (ShowNetName cannot hide it)
        and occupies ~300 mil below the port. Keep wires out of that band.

    Args:
        parts: component dicts. Required keys: designator, symbol_library
            (full path of a .SchLib file; .IntLib is not supported),
            symbol, x, y (mils). Optional: design_item_id, orientation (0-3),
            mirror (0/1), comment, description, footprint,
            parameters ({name: value}).
        wires: each a flat list of alternating x,y in mils, e.g.
            [3900, 3000, 5300, 3000, 5300, 4900] - two segments, three points.
        junctions: [{"x":..,"y":..}] - only where 3+ branches actually meet.
        net_labels: [{"x":..,"y":..,"orientation":0,"text":"LED+"}] - the
            coordinate must lie on a wire.
        power_ports: [{"x":..,"y":..,"orientation":3,"style":5,"text":"GND",
            "show_net_name":false}]. Harvest style/orientation from an existing
            sheet rather than guessing: 5 = digital ground, 2 = supply bar.
        notes: [{"x":..,"y":..,"text":".."}] free text.
        save_path: full path of a .SchDoc that does not exist yet, in an
            existing folder. The sheet is saved there and closed; without it
            the sheet stays open and unsaved. Either way it is kept out of
            the focused project: a project that lists the path takes it in
            when opened. Every .SchLib the call opens is closed again at its
            end; one open before stays open.

    Returns:
        JSON with the created sheet name (its path once saved), counts, and
        the pin map - every placed pin's true connection point, for routing a
        follow-up call.
    """
    logger.info(f"Building schematic: {len(parts)} parts")
    try:
        lines = circuit_spec(parts, wires, junctions, net_labels, power_ports, notes, save_path)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})

    # Part descriptions carry characters that are not plain ASCII: the spec goes as UTF-8 with a byte-order mark
    response = await altium_bridge.execute_command("build_circuit", {},
                                                   files={"circuit_spec.txt": "\n".join(lines) + "\n"}, ctx=ctx)
    if not response.get("success", False):
        return json.dumps({"success": False,
                           "error": response.get("error", "unknown error")})

    result = response.get("result", {})
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return result

    # Hand back the measured pin map so the caller can route a second pass
    pin_map = {}
    pm = Path(response.get("mail", "")) / "pin_map.txt"
    if response.get("mail") and pm.is_file():
        for line in read_altium_text(pm).splitlines():
            f = line.strip().split("|")
            if len(f) == 5 and f[0] == "PIN":
                pin_map.setdefault(f[1], {})[f[2]] = [int(f[3]), int(f[4])]
    result["pin_map"] = pin_map
    result["pin_map_note"] = ("Absolute electrical connection points. Route "
                              "wires from these, not from predicted offsets.")
    return json.dumps(result, indent=2)

@mcp.tool()
async def create_symbols_batch(ctx: Context, spec_file: str) -> str:
    """
    Create many schematic symbols in a single Altium script run.

    Use instead of repeated create_schematic_symbol calls when creating
    more than a handful of symbols (bulk library imports/migrations): one
    script launch instead of one per symbol, and pipe-delimited plain text
    instead of JSON so field text (commas, brackets, spaces) is preserved
    exactly. Verified by exact round-trips of a complete 284-symbol
    production library.

    Args:
        spec_file (str): Path to a plain-text spec file, one record per line:
            LIBRARY|<path to .SchLib>   (optional first line: opens/focuses;
                                         an already-open library is only
                                         focused, never reloaded)
            SYMBOL|<name>|<description>|<part_count>
            PIN|<same pipe fields as create_schematic_symbol pins>
            GRAPHIC|<same entry format as create_schematic_symbol graphics>
            Each SYMBOL line starts a new symbol; PIN/GRAPHIC lines belong
            to the most recent SYMBOL.

    Returns:
        str: JSON object with created count and a failed name list
    """
    logger.info(f"Creating symbols batch from {spec_file}")

    response = await altium_bridge.execute_command(
        "create_symbols_batch",
        {"spec_file": spec_file}
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error in batch symbol creation: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed batch creation: {error_msg}"})

    result = response.get("result", {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

@mcp.tool()
async def get_symbol_primitives(ctx: Context, library_path: str = "", symbol_name: str = "") -> str:
    """
    Read the graphic primitives of symbols in a schematic library (.SchLib).

    Two modes:
    - symbol_name omitted: inventory of every symbol in the library with
      per-type primitive counts (pins, rectangles, lines, polylines,
      polygons, arcs, ellipses, beziers, labels, ...). Use this to survey
      what drawing features a library's symbols require.
    - symbol_name given (exact match, case-insensitive): full geometry dump
      of that symbol - every primitive with coordinates in mils, plus pin
      details (number, name, electrical type, orientation, length,
      owner_part_id). Use this as the reference/spec when recreating or
      validating a symbol.

    Args:
        library_path (str, optional): Full path to the .SchLib file. A library
            the call opens is closed again at its end; one open before stays
            open and is only focused. Omit to use the schematic library
            currently focused in Altium.
        symbol_name (str, optional): Exact symbol (LibReference) name to dump.

    Returns:
        str: JSON object - inventory mode: {library_name, symbol_count,
             symbols: [{name, description, part_count, <type counts>}]};
             dump mode: {library_name, symbol_name, description, part_count,
             primitives: [...]}
    """
    logger.info(f"Getting symbol primitives (library={library_path}, symbol={symbol_name})")

    response = await altium_bridge.execute_command(
        "get_symbol_primitives",
        {"library_path": library_path, "symbol_name": symbol_name}
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting symbol primitives: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to get symbol primitives: {error_msg}"})

    result = response.get("result", {})
    return json.dumps(result, indent=2) if not isinstance(result, str) else result

@mcp.tool()
async def get_all_nets(ctx: Context) -> str:
    """
    Return every unique net name in the active PCB document.

    Returns
    -------
    str :
        A JSON array of net names, e.g. ["GND", "VCC33", "USB_D+", ...]
    """
    logger.info("Getting all nets")

    response = await altium_bridge.execute_command("get_all_nets", {})

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting nets: {error_msg}")
        return json.dumps({"error": f"Failed to get nets: {error_msg}"})

    # Result is already a JSON‑serialisable Python list
    return json.dumps(response.get("result", []), indent=2)

@mcp.tool()
async def create_net_class(ctx: Context, class_name: str, net_names: list) -> str:
    """
    Create a new net class and add specified nets to it
    
    Args:
        class_name (str): Name of the net class to create or modify
        net_names (list): List of net names to add to the class
    
    Returns:
        str: JSON object with the result of the operation
    """
    logger.info(f"Creating net class '{class_name}' with {len(net_names)} nets")
    
    # Execute the command in Altium to create the net class
    response = await altium_bridge.execute_command(
        "create_net_class",
        {
            "class_name": class_name,
            "net_names": net_names
        }
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error creating net class: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to create net class: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})
    
    logger.info(f"Net class '{class_name}' created/modified successfully")
    return json.dumps(result, indent=2)
    
@mcp.tool()
async def set_component_position(ctx: Context, cmp_designator: str, x: float, y: float, rotation: float = -1) -> str:
    """
    Set a component's absolute position in the PCB layout
    
    Args:
        cmp_designator (str): Designator of the component to position (e.g., "R1", "C5", "U3")
        x (float): Absolute X position in mils
        y (float): Absolute Y position in mils
        rotation (float): Rotation angle in degrees (0-360), use -1 to keep current rotation
    
    Returns:
        str: JSON object with the result of the position operation
    """
    logger.info(f"Setting component {cmp_designator} position to X:{x}, Y:{y}, Rotation:{rotation}")
    
    response = await altium_bridge.execute_command(
        "set_component_position",
        {
            "designator": cmp_designator,
            "x": x,
            "y": y,
            "rotation": rotation
        }
    )
    
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error setting component position: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to set component position: {error_msg}"})
    
    result = response.get("result", {})
    logger.info(f"Component position set successfully")
    return json.dumps({"success": True, "result": result}, indent=2)

@mcp.tool()
async def move_components(ctx: Context, cmp_designators: list, x_offset: float, y_offset: float, rotation: float = 0) -> str:
    """
    Move components by RELATIVE offset from their current position (not absolute positioning)
    
    IMPORTANT: This moves components BY the offset amount, not TO a position.
    For absolute positioning, use set_component_position instead.
    
    Args:
        cmp_designators (list): List of designators of the components to move (e.g., ["R1", "C5", "U3"])
        x_offset (float): X offset distance in mils (positive = right, negative = left)
        y_offset (float): Y offset distance in mils (positive = up, negative = down)
        rotation (float): New absolute rotation angle in degrees (0-360), if 0 the rotation is not changed
    
    Returns:
        str: JSON object with the result of the move operation
    """
    logger.info(f"Moving components: {cmp_designators} by X:{x_offset}, Y:{y_offset}, Rotation:{rotation}")
    
    # Execute the command in Altium to move components
    response = await altium_bridge.execute_command(
        "move_components",
        {
            "designators": cmp_designators,
            "x_offset": x_offset,
            "y_offset": y_offset,
            "rotation": rotation
        }
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error moving components: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to move components: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})

    logger.info(f"Components moved successfully")
    return json.dumps({"success": True, "result": result}, indent=2)

def altium_rotation(rotation: float, side: str) -> float:
    """Altium's stored rotation for a part placed at `rotation` on `side`.

    The rotation is counter-clockwise as seen from the top; a bottom part's is the angle of its footprint mirrored
    left to right (x negated) and then turned, the convention KiCad's placement and the house capture scripts
    use. Altium's flip mirrors the footprint top to bottom (y negated) instead, so it stores the angle + 180 for
    a bottom part (laser-calib raw-head adapter, 2026-10-23: J4 at 90 and C18-C20 at 270 landed on their pads
    only with it)."""
    return (rotation + 180) % 360 if side == "bottom" else rotation % 360


def side_rotation(stored: float, side: str) -> float:
    """altium_rotation's inverse: the convention's angle of a part Altium stores at `stored`."""
    return (stored - 180) % 360 if side == "bottom" else stored % 360


@mcp.tool()
async def place_components(ctx: Context, placements: list, board: str = "", save: bool = True) -> str:
    """
    Place multiple components at absolute positions in a single Altium transaction.

    This is the batch version of set_component_position - prefer it whenever
    placing more than one component, since every tool call is a full round
    trip into Altium. The whole batch is one undo step. Every part is read back
    from the board after the transaction; a part that does not hold what was set
    is an error. The board is then saved (save true, the default), so a rules
    step, DRC, ECO or file read after it meets the placement; with save a board
    that already holds unsaved changes is refused before anything moves.

    Coordinate conventions: x/y are in mils relative to the board origin;
    rotation is in degrees counter-clockwise as seen from the top. A bottom
    part's rotation is that of its footprint mirrored left to right and then
    turned (KiCad's convention): Altium's own flip mirrors top to bottom, so the
    tool stores rotation + 180 for a bottom part and reads both back.

    Args:
        placements (list): One dict per component:
            - designator (str, required): e.g. "R1"
            - x (float, required): absolute X position in mils
            - y (float, required): absolute Y position in mils
            - rotation (float, optional): degrees (0-360) in the convention
              above. Omit (or pass -1) to keep Altium's current rotation.
            - side (str, optional): "top" or "bottom"; the footprint is
              mirrored when it changes side. Omit to keep the current side.
        board (str, optional): full path of the .PcbDoc to place on; it is
            shown first and checked. Default: the current board.
        save (bool, optional): save the board after the placement (default
            true); false leaves it unsaved and takes a board with unsaved
            changes.

    Example:
        placements=[{"designator": "R1", "x": 1000, "y": 2000, "rotation": 90},
                    {"designator": "J4", "x": 1050, "y": 2000, "rotation": 90, "side": "bottom"}]

    Returns:
        str: JSON with success, board, saved (its path once saved, else ""), placed_count,
             missing_designators, components [{designator, x, y, side, rotation, altium_rotation}]
             as the board holds them after the transaction, and errors for every part that does
             not hold its request and for a board left unsaved with save
    """
    logger.info(f"Placing {len(placements)} components")

    # Flatten each placement into a pipe-delimited string
    # ('Designator|X|Y|AltiumRotation|side') - the DelphiScript side parses the
    # request line by line, so nested JSON objects are not safe to send
    entries, errors, wanted = [], [], {}
    for idx, placement in enumerate(placements):
        if not isinstance(placement, dict):
            errors.append(f"placements[{idx}] must be an object")
            continue
        designator = str(placement.get("designator", "")).strip()
        x, y = placement.get("x"), placement.get("y")
        if not designator or x is None or y is None:
            errors.append(f"placements[{idx}] must include designator, x, and y")
            continue
        if "|" in designator:
            errors.append(f"placements[{idx}] designator must not contain '|'")
            continue
        if "layer" in placement:
            errors.append(f"placements[{idx}]: 'layer' is now 'side' (top or bottom)")
            continue
        side = str(placement.get("side", "") or "").strip().lower()
        if side not in ("", "top", "bottom"):
            errors.append(f"placements[{idx}] side must be 'top' or 'bottom'")
            continue
        try:
            x, y, rotation = float(x), float(y), float(placement.get("rotation", -1))
        except (TypeError, ValueError):
            errors.append(f"placements[{idx}] x, y, and rotation must be numbers")
            continue
        if rotation >= 0 and not side:
            errors.append(f"placements[{idx}]: a rotation needs its side (top or bottom), the convention differs")
            continue
        stored = altium_rotation(rotation, side) if rotation >= 0 else -1
        entries.append(f"{designator}|{x}|{y}|{stored}|{side}")
        wanted[designator] = (x, y, rotation % 360 if rotation >= 0 else None, side)

    if errors:
        return json.dumps({"success": False, "error": "; ".join(errors)})
    if not entries:
        return json.dumps({"success": False, "error": "No placements provided"})
    if board and (not Path(board).is_absolute() or Path(board).suffix.lower() != ".pcbdoc"):
        return json.dumps({"success": False, "error": f"board must be the full path of a .PcbDoc: {board}"})

    params = {"placements": entries, "save": bool(save)}
    if board:
        params["board"] = str(Path(board))
    response = await altium_bridge.execute_command("place_components", params)
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error placing components: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to place components: {error_msg}"})

    result = response.get("result", {})
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return json.dumps({"success": False, "error": "unreadable answer", "raw_response": result[:500]})
    result["errors"] = placement_mismatches(result.get("components", []), wanted)
    if save and not result.get("saved"):
        result["errors"].append(f"{result.get('board')} was not saved: it still holds unsaved changes")
    result["success"] = not result["errors"] and not result.get("missing_designators")
    return json.dumps(result, indent=2)


def placement_mismatches(components: list, wanted: dict) -> list:
    """Each read-back part gets side, rotation (the convention) and altium_rotation; returns what differs from the
    request: position beyond 0.01 mil, side, or rotation beyond 0.01 degree."""
    errors = []
    for c in components:
        side = "bottom" if "bottom" in str(c.get("layer", "")).lower() else "top"
        stored = float(c.get("rotation", 0))
        c.update(side=side, altium_rotation=stored, rotation=round(side_rotation(stored, side), 4))
        c.pop("layer", None)
        x, y, rotation, want_side = wanted.get(c.get("designator"), (None, None, None, ""))
        des = c.get("designator")
        if x is not None and (abs(float(c["x"]) - x) > 0.01 or abs(float(c["y"]) - y) > 0.01):
            errors.append(f"{des} is at ({c['x']}, {c['y']}), not ({x:g}, {y:g})")
        if want_side and side != want_side:
            errors.append(f"{des} is on the {side}, not the {want_side}")
        if rotation is not None and min(abs(c["rotation"] - rotation), 360 - abs(c["rotation"] - rotation)) > 0.01:
            errors.append(f"{des} holds rotation {c['rotation']:g} (Altium {stored:g}), not {rotation:g}")
    return errors


def _mst_length(points: list) -> float:
    """Minimum-spanning-tree length over (x, y) points (Prim's algorithm)."""
    n = len(points)
    if n < 2:
        return 0.0
    import math
    in_tree = [False] * n
    best = [float("inf")] * n
    best[0] = 0.0
    total = 0.0
    for _ in range(n):
        u = min((i for i in range(n) if not in_tree[i]), key=lambda i: best[i])
        in_tree[u] = True
        total += best[u]
        ux, uy = points[u]
        for v in range(n):
            if not in_tree[v]:
                d = math.dist((ux, uy), points[v])
                if d < best[v]:
                    best[v] = d
    return total

@mcp.tool()
async def get_net_connections(ctx: Context, cmp_designators: list = None, max_pads_per_net: int = 40) -> str:
    """
    Get net connectivity and airline (unrouted connection) lengths for the
    nets touching the given components.

    Use this to plan or score a placement: it shows every pad on each net -
    including pads of OTHER components outside the given set (e.g. an input
    filter the cluster must connect to) - plus the net's minimum-spanning-tree
    airline length in mils. Shorter airlines on critical nets (switching
    loops, input/output capacitors) mean a better placement; non-critical
    nets (enables, set resistors, feedback dividers) may be lengthened to
    buy routing space. Plane nets like GND have many pads and a meaningless
    airline - judge them by proximity to plane connections instead.

    Args:
        cmp_designators (list, optional): Components whose nets to analyze
            (e.g. ["U12", "R42"]). Omit to use the current Altium selection.
        max_pads_per_net (int): Nets with more pads than this (e.g. GND)
            return only pads belonging to the given components, plus the
            total pad_count. Their airline is also skipped. Default 40.

    Returns:
        str: JSON object with one entry per net: pad_count,
             airline_mst_mils (None for large nets), and pads
             [{designator, pin, x, y}, ...] in mils relative to the board
             origin. Large nets set pads_truncated=true.
    """
    logger.info(f"Getting net connections (designators={cmp_designators})")

    params = {}
    if cmp_designators:
        params["designators"] = cmp_designators

    response = await altium_bridge.execute_command("get_net_connections", params)

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting net connections: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to get net connections: {error_msg}"})

    result = response.get("result", {})
    if isinstance(result, str):
        result = json.loads(result)

    target_set = set(cmp_designators or result.get("targets", []))

    # Group the flat pad list by net and compute airline lengths
    nets = {}
    for pad in result.get("pads", []):
        nets.setdefault(pad["net"], []).append(pad)

    out_nets = []
    for name in result.get("net_names", []):
        pads = nets.get(name, [])
        entry = {"net": name, "pad_count": len(pads)}
        if len(pads) <= max_pads_per_net:
            entry["airline_mst_mils"] = round(_mst_length([(p["x"], p["y"]) for p in pads]), 1)
            entry["pads"] = [
                {"designator": p["designator"], "pin": p["pin"], "x": p["x"], "y": p["y"]}
                for p in pads
            ]
        else:
            entry["airline_mst_mils"] = None
            entry["pads_truncated"] = True
            entry["pads"] = [
                {"designator": p["designator"], "pin": p["pin"], "x": p["x"], "y": p["y"]}
                for p in pads
                if not target_set or p["designator"] in target_set
            ]
        out_nets.append(entry)

    out_nets.sort(key=lambda e: (e["airline_mst_mils"] is None, -(e["airline_mst_mils"] or 0)))

    logger.info(f"Net connections: {len(out_nets)} nets")
    return json.dumps({"net_count": len(out_nets), "nets": out_nets}, indent=2)

def _rotate_offset(dx: float, dy: float, degrees: float) -> tuple:
    """Rotate a rotation-0 pad offset counterclockwise by the given angle."""
    import math
    rad = math.radians(degrees)
    return (dx * math.cos(rad) - dy * math.sin(rad),
            dx * math.sin(rad) + dy * math.cos(rad))

@mcp.tool()
async def check_orientation(ctx: Context, cmp_designators: list = None, min_improvement_mils: float = 25) -> str:
    """
    Advisory check: find 2-pad passives whose rotation could be improved.

    For each 2-pad component in the target set, every orthogonal rotation
    (0/90/180/270) is scored in place, summing one term per pad:
    - routable nets (few pads): the net's total airline (MST) length with
      this pad at the candidate position - so a pad that merely slides along
      a pass-through flow (e.g. a bulk cap in a power chain) scores as
      nearly free, while a genuine detour costs its real length
    - plane nets (many pads, e.g. GND): distance to the nearest same-net
      pad - a capacitor's ground-return loop is local, so proximity to the
      IC's GND/thermal pad is what matters
    A component is reported when a different rotation beats the current one
    by at least min_improvement_mils.

    This is advisory, not pass/fail: it measures geometry only and knows
    nothing about net criticality. Suggestions matter for loop-critical
    parts (capacitor ground returns, snubbers, input/output filters) and
    should usually be ignored for parts whose orientation is electrically
    arbitrary (pull-ups, strapping resistors, enables) or where a datasheet
    layout recommendation says otherwise. Weigh suggestions with judgement
    rather than applying them blindly. If a suggestion with
    bbox_changes=true is applied (a 90-degree change alters the body
    outline), re-run check_placement afterwards.

    Args:
        cmp_designators (list, optional): Components to check. Omit to use
            the current Altium selection. Components with more or fewer than
            2 pads are skipped.
        min_improvement_mils (float): Only report components where the best
            rotation improves the connection score by at least this many
            mils (default 25).

    Returns:
        str: JSON object with checked_count and suggestions, each having
             designator, current_rotation, suggested_rotation,
             improvement_mils, bbox_changes, and a per-pad breakdown of
             nearest same-net distances at the current vs suggested rotation.
    """
    import math

    logger.info(f"Checking orientation (designators={cmp_designators})")

    # Resolve the target designators from the selection when not given
    if not cmp_designators:
        sel_resp = await altium_bridge.execute_command("get_selected_components_coordinates", {})
        if not sel_resp.get("success", False):
            return json.dumps({"success": False, "error": sel_resp.get("error", "Unknown error")})
        sel = sel_resp.get("result", [])
        if isinstance(sel, str):
            sel = json.loads(sel)
        cmp_designators = [c["designator"] for c in sel if "designator" in c]
        if not cmp_designators:
            return json.dumps({"success": False, "error": "No components selected and no designators given"})

    pins_resp = await altium_bridge.execute_command("get_component_pins", {"designators": cmp_designators})
    if not pins_resp.get("success", False):
        return json.dumps({"success": False, "error": pins_resp.get("error", "Unknown error")})
    comps = pins_resp.get("result", [])
    if isinstance(comps, str):
        comps = json.loads(comps)

    nets_resp = await altium_bridge.execute_command("get_net_connections", {"designators": cmp_designators})
    if not nets_resp.get("success", False):
        return json.dumps({"success": False, "error": nets_resp.get("error", "Unknown error")})
    net_data = nets_resp.get("result", {})
    if isinstance(net_data, str):
        net_data = json.loads(net_data)

    # net name -> list of (designator, x, y) for every pad on the net
    net_pads = {}
    for p in net_data.get("pads", []):
        net_pads.setdefault(p["net"], []).append((p["designator"], p["x"], p["y"]))

    PLANE_NET_PAD_COUNT = 40  # nets above this are treated as planes

    suggestions = []
    checked = 0
    for comp in comps:
        pins = comp.get("pins", [])
        if len(pins) != 2 or "x" not in comp:
            continue
        mirror = comp.get("layer") == "Bottom Layer"

        def score(rotation):
            """Hybrid per-pad score (see docstring); None if no pad scores."""
            total, detail = 0.0, []
            for pin in pins:
                net = pin.get("net", "")
                cands = [(x, y) for (d, x, y) in net_pads.get(net, [])
                         if d != comp["designator"]] if net else []
                if not cands:
                    continue
                dx = -pin["dx"] if mirror else pin["dx"]
                ox, oy = _rotate_offset(dx, pin["dy"], rotation)
                px, py = comp["x"] + ox, comp["y"] + oy
                if len(cands) > PLANE_NET_PAD_COUNT:
                    value = min(math.dist((px, py), c) for c in cands)
                    metric = "nearest_return_mils"
                else:
                    value = _mst_length(cands + [(px, py)])
                    metric = "net_airline_mils"
                total += value
                detail.append({"pin": pin["name"], "net": net, metric: round(value, 1)})
            return (total, detail) if detail else None

        current = comp.get("rotation", 0) % 360
        cur = score(current)
        if cur is None:
            continue
        checked += 1

        best_rot, best = current, cur
        for r in (0, 90, 180, 270):
            s = score(r)
            if s is not None and s[0] < best[0]:
                best_rot, best = r, s

        improvement = cur[0] - best[0]
        if best_rot != current and improvement >= min_improvement_mils:
            suggestions.append({
                "designator": comp["designator"],
                "current_rotation": current,
                "suggested_rotation": best_rot,
                "improvement_mils": round(improvement, 1),
                "bbox_changes": (best_rot - current) % 180 != 0,
                "current_pads": cur[1],
                "suggested_pads": best[1],
            })

    suggestions.sort(key=lambda s: -s["improvement_mils"])
    logger.info(f"Orientation check: {len(suggestions)} suggestions from {checked} components")
    return json.dumps({
        "checked_count": checked,
        "min_improvement_mils": min_improvement_mils,
        "suggestion_count": len(suggestions),
        "suggestions": suggestions,
    }, indent=2)


def placement_check_params(cmp_designators, clearance_mils, courtyard) -> dict:
    """check_placement's request: the designators (none: Altium's selection), the clearance in mils, above 0, and
    whether to measure courtyards and copper only. ValueError names what is wrong."""
    if isinstance(clearance_mils, bool) or not isinstance(clearance_mils, (int, float)) or clearance_mils <= 0:
        raise ValueError(f"clearance_mils must be a number of mils above 0: {clearance_mils!r}")
    if not isinstance(courtyard, bool):
        raise ValueError(f"courtyard must be true or false: {courtyard!r}")
    params = {"clearance_mils": float(clearance_mils), "courtyard": courtyard}
    if cmp_designators:
        params["designators"] = [str(d) for d in cmp_designators]
    return params


@mcp.tool()
async def check_placement(ctx: Context, cmp_designators: list = None, clearance_mils: float = 6,
                          courtyard: bool = False) -> str:
    """
    Verify component placement: find overlaps and clearance violations.

    Checks each target component against every other component on the same
    side of the board. Bounding boxes (which include silkscreen) are used as
    a fast prefilter; close pairs are then measured precisely with Altium's
    primitive-to-primitive distance, so reported distances are true minimum
    distances between any primitives (pads, silk, etc.) of the two parts.

    courtyard=True measures the house rule instead, against the parts on both
    sides: each part is kept out by its courtyard (Mechanical 15 and every
    mechanical layer named for a courtyard), or by its copper when it has none
    (a module footprint that is a board's edge). Two courtyards on one side, or
    a courtyard and the other part's copper on the courtyard's side (that
    outer layer and multilayer pads), are compared as boxes, so a part inside
    another's courtyard is an overlap; copper meets copper on a layer both
    carry, and courtyards on opposite sides never meet. A top part over a
    module's bottom pad is clear, a bottom part under it an overlap. Silk,
    assembly and other outlines count for nothing; silk closer than the
    clearance to the other part's silk on the same side is listed apart under
    silk.

    Run this after placing components - a screenshot is not verification.

    Args:
        cmp_designators (list, optional): Components to check (e.g. ["U12", "R42"]).
            Omit to check the components currently selected in Altium.
        clearance_mils (float): Minimum allowed primitive-to-primitive distance
            in mils (default 6). Pairs closer than this are reported.
        courtyard (bool): measure courtyards and copper only (default false).

    Returns:
        str: JSON object with checked_count, measure ("all" or "courtyard"),
             violation_count, and a violations list. Each violation has a/b
             designators, type ("bounding_box_overlap" = the parts' outlines
             intersect, or "clearance" = distance below threshold; with
             courtyard "overlap" = distance 0 and between, what was compared:
             "courtyards", "courtyard and copper" or "copper"), distance_mils
             (0 = touching/overlapping), overlap sizes when boxes intersect,
             and the other part's x/y position. With courtyard also
             courtyard_layers (their names), silk_count and silk [{a, b,
             distance_mils}]. An empty violations list means the placement is
             clean at the given clearance.
    """
    logger.info(f"Checking placement (designators={cmp_designators}, clearance={clearance_mils}, "
                f"courtyard={courtyard})")
    try:
        params = placement_check_params(cmp_designators, clearance_mils, courtyard)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})

    response = await altium_bridge.execute_command("check_placement", params)

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error checking placement: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to check placement: {error_msg}"})

    result = response.get("result", {})
    logger.info(f"Placement check complete: {result.get('violation_count', '?')} violations")
    return json.dumps(result, indent=2)

PW_RENDERFULLCONTENT = 2      # PrintWindow: render through DWM, DirectX content included (Windows 8.1+)
# What view_type="dialog" grabs: Altium's boxes and its Delphi dialog forms (the ECO, the DRC), never the main frame
DIALOG_GRAB_CLASSES = DIALOG_CLASSES
ALTIUM_FRAMES = ("TApplication", "TDocumentForm")      # its main and document windows, live 2026-10-23


def _dialog_windows():
    """Altium's dialogs, topmost first: its boxes, its WPF windows (Sign In) and its Delphi dialog forms."""
    return [(h, c, t) for h, c, t in x2_windows(None)
            if c in DIALOG_GRAB_CLASSES or c.startswith("HwndWrapper[")
            or (c.startswith("T") and c.endswith("Form") and c not in ALTIUM_FRAMES and "Altium Designer" not in t)]


def _image_result(meta: dict, img: Image.Image):
    previous = screenshot.compare_with_last(EXCHANGE_DIR, img, {"kind": meta.get("view_type"),
                                                                "document": meta.get("document"),
                                                                "title": meta.get("window_title")})
    if previous is not None:
        meta["success"] = False
        meta["identical_to_previous"] = True
        meta["error"] = screenshot.identical_error(previous)
    meta.update(width=img.size[0], height=img.size[1])
    return [json.dumps(meta, ensure_ascii=False), MCPImage(data=screenshot.png(img), format="png")]


@mcp.tool()
async def get_screenshot(ctx: Context, view_type: str = "pcb", zoom_to: list = None, document: str = "",
                         width: int = 1600):
    """
    Picture of a PCB or schematic document, printed by Altium itself, or of an Altium dialog.

    A board or sheet is printed through Altium's own PCB Print or Schematic Print, published to PDF and
    rasterised: it shows the document's state in memory (unsaved changes included), whatever Altium's window
    shows. Altium's window held another document's pixels while naming the requested one (2026-10-23), so the
    window is grabbed only for a dialog. A picture identical to the previous capture (any server, any kind) is
    returned with success false and says so: nothing changed, or the capture missed the change.

    Args:
        view_type (str): 'pcb', 'sch' or 'dialog' (the topmost Altium dialog or box, grabbed from its window)
        zoom_to (list, optional): PCB only: component designators (e.g. ["U12", "R42"]); the print covers
            their bounding box plus a margin instead of the whole board
        document (str, optional): full path of the .PcbDoc or .SchDoc to print (its project must be open);
            default the focused document of the view_type
        width (int): pixels on the picture's longer side (default 1600, at most 4000)

    Returns:
        A JSON text block (document, project, the printed layers, zoomed and missing designators, the PDF's
        path) and the image
    """
    view = (view_type or "").lower()
    logger.info(f"get_screenshot view={view} zoom_to={zoom_to} document={document!r}")
    if view == "dialog":
        windows = _dialog_windows()
        if not windows:
            return json.dumps({"success": False, "error": "Altium shows no dialog"})
        hwnd, cls, title = windows[0]
        try:
            img = await asyncio.wait_for(asyncio.to_thread(grab_window, hwnd), timeout=10)
        except Exception as e:
            return json.dumps({"success": False, "error": f"could not grab the dialog {cls} \"{title}\": {e}"})
        meta = {"success": True, "view_type": "dialog", "window_title": title, "window_class": cls,
                "other_dialogs": [f'{c} "{t}"' for _, c, t in windows[1:]]}
        return _image_result(meta, img)
    if view not in ("pcb", "sch"):
        return json.dumps({"success": False, "error": f"view_type is 'pcb', 'sch' or 'dialog', not {view_type!r}"})
    if document and (not Path(document).is_absolute() or Path(document).suffix.lower() not in (".pcbdoc", ".schdoc")):
        return json.dumps({"success": False, "error": f"document must be the full path of a .PcbDoc or .SchDoc: "
                                                      f"{document}"})
    if zoom_to and view != "pcb":
        return json.dumps({"success": False, "error": "zoom_to applies to a PCB print only"})

    params = {"view_type": view}
    if document:
        params["document"] = str(Path(document))
    if zoom_to:
        params["designators"] = list(zoom_to)
    files = {screenshot.TEMPLATE: screenshot.outjob(view).encode("ascii")}   # the bridge keeps its code page
    response = await altium_bridge.execute_command("take_view_screenshot", params, files=files, ctx=ctx)
    if not response.get("success", False):
        return json.dumps({"success": False, "error": f"Altium did not print the {view} document: "
                                                      f"{response.get('error', 'unknown error')}"})
    meta = response.get("result", {})
    meta = meta if isinstance(meta, dict) else {}
    pdf = Path(meta.get("pdf") or Path(response.get("mail", "")) / screenshot.PDF)
    try:
        img = await asyncio.to_thread(screenshot.render_pdf, pdf, width)
    except ImportError:
        return json.dumps({"success": False, "error": "rasterising the print needs pypdfium2 in the server's "
                                                      "environment (pip install pypdfium2)", "pdf": str(pdf)})
    except Exception as e:
        return json.dumps({"success": False, "error": f"could not rasterise {pdf}: {e}"})
    meta["success"] = True
    meta["missing_designators"] = [d for d in str(meta.get("missing_designators", "")).split(",") if d]
    meta["layers"] = [d for d in str(meta.get("layers", "")).split(",") if d]
    if screenshot.colours(img) <= 2:
        meta["success"] = False
        meta["error"] = "the print is blank (one colour): nothing of the document lies in the printed area"
    return _image_result(meta, img)


@mcp.tool()
async def layout_duplicator(ctx: Context) -> str:
    """
    First step of layout duplication. Selects source components and returns data to match with destination components.
    
    Returns:
        str: JSON object with source and destination component data for matching
    """
    logger.info("Starting layout duplication - selection phase")
    
    # Execute the command in Altium to get component data
    response = await altium_bridge.execute_command(
        "layout_duplicator", 
        {}
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error in layout duplication selection: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to start layout duplication: {error_msg}"})
    
    # Get the component data
    components_data = response.get("result", {})
    
    if not components_data:
        logger.info("No component data found")
        return json.dumps({"success": False, "error": "No component data returned from Altium"})
    
    # Parse the result to check if no source components were selected
    try:
        if isinstance(components_data, str):
            result_json = json.loads(components_data)
            if not result_json.get("success", True):
                logger.info(f"Source component selection issue: {result_json.get('message', 'Unknown issue')}")
                return json.dumps(result_json)
    except Exception as e:
        logger.error(f"Error parsing layout duplicator result: {e}")
    
    logger.info(f"Retrieved layout duplicator component data")
    return json.dumps(components_data, indent=2)

@mcp.tool()
async def layout_duplicator_apply(ctx: Context, source_designators: list, destination_designators: list) -> str:
    """
    Second step of layout duplication. Applies the layout of source components to destination components.
    
    Args:
        source_designators (list): List of source component designators (e.g., ["R1", "C5", "U3"])
        destination_designators (list): List of destination component designators (e.g., ["R10", "C15", "U7"])
    
    Returns:
        str: JSON object with the result of the layout duplication
    """
    logger.info(f"Applying layout duplication from {source_designators} to {destination_designators}")
    
    # Execute the command in Altium to apply layout duplication
    response = await altium_bridge.execute_command(
        "layout_duplicator_apply",
        {
            "source_designators": source_designators,
            "destination_designators": destination_designators
        }
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error applying layout duplication: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to apply layout duplication: {error_msg}"})
    
    # Get the result data
    result = response.get("result", {})
    
    logger.info(f"Layout duplication applied successfully")
    return json.dumps(result, indent=2)
    
@mcp.tool()
async def get_pcb_rules(ctx: Context) -> str:
    """
    Get all design rules from the current Altium PCB
    
    Returns:
        str: JSON array of PCB design rules with their properties
    """
    logger.info("Getting PCB design rules")
    
    # Execute the command in Altium to get rule data
    response = await altium_bridge.execute_command(
        "get_pcb_rules",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting PCB rules: {error_msg}")
        return json.dumps({"error": f"Failed to get PCB rules: {error_msg}"})
    
    # Get the rules data
    rules_data = response.get("result", [])
    
    if not rules_data:
        logger.info("No PCB rules found")
        return json.dumps({"message": "No PCB rules found in the current document"})
    
    logger.info(f"Retrieved PCB rules data")
    return json.dumps(rules_data, indent=2)

@mcp.tool()
async def get_pcb_layer_stackup(ctx: Context) -> str:
    """
    Get the detailed layer stackup information from the current Altium PCB including
    copper thickness, dielectric materials, constants, and heights
    
    Returns:
        str: JSON object with detailed layer stackup information
    """
    logger.info("Getting PCB layer stackup information")
    
    # Execute the command in Altium to get layer stackup data
    response = await altium_bridge.execute_command(
        "get_pcb_layer_stackup",
        {}  # No parameters needed
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting PCB layer stackup: {error_msg}")
        return json.dumps({"error": f"Failed to get PCB layer stackup: {error_msg}"})
    
    # Get the stackup data
    stackup_data = response.get("result", {})
    
    if not stackup_data:
        logger.info("No PCB layer stackup found")
        return json.dumps({"message": "No PCB layer stackup found in the current document"})
    
    logger.info(f"Retrieved PCB layer stackup data")
    return json.dumps(stackup_data, indent=2)

@mcp.tool()
async def get_output_job_containers(ctx: Context) -> str:
    """
    Get all available output job containers from a specified OutJob file
    
    Args:
        outjob_path (str): Path to the OutJob file (optional, will use first open OutJob if not provided)
    
    Returns:
        str: JSON array with all output job containers and their properties
    """
    logger.info("Getting output job containers from the first open OutJob")
    
    # Execute the command in Altium to get output job containers
    response = await altium_bridge.execute_command(
        "get_output_job_containers", 
        {}  # No parameters needed - will use first open OutJob
    )
    
    # Check for success
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting output job containers: {error_msg}")
        return json.dumps({"error": f"Failed to get output job containers: {error_msg}"})
    
    # Get the containers data
    containers_data = response.get("result", [])
    
    if not containers_data:
        logger.info("No output job containers found")
        return json.dumps({"message": "No output job containers found"})
    
    logger.info(f"Retrieved output job containers data")
    return containers_data  # Already in JSON format

@mcp.tool()
async def get_outjob_settings(ctx: Context, outjob_path: str) -> str:
    """
    Read an .OutJob file from disk and return its outputs, containers and settings.

    Works without Altium and on OutJobs that are not open. It reads the SAVED
    file: changes not yet saved in Altium are not visible.

    Notes for interpreting the result:
    - An output is generated when it is connected to a container
      (connected_containers non-empty). The per-output "enabled" flag mirrors
      OutputEnabledN in the file and does not always match the UI.
    - Gerber layers are decoded from Altium V7 layer IDs; unknown IDs are
      reported as "Unknown layer 0x...".

    Args:
        outjob_path (str): Full path to the .OutJob file

    Returns:
        str: JSON with path, group_name, variant, containers (name, type,
             output paths, file name expression, add_to_project) and outputs
             (type, name, document_path, variant, connected_containers and a
             decoded summary for Gerber, NC Drill, test points, pick and
             place and print outputs, plus raw_settings)
    """
    try:
        data = parse_outjob(outjob_path)
    except FileNotFoundError:
        return json.dumps({"error": f"OutJob not found: {outjob_path}"})
    except Exception as e:
        return json.dumps({"error": f"Could not parse OutJob: {e}"})
    return json.dumps(data, indent=2, ensure_ascii=False)


async def _simple_command(command: str, params: dict, what: str) -> str:
    """Run a bridge command and return its result as JSON text."""
    response = await altium_bridge.execute_command(command, params)
    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error getting {what}: {error_msg}")
        return json.dumps({"error": f"Failed to get {what}: {error_msg}"})
    result = response.get("result", {})
    return result if isinstance(result, str) else json.dumps(result, indent=2, ensure_ascii=False)


@mcp.tool()
async def get_project_info(ctx: Context) -> str:
    """
    Get the focused Altium project: parameters, documents and variants.

    Read-only. Use it to check that nothing has unsaved changes before
    generating outputs, and to read project parameters and variants.

    Returns:
        str: JSON with project_path, parameters [{name, value}],
             schematic_sheet_count, modified_document_count, documents
             [{path, kind, state}] where state is "closed", "open" or
             "modified" (unsaved changes in memory), and variants
             [{name, variations [{designator, kind}]}] where kind is
             "not_fitted" or "alternate"
    """
    return await _simple_command("get_project_info", {}, "project info")


@mcp.tool()
async def get_project_components(ctx: Context, include_parameters: bool = False) -> str:
    """
    List every component of the compiled project (schematic side).

    Compiles the focused project in memory first (nothing is saved). Unlike
    the PCB component tools, this returns the schematic Comment of each
    instance, which is where values of generic passives usually live.

    Args:
        include_parameters (bool): Also return every component parameter
            (e.g. Manufacturer_Part_Number). Default False - the output is
            much larger with parameters.

    Returns:
        str: JSON with component_count and components [{designator, comment,
             footprint, library_reference, sheet, parameters?}]
    """
    return await _simple_command("get_project_components",
                                 {"include_parameters": include_parameters}, "project components")


@mcp.tool()
async def get_erc_violations(ctx: Context, compile: bool = True) -> str:
    """
    Compile the focused project and list its ERC/compiler violations.

    Compiling works in memory and does not save any document (Altium may
    refresh the derived .PrjPcbStructure file).

    Args:
        compile (bool): Compile before reading (default True). False returns
            the violations of the last compilation.

    Returns:
        str: JSON with total, fatal, errors, warnings and violations
             [{level, type, detail}]
    """
    return await _simple_command("get_erc_violations", {"compile": compile}, "ERC violations")


EDIT_SPEC_FILE = "edit_spec.txt"             # in the request's folder (mailbox.py)
ANNOTATE_SPEC_FILE = "annotate_spec.txt"


async def _bridge_spec(command: str, spec_file: str, lines: list, ctx: Context = None) -> dict:
    """Run a command with its spec in the request's folder; the bridge's JSON as a dict."""
    response = await altium_bridge.execute_command(command, {}, files={spec_file: "\n".join(lines) + "\n"}, ctx=ctx)
    if not response.get("success", False):
        return {"success": False, "error": response.get("error", "unknown error")}
    result = response.get("result", {})
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return {"success": False, "error": "the bridge's answer is not JSON", "raw_response": result[:500]}
    return result


async def _spec_command(command: str, spec_file: str, lines: list) -> str:
    return json.dumps(await _bridge_spec(command, spec_file, lines), indent=2, ensure_ascii=False)


async def _edit_command(sheet: str, ops: list, power_nets: list, save: bool, ctx: Context = None) -> str:
    """edit_schematic in two passes when it connects pins or draws: the ops run and Altium reports the pins to
    connect and the sheet (pass 1, nothing saved), sheet_plan resolves the wire, junction and port ops and places
    each connection where it touches nothing, pass 2 draws it all, then the sheet ports on the drawn label stubs, and
    saves."""
    try:
        lines = schematic_edit.edit_spec(sheet, ops, power_nets, save=False)
        draws = schematic_edit.draw_ops(ops)
        ports = schematic_edit.sheet_port_lines(ops)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})
    path = lines[0].split("|", 1)[1]
    power = schematic_edit.power_table(list(power_nets or []) + [d["net"] for d in draws if d["op"] == "port"])
    connects = bool(draws or ports) or any(line.split("|", 1)[0] in schematic_edit.CONNECTIONS for line in lines)
    first = await _bridge_spec("edit_schematic", EDIT_SPEC_FILE,
                               lines + (["GEOMETRY"] if connects else ["SAVE"] if save else []), ctx)
    geometry, pending = first.pop("geometry", None), first.pop("pending", [])
    errors = first.setdefault("errors", [])
    if connects and geometry is not None:
        objects = sheet_plan.plan(geometry, pending, power, draws)
        second = await _bridge_spec("edit_schematic", EDIT_SPEC_FILE,
                                    sheet_plan.draw_spec(path, objects, False) + ports + (["SAVE"] if save else []), ctx)
        first["saved"] = second.get("saved", "")
        errors += objects["errors"] + second.get("errors", [])
        if "error" in second:
            errors.append(f"drawing the connections: {second['error']}")
        first["connections"] = objects["connections"]
        first["overlaps"] = objects["overlaps"]
    unbound = schematic_edit.unbound_models(first.get("placed", []))
    if unbound:
        errors.append("models on library Any, neither taken from their component library nor given a library path "
                      "(the ECO would search every installed library for them): " + ", ".join(unbound))
    if "error" not in first:
        first["success"] = not errors
    return json.dumps(first, indent=2, ensure_ascii=False)


@mcp.tool()
async def open_project(ctx: Context, project_path: str) -> str:
    """
    Open an Altium project (or find it open) and open its schematic sheets.

    Drives the live Altium: it opens documents on the user's screen.

    Args:
        project_path (str): full path of the .PrjPcb.

    Returns:
        str: JSON with success (true when the project is now the focused one), project, focused,
             documents [{path, kind}]
    """
    path = Path(project_path)
    if not path.is_absolute() or path.suffix.lower() != ".prjpcb" or not path.is_file():
        return json.dumps({"success": False,
                           "error": f"project_path must be the full path of an existing .PrjPcb: {project_path}"})
    return await _simple_command("open_project", {"project_path": str(path)}, "open project")


@mcp.tool()
async def edit_schematic(ctx: Context, sheet: str, ops: list, power_nets: list = None, save: bool = True) -> str:
    """
    Edit an EXISTING schematic sheet: delete, rename, connect, place and replace parts.

    Every connection the tool makes is a short stub out of the pin with a net label or a power port, so it never
    routes a wire across the sheet; wires inside a block are the wire op's. Where a connection goes is planned
    from the placed symbols (server/sheet_plan.py, the house style): a horizontal label on a stub sized to its
    text, at the far end on a left pin; a power port pointing up (supply) or down (ground) only, moved out or
    jogged beside the pin until it touches no body, pin or text, shared by neighbouring pins of one part on one
    power net, else a label; never a spot that touches another net's pin, wire, label or port. "connections" returns each one's boxes and
    "overlaps" what still touches. Read the result's "errors": an op that cannot be carried out changes nothing
    and is listed there; the rest still runs. Verify with a connectivity readback of the saved sheet
    (c:/Projects/tools/altium/sch_nets.py), not with the result alone. Every .SchLib the call opens to copy a
    symbol from is closed again at its end, saving nothing, a failed op's too; one open before the call stays open.

    Components are referenced as "uid:<UNIQUEID>" (read from the file) or "des:<designator>". A designator names
    every record of a multi-part part: relink changes each record, attach_pin, detach_pin and no_erc_pins act on
    each record that shows the pin, and replace refuses one naming more than one record (replace each part by uid).

    Args:
        sheet: full path of the .SchDoc (opened if it is not).
        ops: list of dicts, run in order:
            {"op": "delete", "uid"}                      any sheet object by unique id
            {"op": "delete_component", "uid", "dead_ends": true, "keep": [uid...]}
                                                         a part, and with dead_ends the wires left connecting
                                                         nothing plus the labels and ports on them, mid-stub too,
                                                         unless another wire or a pin is under one
            {"op": "rename_label", "uid", "text"}        {"op": "rename_net", "old", "new"} (every label)
                                                         a renamed label's stub (one straight wire, one end free)
                                                         is redrawn as long as the new text, on the 50 mil grid; a
                                                         label at the free end moves with it. Refused, the old text
                                                         kept, where the stub would meet anything
            {"op": "port_rename", "uid", "new"}          {"op": "label_to_port", "uid", "text"}
            {"op": "attach_pin", "comp", "pin", "net"}   stub + label or port on a pin
            {"op": "detach_pin", "comp", "pin"}          removes the port, label or stub at a pin and every
                                                         label or port on that stub; a wire that also serves
                                                         something else is refused
            {"op": "no_erc_pins", "comp", "pins": [..]}  a pin end with a marker keeps that one, no second
            {"op": "attach_entry", "symbol", "entry", "net"}   a sheet symbol's entry
            {"op": "place", "designator", "symbol_library", "lib_ref", "x", "y", "orient", "mirror",
             "link_library", "db_table", "design_item", "footprint", "alternate_footprints": [..],
             "footprint_library", "comment", "description", "parameters": {}, "pins": {pin: net}, "no_erc": [pin],
             "part", "comment_at": [dx, dy]}
                                                         symbol copied from a .SchLib; link_library (e.g.
                                                         "dnbm.IntLib" or "Res-Gen.DbLib") is what it links to;
                                                         part (1 = A, 2 = B, ...) places one part of a multi-part
                                                         symbol, one place op per part under one designator;
                                                         description sets the part's own Description (the
                                                         component description, as build_schematic's does).
                                                         Altium keeps no parameter named Description, so a
                                                         "Description" key in parameters (any case) sets the
                                                         component description too; given both, they must match.
                                                         footprint_library, for a part without a database row
                                                         (no design_item): the full path of the .PcbLib holding
                                                         footprint; the model takes it by that path, not from the
                                                         component library, which has no row to give one.
                                                         The part's footprint models become exactly footprint
                                                         (current) and alternate_footprints (a DbLib row's
                                                         Footprint Ref 2 onward); without footprint it has none
                                                         but what its symbol carries. comment_at puts the comment's
                                                         bottom right corner on the part's origin plus (dx, dy)
                                                         mils, right-aligned and unrotated, before the connections
                                                         are planned, so they keep clear of it
            {"op": "replace", "comp", "symbol_library", "lib_ref", "pin_map": {old: new},
             "nets": {new_pin: net}, place's link fields, "description", "parameters": {}}
                                                         every mapped new pin needs its net: the old pin's end
                                                         gets the same net, so the old wiring keeps it. The new
                                                         part takes the old origin, never moved; refused, nothing
                                                         changed, when a pin the op names is not on the new
                                                         symbol, its body overlaps another part's, or a pin end
                                                         lands on anything but the old end of the pin mapped onto
                                                         it. An old pin connected but in no pin_map entry is an
                                                         error naming it: its wiring is left without a pin
            {"op": "relink", "comp", "link_library", "db_table", "design_item", "footprint",
             "alternate_footprints", "footprint_library", "comment", "description", "parameters": {}}
                                                         changes a part's library link and keeps its symbol: a
                                                         link_library given replaces the old table (an IntLib
                                                         clears it), the footprints replace every old model;
                                                         the rest as in place. tools/altium/relink_plan.py writes
                                                         the ops that move a sheet to house.DbLib
            {"op": "wire", "points": [P, P, ...], "junctions": [P]}   orthogonal segments through the points; a
                                                         point P is [x, y] or {"comp": "des:R1", "pin": "2"} (that
                                                         pin's end); junctions only at tees
            {"op": "junction", "at": P}
            {"op": "port", "net", "at": P, "orient": 0-3, "style"}   a power port at a chosen point, its own
                                                         orientation (style and shown name from the net)
            {"op": "sheet_port", "net", "io": "in"|"out"|"bidi"|"unspecified", "side": "left"|"right"}
                                                         a child sheet's port, drawn last: on the free end of the
                                                         first stub carrying a label of net whose free end points
                                                         side, running on that way, as wide as its name plus 100
                                                         mil; io seen from the sheet. One per net; refused,
                                                         naming it, where its far end would meet anything
        power_nets: nets drawn as power ports (GND, 3V3 and 5V always are; GND power ground pointing down with its
            name hidden, a supply a bar pointing up).
        save: save the sheet afterwards (default true).

    Returns:
        str: JSON with success, sheet, saved, ops, errors [..], placed [{designator, uid, x, y,
             pins [[pin, x, y]], unassigned_pins, library, design_item, models [{type, name, current,
             use_component_library, library_path}]}], removed [{uid, kind, text}] (every object the call took
             off the sheet: component, wire, label, power_port, port, no_erc, junction), connections
             [{designator, pins, net, kind, boxes [[l, b, r, t]]}], overlaps [..]. A part linked to an IntLib or
             DbLib takes every model from that
             library ("Use Component Library", as a hand-placed part does) but the footprint given a
             footprint_library; a model on library Any (neither) is an error.
    """
    return await _edit_command(sheet, ops, power_nets, save, ctx)


@mcp.tool()
async def replace_component(ctx: Context, sheet: str, comp: str, symbol_library: str, lib_ref: str,
                            pin_map: dict, nets: dict, no_erc: list = None, link_library: str = None,
                            db_table: str = None, design_item: str = None, footprint: str = None,
                            comment: str = None, parameters: dict = None, orient: int = None,
                            mirror: bool = False, power_nets: list = None, save: bool = True) -> str:
    """
    Swap a placed part for another symbol and keep its connections, mapping pins by FUNCTION.

    The old pin ends get net labels carrying each pin's net, the old part goes, the new one takes its origin,
    never moved, and every new pin gets a stub with its net. Refused, nothing changed, when the new part does not
    fit there: a pin the call names missing on the new symbol, its body over another part's, a pin end on
    anything but the old end of the pin mapped onto it. An old pin connected but in no pin_map entry is an error
    naming it, its wiring left without a pin. Map by function, never by number: two diode symbols can number the
    cathode differently.

    Args:
        sheet: full path of the .SchDoc.
        comp: "uid:<UNIQUEID>" or "des:<designator>" of the part to replace; its designator is kept.
        symbol_library: full path of the .SchLib holding the new symbol; closed again at the end of the call
            unless it was open before.
        lib_ref: the new symbol's name.
        pin_map: {old_pin: new_pin}.
        nets: {new_pin: net}, required for every mapped pin; extra entries connect unmapped new pins.
        no_erc: new pins to mark No-ERC.
        link_library, db_table, design_item, footprint, comment, parameters: the new part's library link; a
            "Description" parameter sets the part's own Description (Altium keeps no parameter of that name).
        orient: 0-3, default the old part's. mirror: mirror the new part.
        power_nets: nets drawn as power ports (GND, 3V3, 5V always). save: save afterwards.

    Returns:
        str: edit_schematic's JSON.
    """
    op = dict(op="replace", comp=comp, symbol_library=symbol_library, lib_ref=lib_ref, pin_map=pin_map,
              nets=nets, no_erc=no_erc or [], link_library=link_library, db_table=db_table,
              design_item=design_item, footprint=footprint, comment=comment, parameters=parameters or {},
              mirror=mirror)
    if orient is not None:
        op["orient"] = orient
    return await _edit_command(sheet, [op], power_nets, save, ctx)


@mcp.tool()
async def annotate_schematic(ctx: Context, sheets: list, assignments: dict, save: bool = True) -> str:
    """
    Set component designators by unique id across one or more sheets.

    Explicit on purpose: compute the assignment first (schematic_edit.auto_designators gives the usual one:
    the next free number per prefix, top to bottom, then left to right) and review it before it is written.

    Args:
        sheets: full paths of the .SchDoc files to search.
        assignments: {unique_id: designator}; designators must be unique and annotated.
        save: save every sheet afterwards.

    Returns:
        str: JSON with success, assigned [{uid, old, new}], missing_uids
    """
    try:
        lines = schematic_edit.annotate_spec(sheets, assignments, save)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})
    return await _spec_command("annotate_schematic", ANNOTATE_SPEC_FILE, lines)


@mcp.tool()
async def get_board_summary(ctx: Context) -> str:
    """
    Summarise the focused PCB for fabrication checks.

    Read-only. Coordinates are millimetres relative to the board origin.

    Returns:
        str: JSON with pcb_path, board origin (absolute), board_outline
             (bounding box, width, height, vertex count), dimensions (cotas)
             with their extents, via_count and vias_by_hole, components per
             side, layer_contents (primitive count per layer, split into free
             objects and objects inside components) and overlay_texts (free
             texts on Top/Bottom Overlay, e.g. board name and revision)
    """
    return await _simple_command("get_board_summary", {}, "board summary")


@mcp.tool()
async def get_component_primitives(ctx: Context, designators: list) -> str:
    """
    List the primitives of PCB components with the layer each one is on.

    Read-only. Useful to check footprints that were flipped to the bottom
    side (e.g. fiducials: the copper and the pad that opens the solder mask
    must end up on the same layer) and pad mask expansions.

    Args:
        designators (list): Component designators, e.g. ["FD1", "FD3"]

    Returns:
        str: JSON with components [{designator, footprint, layer, x_mm, y_mm,
             rotation, primitives [{type, layer, ...}]}] and
             missing_designators
    """
    return await _simple_command("get_component_primitives", {"designators": designators},
                                 "component primitives")


@mcp.tool()
async def run_output_jobs(ctx: Context, container_names: list, outjob_path: str = "") -> str:
    """
    Run output job containers and report the files each one wrote.

    The OutJob is outjob_path (full path, opened if it is not) or else the focused OutJob, and Altium shows it
    before the run, as the scripts that generated LED240's and Elch's sets did (this tool once reported success on
    Elch's TEMPMODUL while writing nothing). Each container's output folder (the OutJob's output base path under its project's
    folder) is listed before and after its run: the container succeeds when files appeared or changed there and
    fails with the reason when none did. The OutJob is read as saved: save it in Altium first.

    Args:
        container_names (list): names of the containers to run, e.g. ["JLC"]
        outjob_path (str): full path of the .OutJob; empty for the focused one

    Returns:
        str: JSON with success, outjob_path, project, containers [{name, success, output_folder,
             files [{path, size}] (paths relative to output_folder), error}]
    """
    if not container_names or not all(isinstance(n, str) and n for n in container_names):
        return json.dumps({"success": False, "error": "container_names must be a list of container names"})
    if not outjob_path:
        response = await altium_bridge.execute_command("get_output_job_containers", {})
        found = response.get("result", {})
        if isinstance(found, str):
            try:
                found = json.loads(found)
            except ValueError:
                found = {}
        outjob_path = found.get("outjob_path", "") if response.get("success") else ""
        if not outjob_path:
            return json.dumps({"success": False, "error": "no OutJob is open in Altium: pass outjob_path ("
                               + str(found.get("error") or response.get("error", "")) + ")"})
    outjob = Path(outjob_path)
    if not outjob.is_absolute() or outjob.suffix.lower() != ".outjob" or not outjob.is_file():
        return json.dumps({"success": False, "error": f"outjob_path must be the full path of an existing .OutJob: "
                                                      f"{outjob_path}"})
    try:
        settings = parse_outjob(outjob)
        project = outjob_project(outjob)
    except ValueError as e:
        return json.dumps({"success": False, "error": str(e)})
    containers = {c["name"]: c for c in settings["containers"]}
    unknown = [n for n in container_names if n not in containers]
    if unknown:
        return json.dumps({"success": False, "error": f"{outjob.name} has no container {', '.join(unknown)}; "
                                                      f"it has {', '.join(containers) or 'none'}"})
    results = []
    for name in container_names:
        folder = output_folder(project, containers[name])
        before = snapshot(folder)
        response = await altium_bridge.execute_command(
            "run_output_jobs", {"container_names": [name], "outjob_path": str(outjob)})
        ran = response.get("result", {})
        if isinstance(ran, str):
            try:
                ran = json.loads(ran)
            except ValueError:
                ran = {}
        files = produced(before, snapshot(folder))
        for _ in range(10):                     # Altium may still be closing the last file
            if files or not response.get("success"):
                break
            await asyncio.sleep(0.5)
            files = produced(before, snapshot(folder))
        entry = {"name": name, "output_folder": str(folder), "files": files}
        mine = next((c for c in ran.get("container_results", []) if c.get("container_name") == name), {})
        if not response.get("success"):
            entry["error"] = f"the bridge failed: {response.get('error', 'unknown error')}"
        elif mine.get("error") or ran.get("error"):
            entry["error"] = mine.get("error") or ran.get("error")
        elif not files:
            outputs = [o["name"] or o["type"] for o in settings["outputs"] if name in o["connected_containers"]]
            entry["error"] = (f"Altium ran the container and nothing appeared or changed under {folder}: "
                              + (f"its outputs ({', '.join(outputs)}) wrote nothing; is each output's document "
                                 "saved and in the project, and the OutJob saved?" if outputs else
                                 "no output of the OutJob is connected to it"))
        entry["success"] = "error" not in entry
        results.append(entry)
    return json.dumps({"success": all(r["success"] for r in results), "outjob_path": str(outjob),
                       "project": str(project), "containers": results}, indent=2, ensure_ascii=False)


@mcp.tool()
async def create_pcb_footprint(ctx: Context, footprint_name: str, description: str, pads: list, courtyard_x_mm: float = 0, courtyard_y_mm: float = 0) -> str:
    """
    Create a new PCB footprint in the currently active PcbLib document.
    The PcbLib (e.g. Discrete.PcbLib) must be the focused document in Altium.

    Pad format: each element is "pad_number|x_mm|y_mm|width_mm|height_mm|shape"
                shape options: Rect (default), Round, Oval
                Coordinates are in mm relative to component origin (0,0).
                Pin 1 is indicated by a gap in the top-left silkscreen corner.

    Courtyard & silkscreen are auto-generated from pad extents + 0.25 mm margin
    unless courtyard_x_mm / courtyard_y_mm are provided explicitly (half-dimensions).

    Args:
        footprint_name (str): Footprint name as it will appear in the library
        description (str): Description string
        pads (list): List of pad definitions, e.g. ["1|-0.9|0.55|1.0|0.8|Rect", ...]
        courtyard_x_mm (float): Half-width of courtyard in mm (0 = auto)
        courtyard_y_mm (float): Half-height of courtyard in mm (0 = auto)

    Returns:
        str: JSON object with result
    """
    logger.info(f"Creating PCB footprint: {footprint_name} with {len(pads)} pads")

    response = await altium_bridge.execute_command(
        "create_pcb_footprint",
        {
            "footprint_name": footprint_name,
            "description": description,
            "pads": pads,
            "courtyard_x_mm": courtyard_x_mm,
            "courtyard_y_mm": courtyard_y_mm,
        }
    )

    if not response.get("success", False):
        error_msg = response.get("error", "Unknown error")
        logger.error(f"Error creating footprint: {error_msg}")
        return json.dumps({"success": False, "error": f"Failed to create footprint: {error_msg}"})

    result = response.get("result", {})
    logger.info(f"Footprint {footprint_name} created successfully")
    return json.dumps(result, indent=2)

@mcp.tool()
async def get_server_status(ctx: Context) -> str:
    """Get the current status of the Altium MCP server"""
    status = {
        "server": "Running",
        "altium_exe": altium_bridge.config.altium_exe_path,
        "script_path": altium_bridge.config.script_path,
        "altium_found": os.path.exists(altium_bridge.config.altium_exe_path),
        "script_found": os.path.exists(altium_bridge.config.script_path),
    }
    
    return json.dumps(status, indent=2)

if __name__ == "__main__":
    logger.info("Starting Altium MCP Server...")
    logger.info(f"Using MCP directory: {MCP_DIR}")
    
    # Initialize the directory
    MCP_DIR.mkdir(exist_ok=True)
    
    # Create the AltiumScript directory if it doesn't exist
    script_dir = MCP_DIR / "AltiumScript"
    script_dir.mkdir(exist_ok=True)
    
    # Verify configuration before starting
    if not altium_bridge.config.verify_paths():
        print("Warning: Configuration not complete. Some functionality may not work.")
    
    # Print status
    print(f"Altium executable: {altium_bridge.config.altium_exe_path}")
    print(f"Script path: {altium_bridge.config.script_path}")
    
    # Run the server
    mcp.run(transport='stdio')