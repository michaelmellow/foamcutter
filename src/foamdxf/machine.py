"""Machine tab of the slicer window: connect to grblHAL, move the machine and send the cut
(see sender.py for the protocol).

- Always visible: port, live DRO (positions, controller feed, the wire's actual speed at
  both foam faces), Start / Hold / Resume / Stop / Unlock and the progress bar.
- Controls (tabs): Program (last Preview or a .nc file, dry run, feed override 50-200 %),
  Jog (each axis, each tower, or both towers together, kept inside the profile's travel),
  Home (the owner's homing macro, editable, run like a program).
- Views (tabs): Section (the window puts its section view here, with the live position)
  and Console (the controller's messages and an MDI line).

ioSender cannot be connected at the same time (one program per COM port).
"""

import functools
import json
import math
import re
import time
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from .sender import (
    FEED_OV_RANGE,
    Event,
    Sender,
    SenderError,
    Status,
    iter_events,
    list_ports,
    program_lines,
    scale_feeds,
)
from .sender import (
    POLL_SECONDS as POLL_SECONDS_DEFAULT,
)

CONTROLLER_AXES = "XYZA"  # order of the positions in a 4-axis grblHAL status report
POLL_MS = 50
LOG_LINES = 400
DRY_RUN_FACTORS = ("2", "3", "5", "10")
JOG_STEPS = ("0.1", "1", "5", "10", "50")
SPEED_GAP_SECONDS = 1.0  # two status reports further apart than this give no speed
MDI_HISTORY = 50
MOUSE_FEED_DEFAULT = 600  # mm/min
MOUSE_FEED_MIN = 50
MOUSE_FEED_FALLBACK_MAX = 3000  # slider top until the controller's rapid rate ($110-$113) is read
FOLLOW_MS = 40  # how often the drag-follow loop looks for a chance to send the next target
FOLLOW_POLL_SECONDS = 0.1  # status interval while following the mouse (0.2 otherwise)
MACHINE_FILE = Path.home() / ".foamdxf-machine.json"  # homing macro, jog settings, MDI history
Point = tuple[float, float]
Travel = dict[str, tuple[float, float]]  # letter -> (min, max), G54 work coordinates

# The owner's homing macro (foamcutter-handoff.md section 1): drive every axis into its
# negative stop at low motor current, re-seat, back off 1 mm and zero G54 there.
DEFAULT_HOMING_MACRO = """\
M906 X200 Y200 Z200 A200            ; TMC2209 current 200 mA on all axes (live, not saved)
G91                                 ; relative mode
G1 X-300 Y-300 Z-300 A-300 F2000    ; drive 300 mm toward the negative stops; motors skip there
G4 P1                               ; wait 1 s
G1 X20 Y20 Z20 A20 F500             ; back off 20 mm
G1 X-25 Y-25 Z-25 A-25              ; push 25 mm back in to re-seat against the stops (F500)
G4 P1                               ; wait 1 s
G1 X1 Y1 Z1 A1                      ; back off 1 mm
G90                                 ; absolute mode
G10 L20 P1 X0 Y0 Z0 A0              ; G54 zero = here, 1 mm off each negative stop
M906 X500 Y500 Z500 A500            ; back to running current 500 mA
"""

STATE_COLOURS = {
    "Idle": "#1e8e3e",
    "Run": "#1a73e8",
    "Jog": "#1a73e8",
    "Home": "#1a73e8",
    "Hold": "#f9ab00",
    "Door": "#f9ab00",
    "Alarm": "#d93025",
}


def tower_positions(wpos: tuple[float, ...], letters: tuple[str, ...]) -> tuple[Point, Point] | None:
    """(axis 1, axis 2) and (axis 3, axis 4) from a status report's positions, which come in
    controller order (X Y Z A) whatever letters the profile gives the tower axes."""
    try:
        v = [wpos[CONTROLLER_AXES.index(letter)] for letter in letters[:4]]
    except (ValueError, IndexError):
        return None
    return (v[0], v[1]), (v[2], v[3])


def on_wire(towers: tuple[Point, Point], fraction: float) -> Point:
    """The wire's point at `fraction` of the way from the axes 1-2 tower to the axes 3-4 tower."""
    (x1, z1), (x2, z2) = towers
    return x1 + fraction * (x2 - x1), z1 + fraction * (z2 - z1)


def wire_speeds(
    before: Status, after: Status, letters: tuple[str, ...], faces: tuple[float, float] | None
) -> list[tuple[str, float]] | None:
    """(name, mm/min) of the wire at the root and tip faces (if `faces` gives their fractions
    of the tower distance) and at both towers, from two status reports. None if unknown."""
    dt = after.at - before.at
    if not 0 < dt <= SPEED_GAP_SECONDS:
        return None
    a, b = tower_positions(before.wpos, letters), tower_positions(after.wpos, letters)
    if a is None or b is None:
        return None
    points = [("towers 1-2", 0.0), ("3-4", 1.0)]
    if faces is not None:
        points = [("root face", faces[0]), ("tip face", faces[1]), *points]
    return [(name, math.dist(on_wire(a, f), on_wire(b, f)) / dt * 60) for name, f in points]


def jog_line(
    moves: dict[str, float], wpos: tuple[float, ...] | None, travel: Travel | None, feed: float
) -> tuple[str | None, str]:
    """(`$J=` line or None, note) for a relative jog of the given letters.

    With `travel` and the current work position, the jog is shortened so no axis leaves
    its [min, max]; all axes are shortened by the same factor, so a jog of both towers
    together keeps the wire as it was (square stays square)."""
    if feed <= 0:
        return None, "the jog feed must be more than 0"
    factor, note = 1.0, ""
    if travel is not None and wpos is not None:
        for letter, d in moves.items():
            if letter not in travel or d == 0:
                continue
            pos = wpos[CONTROLLER_AXES.index(letter)]
            lo, hi = travel[letter]
            allowed = min(max(pos + d, lo), hi) - pos
            factor = min(factor, max(0.0, allowed / d))
        if factor < 1e-6:
            return None, "at the end of the travel (profile min/max): not moved"
        if factor < 1:
            note = "shortened to stay inside the travel"
    words = " ".join(f"{letter}{d * factor:.3f}" for letter, d in moves.items())
    return f"$J=G91 G21 {words} F{feed:g}", note


def move_line(targets: dict[str, float], travel: Travel | None, feed: float) -> tuple[str | None, str]:
    """(`$J=G90` line or None, note): an absolute jog of the given letters to the given work
    positions, each kept inside its [min, max] if `travel` is given."""
    if feed <= 0:
        return None, "the jog feed must be more than 0"
    note = ""
    words = []
    for letter, value in targets.items():
        if travel is not None and letter in travel:
            lo, hi = travel[letter]
            if not lo <= value <= hi:
                value = min(max(value, lo), hi)
                note = "kept inside the travel (profile min/max)"
        words.append(f"{letter}{value:.3f}")
    return f"$J=G90 G21 {' '.join(words)} F{feed:g}", note


def tower_targets(letters: tuple[str, ...], x: float, z: float, towers: tuple[int, ...] = (0, 1)) -> dict[str, float]:
    """Work positions that put the given towers (0 = axes 1-2, 1 = axes 3-4) at (x, z)."""
    out: dict[str, float] = {}
    for t in towers:
        out[letters[2 * t]] = x
        out[letters[2 * t + 1]] = z
    return out


def load_machine_settings() -> dict[str, object]:
    try:
        data = json.loads(MACHINE_FILE.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def save_machine_settings(values: dict[str, object]) -> None:
    try:
        MACHINE_FILE.write_text(json.dumps(values, indent=2), encoding="utf-8")
    except OSError:
        pass  # a convenience only


class Program:
    """G-code to send, and where it came from."""

    def __init__(self, name: str, gcode: str, minutes: float | None = None) -> None:
        self.name = name
        self.gcode = gcode
        self.minutes = minutes
        self.lines = len(program_lines(gcode))


class MachinePanel(ttk.Frame):
    """`current_program()` gives the G-code of the last Preview, or raises ValueError with the
    reason there is none (no preview yet, form changed since). `letters()` gives the G-code
    letters of axes 1 to 4. `faces()` gives where the root and tip faces lie along the wire
    (fractions of the tower distance) for the last Preview, or None. `travel()` gives each
    letter's (min, max) from the machine profile, or None. `on_status` gets every status report
    (None when the connection ends). `views` is a notebook the window may add tabs to.
    `connect` and `ports` are for tests."""

    def __init__(
        self,
        parent: tk.Misc,
        current_program: Callable[[], Program],
        letters: Callable[[], tuple[str, str, str, str]],
        say: Callable[[str], None],
        faces: Callable[[], tuple[float, float] | None] = lambda: None,
        on_status: Callable[[Status | None], None] = lambda _s: None,
        travel: Callable[[], Travel | None] = lambda: None,
        connect: Callable[[str], Sender] = Sender.connect,
        ports: Callable[[], list[tuple[str, str]]] = list_ports,
    ) -> None:
        super().__init__(parent, padding=8)
        self.current_program = current_program
        self.letters = letters
        self.say = say
        self.faces = faces
        self.on_status = on_status
        self.travel = travel
        self._connect = connect
        self._ports = ports
        self.sender: Sender | None = None
        self.file_program: Program | None = None  # a .nc picked with "Open file", else the preview
        self.port_var = tk.StringVar()
        self.source = tk.StringVar(value="preview")
        self.dry_run = tk.BooleanVar(value=False)  # not remembered: every session starts with real cuts
        self.dry_factor = tk.StringVar(value="3")
        self.feed_ov = tk.IntVar(value=100)
        saved = load_machine_settings()
        self.settings = saved
        self.jog_step = tk.StringVar(value=str(saved.get("jog_step", "1")))
        self.jog_feed = tk.StringVar(value=str(saved.get("jog_feed", "600")))
        self.jog_clamp = tk.BooleanVar(value=bool(saved.get("jog_clamp", True)))
        try:
            mouse_feed = int(float(str(saved.get("mouse_feed", MOUSE_FEED_DEFAULT))))
        except ValueError:
            mouse_feed = MOUSE_FEED_DEFAULT
        self.mouse_feed = tk.IntVar(value=mouse_feed)  # section view: speed of click and drag moves, mm/min
        history = saved.get("mdi_history", [])
        self.mdi_history: list[str] = [str(h) for h in history] if isinstance(history, list) else []
        self._mdi_index = len(self.mdi_history)
        self._ov_touched = -1e9  # time the operator last moved the override slider
        self._last_status: Status | None = None
        self._error_held = False
        self._started = 0.0
        self._is_cut = False  # the running program is a cut (not a macro or an edge move)
        self._block_name = ""  # name of the running program, macro or move
        # called with (name, finished, message) when a run_block program ends: finished is
        # False if it was stopped (Stop, error, alarm, reset or lost connection)
        self.block_listeners: list[Callable[[str, bool, str], None]] = []
        self.jog_buttons: list[ttk.Button] = []
        self._following = False
        self._follow_target: dict[str, float] | None = None
        self._follow_sent: dict[str, float] | None = None
        self._follow_sent_at = 0.0
        self._build()
        macro = saved.get("homing_macro")
        self.macro_text.insert("1.0", macro if isinstance(macro, str) and macro.strip() else DEFAULT_HOMING_MACRO)
        self.refresh_ports()
        self._update_buttons()

    # --- layout ---------------------------------------------------------------------------

    def _build(self) -> None:
        self._build_connection()
        self._build_dro()
        self._build_run_bar()
        paned = ttk.PanedWindow(self, orient=tk.VERTICAL)
        paned.pack(fill=tk.BOTH, expand=True, pady=(8, 0))
        self.controls = ttk.Notebook(paned)
        self.views = ttk.Notebook(paned)
        paned.add(self.controls, weight=0)
        paned.add(self.views, weight=1)
        self._build_program_tab()
        self._build_jog_tab()
        self._build_home_tab()
        self._build_console()

    def _build_connection(self) -> None:
        conn = ttk.Frame(self)
        conn.pack(fill=tk.X)
        ttk.Label(conn, text="Port").pack(side=tk.LEFT)
        self.port_box = ttk.Combobox(conn, textvariable=self.port_var, width=34, state="readonly")
        self.port_box.pack(side=tk.LEFT, padx=6)
        ttk.Button(conn, text="Refresh", command=self.refresh_ports).pack(side=tk.LEFT)
        self.connect_text = tk.StringVar(value="Connect")
        self.connect_button = ttk.Button(conn, textvariable=self.connect_text, width=11, command=self.toggle_connection)
        self.connect_button.pack(side=tk.LEFT, padx=6)
        self.conn_note = tk.StringVar(value="Not connected. One program per port: disconnect ioSender first.")
        ttk.Label(conn, textvariable=self.conn_note, foreground="#5f6368").pack(side=tk.LEFT, padx=6)

    def _build_dro(self) -> None:
        dro = tk.Frame(self, background="#202124", padx=12, pady=6)
        dro.pack(fill=tk.X, pady=(8, 0))
        big, small = ("Consolas", 16, "bold"), ("Consolas", 10)
        self.axis_vars: list[tuple[tk.StringVar, tk.StringVar, tk.StringVar]] = []
        self.state_label = tk.Label(dro, text="OFFLINE", font=("Segoe UI", 12, "bold"), fg="white",
                                    bg="#5f6368", width=9, pady=2)
        self.state_label.grid(row=0, column=0, sticky="nw", padx=(0, 18))
        for i in range(4):
            cell = tk.Frame(dro, background="#202124")
            cell.grid(row=0, column=i + 1, sticky="w", padx=(0, 18))
            letter = tk.StringVar(value=CONTROLLER_AXES[i])
            work, mach = tk.StringVar(value="   ---.---"), tk.StringVar(value="")
            line = tk.Frame(cell, background="#202124")
            line.pack(anchor="w")
            tk.Label(line, textvariable=letter, font=big, fg="#8ab4f8", bg="#202124").pack(side=tk.LEFT)
            tk.Label(line, textvariable=work, font=big, fg="#e8eaed", bg="#202124").pack(side=tk.LEFT)
            tk.Label(cell, textvariable=mach, font=small, fg="#9aa0a6", bg="#202124").pack(anchor="w")
            self.axis_vars.append((letter, work, mach))
        self.dro_detail = tk.StringVar(value="work position (large), machine position (small), mm")
        tk.Label(dro, textvariable=self.dro_detail, font=small, fg="#bdc1c6", bg="#202124").grid(
            row=1, column=0, columnspan=5, sticky="w", pady=(4, 0))
        self.dro_speed = tk.StringVar(value="")
        tk.Label(dro, textvariable=self.dro_speed, font=("Consolas", 11, "bold"), fg="#81c995", bg="#202124").grid(
            row=2, column=0, columnspan=5, sticky="w")

    def _build_run_bar(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill=tk.X, pady=(8, 0))
        self.start_button = ttk.Button(bar, text="Start", width=14, command=self.start)
        self.hold_button = ttk.Button(bar, text="Hold", width=9, command=self.hold)
        self.resume_button = ttk.Button(bar, text="Resume", width=9, command=self.resume)
        self.stop_button = ttk.Button(bar, text="Stop", width=9, command=self.stop)
        self.unlock_button = ttk.Button(bar, text="Unlock ($X)", width=12, command=self.unlock)
        for b in (self.start_button, self.hold_button, self.resume_button, self.stop_button, self.unlock_button):
            b.pack(side=tk.LEFT, padx=(0, 6))
        self.progress = ttk.Progressbar(bar, mode="determinate", maximum=1)
        self.progress.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))
        self.progress_note = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.progress_note).pack(anchor="w")

    def _build_program_tab(self) -> None:
        prog = ttk.Frame(self.controls, padding=8)
        self.controls.add(prog, text="Program")
        ttk.Radiobutton(prog, text="Last Preview", value="preview", variable=self.source,
                        command=self._show_program).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(prog, text="G-code file", value="file", variable=self.source,
                        command=self._show_program).grid(row=0, column=1, sticky="w", padx=(12, 0))
        ttk.Button(prog, text="Open .nc file…", command=self.open_file).grid(row=0, column=2, padx=8)
        self.program_note = tk.StringVar(value="")
        ttk.Label(prog, textvariable=self.program_note, foreground="#5f6368").grid(
            row=1, column=0, columnspan=6, sticky="w", pady=(4, 0))
        dry = ttk.Frame(prog)
        dry.grid(row=2, column=0, columnspan=6, sticky="w", pady=(6, 0))
        ttk.Checkbutton(dry, text="Dry run (wire cold, no foam) at", variable=self.dry_run,
                        command=self._show_program).pack(side=tk.LEFT)
        factor = ttk.Combobox(dry, textvariable=self.dry_factor, values=DRY_RUN_FACTORS, width=4, state="readonly")
        factor.pack(side=tk.LEFT, padx=4)
        factor.bind("<<ComboboxSelected>>", lambda _e: self._show_program())
        ttk.Label(dry, text="x the program's speed (the machine's max rates still apply)",
                  foreground="#5f6368").pack(side=tk.LEFT)
        ov = ttk.Frame(prog)
        ov.grid(row=3, column=0, columnspan=6, sticky="w", pady=(6, 0))
        ttk.Label(ov, text="Feed override").pack(side=tk.LEFT)
        lo, hi = FEED_OV_RANGE
        self.ov_scale = tk.Scale(ov, from_=lo, to=hi, resolution=5, orient=tk.HORIZONTAL, length=300,
                                 variable=self.feed_ov, showvalue=True, command=self._override_moved)
        self.ov_scale.pack(side=tk.LEFT, padx=6)
        ttk.Label(ov, text="%").pack(side=tk.LEFT)
        self.ov_reset = ttk.Button(ov, text="100 %", width=7, command=lambda: self._override_to(100))
        self.ov_reset.pack(side=tk.LEFT, padx=8)
        ttk.Label(ov, text="Acts at once. Kerf was compensated for 100 %.", foreground="#5f6368").pack(side=tk.LEFT)
        ttk.Label(prog, text="Wire heat is switched by hand: the controller does not control it. "
                             "Stop holds first, then resets once the wire has stopped.",
                  foreground="#5f6368").grid(row=4, column=0, columnspan=6, sticky="w", pady=(6, 0))

    def _build_jog_tab(self) -> None:
        jog = ttk.Frame(self.controls, padding=8)
        self.controls.add(jog, text="Jog")
        top = ttk.Frame(jog)
        top.grid(row=0, column=0, columnspan=6, sticky="w")
        ttk.Label(top, text="Step").pack(side=tk.LEFT)
        ttk.Combobox(top, textvariable=self.jog_step, values=JOG_STEPS, width=5).pack(side=tk.LEFT, padx=4)
        ttk.Label(top, text="mm    Feed").pack(side=tk.LEFT)
        ttk.Entry(top, textvariable=self.jog_feed, width=7).pack(side=tk.LEFT, padx=4)
        ttk.Label(top, text="mm/min").pack(side=tk.LEFT)
        ttk.Checkbutton(top, text="Keep inside the profile's travel", variable=self.jog_clamp).pack(
            side=tk.LEFT, padx=(14, 0))
        self.stop_jog_button = ttk.Button(top, text="Stop jog", command=self.cancel_jog)
        self.stop_jog_button.pack(side=tk.LEFT, padx=(14, 0))

        ttk.Label(jog, text="Horizontal (+ = away)").grid(row=1, column=1, columnspan=2, pady=(8, 2))
        ttk.Label(jog, text="Vertical (+ = up)").grid(row=1, column=3, columnspan=2, pady=(8, 2))
        self.jog_rows: list[tk.StringVar] = []
        for row, (axes_h, axes_v) in enumerate((((0, 2), (1, 3)), ((0,), (1,)), ((2,), (3,))), start=2):
            name = tk.StringVar()
            self.jog_rows.append(name)
            ttk.Label(jog, textvariable=name, width=24).grid(row=row, column=0, sticky="w")
            for col, (axes, sign) in enumerate(((axes_h, -1), (axes_h, 1), (axes_v, -1), (axes_v, 1)), start=1):
                b = ttk.Button(jog, text="-" if sign < 0 else "+", width=6,
                               command=functools.partial(self.jog, axes, sign))
                b.grid(row=row, column=col, padx=2, pady=1)
                self.jog_buttons.append(b)
        self.jog_note = tk.StringVar(value="")
        ttk.Label(jog, textvariable=self.jog_note, foreground="#5f6368").grid(
            row=5, column=0, columnspan=6, sticky="w", pady=(6, 0))
        self._name_jog_rows()

    def _build_home_tab(self) -> None:
        home = ttk.Frame(self.controls, padding=8)
        self.controls.add(home, text="Home")
        ttk.Label(home, foreground="#5f6368", justify="left", text=(
            "Drives every axis into its stop at low current, then zeroes G54 1 mm off the stops.\n"
            "Clear the table and keep the wire cold: all four axes move at once and the verticals go DOWN.")
        ).pack(anchor="w")
        body = ttk.Frame(home)
        body.pack(fill=tk.BOTH, expand=True, pady=(4, 0))
        self.macro_text = tk.Text(body, height=7, wrap="none", font=("Consolas", 9), undo=True)
        self.macro_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        buttons = ttk.Frame(body)
        buttons.pack(side=tk.LEFT, fill=tk.Y, padx=(8, 0))
        self.home_button = ttk.Button(buttons, text="Home now", width=16, command=self.home)
        self.home_button.pack(pady=(0, 4))
        ttk.Button(buttons, text="Restore default", width=16, command=self.restore_macro).pack()

    def _build_console(self) -> None:
        console = ttk.Frame(self.views)
        self.views.add(console, text="Console")
        self.console = console
        mdi = ttk.Frame(console)
        mdi.pack(side=tk.BOTTOM, fill=tk.X, pady=(4, 0))
        ttk.Label(mdi, text="MDI").pack(side=tk.LEFT)
        self.mdi_var = tk.StringVar()
        self.mdi_entry = ttk.Entry(mdi, textvariable=self.mdi_var, font=("Consolas", 10))
        self.mdi_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        self.mdi_entry.bind("<Return>", lambda _e: self.send_mdi())
        self.mdi_entry.bind("<Up>", lambda _e: self._mdi_recall(-1))
        self.mdi_entry.bind("<Down>", lambda _e: self._mdi_recall(1))
        self.mdi_button = ttk.Button(mdi, text="Send", command=self.send_mdi)
        self.mdi_button.pack(side=tk.LEFT)
        logf = ttk.Frame(console)
        logf.pack(fill=tk.BOTH, expand=True)
        self.log = tk.Text(logf, height=8, wrap="none", font=("Consolas", 9), state="disabled")
        scroll = ttk.Scrollbar(logf, orient=tk.VERTICAL, command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)
        self.log.pack(fill=tk.BOTH, expand=True)

    # --- connection -----------------------------------------------------------------------

    def refresh_ports(self) -> None:
        try:
            ports = self._ports()
        except Exception as error:  # noqa: BLE001 - pyserial missing or the OS refused; shown, not fatal
            ports = []
            self.conn_note.set(f"Cannot list serial ports: {error}")
        self.port_box.configure(values=[f"{dev}  {desc}" for dev, desc in ports])
        current = self.port_var.get().split(" ")[0]
        names = [dev for dev, _ in ports]
        if current not in names:
            # the board's USB port first (an STM32 virtual COM port), not e.g. Bluetooth serial links
            board = [p for p in ports if re.search(r"STM|USB|CDC|Virtual COM", p[1], re.IGNORECASE)]
            pick = (board or ports)[:1]
            self.port_var.set(f"{pick[0][0]}  {pick[0][1]}" if pick else "")

    def toggle_connection(self) -> None:
        if self.sender is None:
            self.connect()
        else:
            self.disconnect()

    def connect(self) -> None:
        device = self.port_var.get().split(" ")[0]
        if not device:
            messagebox.showwarning("No port", "Plug in the board's USB cable, press Refresh and pick its port.")
            return
        try:
            self.sender = self._connect(device)
        except SenderError as error:
            messagebox.showerror("Cannot connect", str(error))
            return
        self.conn_note.set(f"Connecting to {device}…")
        self._write_log(f"--- {device} opened")
        self._update_buttons()
        self.after(POLL_MS, self._poll)

    def disconnect(self) -> bool:
        """Release the port. Asks first if a program is running. Returns False if kept."""
        self._save_settings()
        if self.sender is None:
            return True
        if self.sender.running and not messagebox.askyesno(
            "Program running", "A program is running. Stop it (hold, then reset) and disconnect?"
        ):
            return False
        sender, self.sender = self.sender, None
        sender.close()
        for event in iter_events(sender):
            self._handle(event)
        self._offline("Disconnected. The port is free for ioSender.")
        return True

    def _offline(self, note: str) -> None:
        self.sender = None
        self._following = False
        self._last_status = None
        self.dro_speed.set("")
        self.feed_ov.set(100)
        self.on_status(None)
        self.conn_note.set(note)
        self.state_label.configure(text="OFFLINE", bg="#5f6368")
        self._update_buttons()

    def _save_settings(self) -> None:
        self.settings.update({
            "homing_macro": self.macro_text.get("1.0", "end-1c"),
            "jog_step": self.jog_step.get(),
            "jog_feed": self.jog_feed.get(),
            "jog_clamp": self.jog_clamp.get(),
            "mouse_feed": self.mouse_feed.get(),
            "mdi_history": self.mdi_history[-MDI_HISTORY:],
        })
        save_machine_settings(self.settings)

    # --- running G-code (program, homing macro, edge moves) -------------------------------

    def run_block(self, name: str, gcode: str, title: str, checks: str, cut: bool = False) -> bool:
        """Confirm, then stream `gcode` like a program (character counting, stop on error,
        Hold/Stop). Returns True if it started."""
        if self.sender is None or self.sender.status is None:
            messagebox.showwarning("Not connected", "Connect to the machine on the Machine tab first.")
            return False
        lines = len(program_lines(gcode))
        if not messagebox.askokcancel(title, f"{name} ({lines} lines)\n\n{checks}", icon="warning"):
            return False
        try:
            self.sender.run_program(gcode, label=name)
        except SenderError as error:
            messagebox.showerror("Cannot start", str(error).capitalize() + ".")
            return False
        self._error_held = False
        self._is_cut = cut
        self._block_name = name
        self._started = time.monotonic()
        self.progress.configure(maximum=max(1, lines), value=0)
        self.progress_note.set(f"Sending {name}…")
        self._write_log(f"--- start {name}, {lines} lines")
        self.say(f"Running {name}. Hold or Stop on the Machine tab.")
        self.after(POLL_MS * 2, self._update_buttons)
        return True

    def open_file(self) -> None:
        path = filedialog.askopenfilename(title="G-code file", filetypes=[("G-code", "*.nc *.gcode *.ngc *.tap"),
                                                                           ("All files", "*.*")])
        if path:
            self.load_file(Path(path))

    def load_file(self, path: Path) -> None:
        try:
            self.file_program = Program(path.name, path.read_text(encoding="utf-8", errors="replace"))
        except OSError as error:
            messagebox.showerror("Cannot read the file", str(error))
            return
        self.source.set("file")
        self._show_program()

    def program(self) -> Program:
        """The program Start would send. Raises ValueError with the reason if there is none."""
        if self.source.get() == "file":
            if self.file_program is None:
                raise ValueError("open a G-code file first")
            p = self.file_program
        else:
            p = self.current_program()
        factor = self.dry_factor_value()
        if factor == 1:
            return p
        minutes = p.minutes / factor if p.minutes is not None else None  # rapids unchanged: at least this
        return Program(f"{p.name} (DRY RUN x{factor:g})", scale_feeds(p.gcode, factor), minutes)

    def dry_factor_value(self) -> float:
        if not self.dry_run.get():
            return 1.0
        try:
            return max(1.0, float(self.dry_factor.get()))
        except ValueError:
            return 1.0

    def _show_program(self) -> None:
        try:
            p = self.program()
        except ValueError as why:
            self.program_note.set(f"Nothing to send: {why}.")
        else:
            minutes = f", cut time {p.minutes:.1f} min" if p.minutes is not None else ""
            self.program_note.set(f"{p.name}: {p.lines} lines{minutes}")
        self.start_button.configure(text="Start DRY RUN" if self.dry_run.get() else "Start")
        self._update_buttons()

    def refresh(self) -> None:
        """Called by the window after a Preview or a form change."""
        self._show_program()
        self._name_jog_rows()

    def start(self) -> None:
        try:
            p = self.program()
        except ValueError as why:
            messagebox.showwarning("Nothing to send", str(why).capitalize() + ".")
            return
        minutes = f"About {p.minutes:.1f} min. " if p.minutes is not None else ""
        if self.dry_run.get():
            title, checks = "Start the DRY RUN", (
                f"{minutes}DRY RUN: every cutting move {self.dry_factor_value():g} times faster.\n\n"
                "Check before you press OK:\n"
                "  - the wire heat is OFF\n"
                "  - there is NO foam on the table\n"
                "  - the work zero is the one the program was made for\n"
                "  - nothing is in the wire's path\n\n")
        else:
            title, checks = "Start the cut", (
                f"{minutes}Check before you press OK:\n"
                "  - the stock is in place and aligned (Align stock tab)\n"
                "  - the work zero is the one the program was made for\n"
                "  - the wire heat is ON (the controller does not switch it)\n"
                "  - nothing is in the wire's path\n\n")
        self.run_block(p.name, p.gcode, title, checks + "Hold pauses the wire. Stop holds, then resets.",
                       cut=not self.dry_run.get())

    def home(self) -> None:
        macro = self.macro_text.get("1.0", "end-1c")
        if not program_lines(macro):
            messagebox.showwarning("Empty macro", "The homing macro has no G-code lines. Restore the default.")
            return
        self._save_settings()
        self.run_block("homing macro", macro, "Home all axes", (
            "Check before you press OK:\n"
            "  - the wire heat is OFF\n"
            "  - the table is clear: the wire moves down and back to the corner\n"
            "  - every axis is less than 300 mm from its stop (the macro's seek distance)\n\n"
            "Afterwards check that every carriage is 1 mm off its stop."))

    def restore_macro(self) -> None:
        self.macro_text.delete("1.0", tk.END)
        self.macro_text.insert("1.0", DEFAULT_HOMING_MACRO)
        self._save_settings()

    def hold(self) -> None:
        if self.sender is not None:
            self.sender.hold()

    def resume(self) -> None:
        if self.sender is not None and not self._error_held:
            self.sender.resume()

    def stop(self) -> None:
        if self.sender is not None:
            self.sender.stop()

    def unlock(self) -> None:
        self._command("$X")

    def _command(self, line: str) -> bool:
        if self.sender is None:
            return False
        try:
            self.sender.command(line)
        except SenderError as error:
            messagebox.showwarning("Not now", str(error).capitalize() + ".")
            return False
        return True

    def _override_moved(self, value: str) -> None:
        self._ov_touched = time.monotonic()
        self._override_to(int(float(value)))

    def _override_to(self, percent: int) -> None:
        self.feed_ov.set(percent)
        if self.sender is not None and percent != self.sender.feed_override:
            self.sender.set_feed_override(percent)

    # --- jog and MDI ----------------------------------------------------------------------

    def _name_jog_rows(self) -> None:
        l1, l2, l3, l4 = self.letters()
        names = (f"Both towers ({l1}+{l3} / {l2}+{l4})", f"Tower axes 1-2 ({l1} / {l2})",
                 f"Tower axes 3-4 ({l3} / {l4})")
        for var, text in zip(self.jog_rows, names, strict=True):
            var.set(text)

    def jog(self, axes: tuple[int, ...], sign: int) -> None:
        """Jog tower axes (0-based axis numbers) by one step in direction `sign`."""
        if self.sender is None:
            return
        try:
            step, feed = float(self.jog_step.get().replace(",", ".")), float(self.jog_feed.get().replace(",", "."))
        except ValueError:
            self.jog_note.set("Step and feed must be numbers.")
            return
        letters = self.letters()
        moves = {letters[a]: sign * abs(step) for a in axes}
        travel = self.travel() if self.jog_clamp.get() else None
        if self.jog_clamp.get() and travel is None:
            self.jog_note.set("Cannot read the profile's travel: not moved. Untick the box to jog anyway.")
            return
        wpos = self.sender.status.wpos if self.sender.status is not None else None
        line, note = jog_line(moves, wpos, travel, feed)
        self.jog_note.set(note)
        if line is not None and self._command(line):
            self.settings.update({"jog_step": self.jog_step.get(), "jog_feed": self.jog_feed.get()})

    # --- moves from the section view (absolute jogs) --------------------------------------

    def can_move(self) -> bool:
        """Connected, no program running, and Idle or jogging."""
        s = self.sender
        return (s is not None and s.status is not None and not s.running
                and s.status.state in ("Idle", "Jog"))

    def rapid_rate(self) -> float | None:
        """The controller's rapid rate (slowest of $110-$113), once read after connecting."""
        return self.sender.rapid_rate() if self.sender is not None else None

    def mouse_feed_value(self) -> float:
        """Feed of the section view's mouse moves: the speed slider, never above the rapid rate."""
        feed = float(self.mouse_feed.get())
        rapid = self.rapid_rate()
        return min(feed, rapid) if rapid is not None else feed

    def move_to(self, targets: dict[str, float]) -> bool:
        """Jog the given letters to absolute work positions at the mouse speed. True if sent."""
        if self.sender is None or not self.can_move():
            return False
        feed = self.mouse_feed_value()
        travel = self.travel() if self.jog_clamp.get() else None
        if self.jog_clamp.get() and travel is None:
            self.jog_note.set("Cannot read the profile's travel: not moved. Untick the box on the Jog tab to move anyway.")
            return False
        line, note = move_line(targets, travel, feed)
        self.jog_note.set(note)
        if line is None:
            return False
        try:
            self.sender.jog(line)
        except SenderError as error:
            self.jog_note.set(str(error))
            return False
        return True

    def follow(self, targets: dict[str, float]) -> None:
        """Keep the towers on a moving target (mouse drag): the newest target is sent each time
        the controller has answered the last jog and its planner is nearly empty, so moves do
        not pile up behind the mouse."""
        self._follow_target = targets
        if not self._following:
            self._following = True
            self._follow_sent = None
            if self.sender is not None:
                self.sender.poll_seconds = FOLLOW_POLL_SECONDS
            self._follow_tick()

    def end_follow(self) -> None:
        """Mouse released: send the last target if it was not sent yet, and stop following."""
        if not self._following:
            return
        self._following = False
        if self.sender is not None:
            self.sender.poll_seconds = POLL_SECONDS_DEFAULT
        if self._follow_target is not None and self._follow_target != self._follow_sent:
            self.move_to(self._follow_target)

    def _follow_ready(self) -> bool:
        s = self.sender
        if s is None or s.status is None or s.pending:
            return False
        if self._follow_sent is not None and s.status.at <= self._follow_sent_at:
            return False  # wait for a report made after the last jog
        if s.status.buffer is not None and s.planner_size is not None:
            return s.status.buffer[0] >= s.planner_size - 1  # at most the running block queued
        return True

    def _follow_tick(self) -> None:
        if not self._following:
            return
        target = self._follow_target
        if target is not None and target != self._follow_sent and self._follow_ready() and self.move_to(target):
            self._follow_sent = dict(target)
            self._follow_sent_at = time.monotonic()
        self.after(FOLLOW_MS, self._follow_tick)

    def cancel_jog(self) -> None:
        if self.sender is not None:
            self.sender.cancel_jog()

    def send_mdi(self) -> None:
        line = self.mdi_var.get().strip()
        if not line or not self._command(line):
            return
        if not self.mdi_history or self.mdi_history[-1] != line:
            self.mdi_history.append(line)
            del self.mdi_history[:-MDI_HISTORY]
        self._mdi_index = len(self.mdi_history)
        self.mdi_var.set("")

    def _mdi_recall(self, step: int) -> None:
        if not self.mdi_history:
            return
        self._mdi_index = min(max(self._mdi_index + step, 0), len(self.mdi_history))
        self.mdi_var.set(self.mdi_history[self._mdi_index] if self._mdi_index < len(self.mdi_history) else "")
        self.mdi_entry.icursor(tk.END)

    # --- events ---------------------------------------------------------------------------

    def _poll(self) -> None:
        sender = self.sender
        if sender is None:
            return
        for event in iter_events(sender):
            self._handle(event)
            if self.sender is None:
                return
        self.after(POLL_MS, self._poll)

    def _handle(self, e: Event) -> None:
        if e.kind == "status" and e.status is not None:
            self._show_status(e.status)
        elif e.kind == "connected":
            self.conn_note.set(f"Connected to {e.text}. Only this window can use the port now.")
            self._write_log(f"--- connected to {e.text}")
            self._update_buttons()
        elif e.kind == "lost":
            self._write_log(f"--- {e.text}")
            messagebox.showerror("Connection", e.text.capitalize() + ".")
        elif e.kind == "disconnected":
            if self.sender is not None:
                self.sender.close()
            self._offline("Disconnected.")
        elif e.kind in ("log", "sent"):
            self._write_log(("> " if e.kind == "sent" else "") + e.text)
        elif e.kind == "progress":
            self._show_progress(e.done, e.total)
        elif e.kind == "done":
            self._show_progress(e.done, e.total)
            self.progress_note.set(f"Finished: {e.text}." + (" Switch the wire heat off." if self._is_cut else ""))
            self._write_log(f"--- {e.text}")
            self.say(f"Finished: {e.text}.")
            self._update_buttons()
            self._block_ended(True, e.text)
        elif e.kind == "error":
            self._error_held = True
            self._write_log(f"--- {e.text}")
            self.progress_note.set(e.text)
            self._update_buttons()
            messagebox.showerror("Controller error", f"{e.text}\n\nThe rest of the program was not sent, because a "
                                 "missing move would cut the wrong shape. Press Stop.")
        elif e.kind == "stopped":
            self._error_held = False
            self.progress_note.set(f"Stopped at {e.done} of {e.total} lines: {e.text}.")
            self._write_log(f"--- {e.text}")
            self.say(f"Stopped: {e.text}.")
            self._update_buttons()
            self._block_ended(False, e.text)

    def _block_ended(self, finished: bool, message: str) -> None:
        name, self._block_name = self._block_name, ""
        for listener in list(self.block_listeners):
            listener(name, finished, message)

    def work_position(self) -> dict[str, float] | None:
        """Work position of each controller axis by letter, from the last status report."""
        s = self.sender
        if s is None or s.status is None or len(s.status.wpos) < len(CONTROLLER_AXES):
            return None
        return dict(zip(CONTROLLER_AXES, s.status.wpos, strict=False))

    def reset_override(self) -> bool:
        """Feed override back to 100 %. True if it was not 100 % (and was reset)."""
        if self.sender is not None and self.sender.feed_override != 100:
            self._override_to(100)
            return True
        return False

    def _show_status(self, st: Status) -> None:
        # the report lists the controller's axes in its own order (X Y Z A), whatever the profile maps
        # them to; the small line names the tower axis each one drives
        letters = self.letters()
        for i, (letter, work, mach) in enumerate(self.axis_vars):
            name = CONTROLLER_AXES[i]
            letter.set(name)
            tower = f"  axis {letters.index(name) + 1}" if name in letters else ""
            if i < len(st.wpos):
                work.set(f"{st.wpos[i]:9.3f}")
                mach.set(f"{tower}  mach {st.mpos[i]:.3f}")
        base = st.state.split(":")[0]
        self.state_label.configure(text=st.state.upper(), bg=STATE_COLOURS.get(base, "#5f6368"))
        parts = []
        if st.feed is not None:
            parts.append(f"controller feed {st.feed:g} mm/min")
        if st.overrides:
            parts.append(f"override {st.overrides[0]} %")
            # follow the controller (e.g. after a reset) unless the operator is moving the slider
            if time.monotonic() - self._ov_touched > 1.0 and self.feed_ov.get() != st.overrides[0]:
                self.feed_ov.set(st.overrides[0])
        if st.buffer is not None:
            parts.append(f"planner free {st.buffer[0]}  rx free {st.buffer[1]}")
        if st.pins:
            parts.append(f"inputs active: {st.pins}")
        self.dro_detail.set("     ".join(parts) or "work position (large), machine position (small), mm")
        speeds = None
        if self._last_status is not None:
            faces = self.faces() if self.source.get() == "preview" else None
            speeds = wire_speeds(self._last_status, st, letters, faces)
        self._last_status = st
        if speeds is not None:
            self.dro_speed.set("wire speed  " + "   ".join(f"{name} {v:6.1f}" for name, v in speeds) + "  mm/min")
        elif base == "Idle":
            self.dro_speed.set("")
        self.on_status(st)
        self._update_buttons()

    def _show_progress(self, done: int, total: int) -> None:
        self.progress.configure(maximum=max(1, total), value=done)
        elapsed = time.monotonic() - self._started
        self.progress_note.set(f"{done} / {total} lines   {int(elapsed // 60)}:{int(elapsed % 60):02d} elapsed")

    def _write_log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert(tk.END, text + "\n")
        extra = int(self.log.index("end-1c").split(".")[0]) - LOG_LINES
        if extra > 0:
            self.log.delete("1.0", f"{extra + 1}.0")
        self.log.see(tk.END)
        self.log.configure(state="disabled")

    def _update_buttons(self) -> None:
        s = self.sender
        online = s is not None and s.status is not None
        running = s is not None and s.running
        try:
            self.program()
            has_program = True
        except ValueError:
            has_program = False
        state = s.status.state if s is not None and s.status is not None else ""
        idle = online and state == "Idle"

        def enable(button: ttk.Button, on: bool) -> None:
            button.state(["!disabled"] if on else ["disabled"])

        enable(self.start_button, bool(idle and not running and has_program))
        enable(self.hold_button, online)
        enable(self.resume_button, online and not self._error_held)
        enable(self.stop_button, online)
        enable(self.unlock_button, online and not running)
        enable(self.home_button, idle and not running)
        enable(self.mdi_button, online and not running)
        for b in self.jog_buttons:
            enable(b, online and not running and state in ("Idle", "Jog"))
        enable(self.stop_jog_button, online and state == "Jog")
        self.ov_scale.configure(state="normal" if online else "disabled")
        enable(self.ov_reset, online)
        enable(self.connect_button, not running)
        self.connect_text.set("Connect" if s is None else "Disconnect")
        self.port_box.configure(state="disabled" if s is not None else "readonly")
