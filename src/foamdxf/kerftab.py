"""Kerf tab of the Machine tab: cut test slots at several speeds, enter their widths and save
them as a kerf table in the machine profile (logic and programs in kerfcal.py).

Sequence for each slot (the tab waits at every step, so the scrap block can be moved or
replaced and the wire re-positioned with Jog or the section view between slots):

    ready ──Cut──▶ cutting ──done──▶ cooling ──Pull out (heat off, cold)──▶ pulling ──done──▶ next slot
"""

import datetime
import shutil
import tkinter as tk
from collections.abc import Callable
from pathlib import Path
from tkinter import messagebox, ttk

from foamcam.profile import Profile

from .kerfcal import (
    DIRECTIONS,
    OFFSETS,
    Direction,
    append_kerf_table,
    calibration_speeds,
    cut_move,
    direction,
    mean_width,
    parse_speeds,
    pullout_program,
    slot_end_inside,
    slot_program,
    step_move,
    straight_wire,
)
from .machine import MachinePanel

MAX_SLOTS = 10
REFRESH_MS = 300
CUT_NAME, PULL_NAME = "kerf slot", "kerf pull-out"


def _num(text: str) -> float | None:
    try:
        return float(text.strip().replace(",", "."))
    except ValueError:
        return None


class KerfPanel(ttk.Frame):
    """`profile()` gives the machine profile in the form (or None), `target()` the form's cut
    speed text, `saved(name)` is called after a new table was written to the profile."""

    def __init__(
        self,
        parent: tk.Misc,
        machine: MachinePanel,
        profile: Callable[[], Profile | None],
        profile_path: Callable[[], Path],
        target: Callable[[], str],
        saved: Callable[[str], None] = lambda _n: None,
    ) -> None:
        super().__init__(parent, padding=8)
        self.machine = machine
        self.profile = profile
        self.profile_path = profile_path
        self.target_text = target
        self.saved = saved
        st = machine.settings
        self.target = tk.StringVar(value=target())
        self.count = tk.StringVar(value=str(st.get("kerf_slots", 5)))
        self.speeds_text = tk.StringVar()
        self.direction_label = tk.StringVar()
        self.depth = tk.StringVar(value=str(st.get("kerf_depth", 30)))
        self.offset_label = tk.StringVar()
        self.spacing = tk.StringVar(value=str(st.get("kerf_spacing", 10)))
        self.pullout = tk.StringVar(value=str(st.get("kerf_pullout", 200)))
        today = datetime.date.today().isoformat()
        self.table_name = tk.StringVar(value=f"measured-{today}")
        self.table_desc = tk.StringVar(value=f"kerf calibration {today}: foam ?, wire supply ?")
        self.note = tk.StringVar(value="Set the target speed, then Start. Place the wire beside the scrap block "
                                      "with Jog or by clicking the section view.")
        self.speeds: list[float] = []
        self.states: list[str] = []  # per slot: "", "cut", "skipped", "stopped"
        self.current = 0
        self.phase = "idle"  # idle, ready, cutting, cooling, pulling, done
        self.rows: list[tuple[tk.StringVar, tk.StringVar, tk.StringVar, tk.StringVar]] = []
        self._build()
        saved_dir = str(st.get("kerf_direction", "h-"))
        self._direction_labels()
        keys = [d.key for d in DIRECTIONS]
        self.direction_label.set(self.dir_box.cget("values")[keys.index(saved_dir) if saved_dir in keys else 1])
        self._offset_labels(str(st.get("kerf_offset", "")))
        self.generate()
        machine.block_listeners.append(self._block_ended)
        self.after(REFRESH_MS, self._refresh)

    # --- layout ---------------------------------------------------------------------------

    def _build(self) -> None:
        left = ttk.Frame(self)
        left.pack(side=tk.LEFT, fill=tk.Y)
        right = ttk.Frame(self)
        right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(16, 0))

        r = 0
        ttk.Label(left, text="Target speed").grid(row=r, column=0, sticky="w")
        ttk.Entry(left, textvariable=self.target, width=7).grid(row=r, column=1, sticky="w")
        ttk.Label(left, text="mm/min    Slots").grid(row=r, column=2, sticky="w")
        ttk.Spinbox(left, from_=2, to=MAX_SLOTS, textvariable=self.count, width=4).grid(row=r, column=3, sticky="w")
        ttk.Button(left, text="Generate", command=self.generate).grid(row=r, column=4, padx=(6, 0))
        r += 1
        ttk.Label(left, text="Speeds").grid(row=r, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(left, textvariable=self.speeds_text, width=34).grid(row=r, column=1, columnspan=3, sticky="we",
                                                                      pady=(4, 0))
        ttk.Label(left, text="mm/min").grid(row=r, column=4, sticky="w", pady=(4, 0))
        r += 1
        ttk.Label(left, text="Cut direction").grid(row=r, column=0, sticky="w", pady=(4, 0))
        self.dir_box = ttk.Combobox(left, textvariable=self.direction_label, state="readonly", width=30)
        self.dir_box.grid(row=r, column=1, columnspan=3, sticky="w", pady=(4, 0))
        self.dir_box.bind("<<ComboboxSelected>>", lambda _e: self._offset_labels())
        r += 1
        ttk.Label(left, text="Depth").grid(row=r, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(left, textvariable=self.depth, width=7).grid(row=r, column=1, sticky="w", pady=(4, 0))
        ttk.Label(left, text="mm, from where the wire starts", foreground="#5f6368").grid(
            row=r, column=2, columnspan=3, sticky="w", pady=(4, 0))
        r += 1
        ttk.Label(left, text="Next slot").grid(row=r, column=0, sticky="w", pady=(4, 0))
        self.offset_box = ttk.Combobox(left, textvariable=self.offset_label, state="readonly", width=8)
        self.offset_box.grid(row=r, column=1, sticky="w", pady=(4, 0))
        ttk.Label(left, text="by").grid(row=r, column=2, sticky="e", pady=(4, 0))
        ttk.Entry(left, textvariable=self.spacing, width=6).grid(row=r, column=3, sticky="w", pady=(4, 0))
        ttk.Label(left, text="mm").grid(row=r, column=4, sticky="w", pady=(4, 0))
        r += 1
        ttk.Label(left, text="Pull-out speed").grid(row=r, column=0, sticky="w", pady=(4, 0))
        ttk.Entry(left, textvariable=self.pullout, width=7).grid(row=r, column=1, sticky="w", pady=(4, 0))
        ttk.Label(left, text="mm/min, wire cold", foreground="#5f6368").grid(
            row=r, column=2, columnspan=3, sticky="w", pady=(4, 0))
        r += 1
        buttons = ttk.Frame(left)
        buttons.grid(row=r, column=0, columnspan=5, sticky="w", pady=(8, 0))
        self.start_button = ttk.Button(buttons, text="Start", command=self.start)
        self.cut_button = ttk.Button(buttons, text="Cut slot", width=24, command=self.cut)
        self.pull_button = ttk.Button(buttons, text="Pull out (wire cold)", command=self.pull_out)
        self.skip_button = ttk.Button(buttons, text="Skip", command=self.skip)
        for b in (self.start_button, self.cut_button, self.pull_button, self.skip_button):
            b.pack(side=tk.LEFT, padx=(0, 6))
        r += 1
        ttk.Label(left, textvariable=self.note, wraplength=470, justify="left").grid(
            row=r, column=0, columnspan=5, sticky="w", pady=(6, 0))

        self.table = ttk.Frame(right)
        self.table.pack(anchor="nw", fill=tk.X)
        save = ttk.Frame(right)
        save.pack(anchor="nw", fill=tk.X, pady=(8, 0))
        ttk.Label(save, text="Table name").grid(row=0, column=0, sticky="w")
        ttk.Entry(save, textvariable=self.table_name, width=26).grid(row=0, column=1, sticky="w")
        ttk.Label(save, text="Description").grid(row=1, column=0, sticky="w")
        ttk.Entry(save, textvariable=self.table_desc, width=40).grid(row=1, column=1, columnspan=2, sticky="we")
        self.save_button = ttk.Button(save, text="Save to profile", command=self.save)
        self.save_button.grid(row=0, column=2, padx=(8, 0), sticky="w")

    def _direction_labels(self) -> None:
        letters = self.machine.letters()
        self.dir_box.configure(values=[d.label(letters) for d in DIRECTIONS])

    def _direction(self) -> Direction:
        values = list(self.dir_box.cget("values"))
        label = self.direction_label.get()
        return DIRECTIONS[values.index(label)] if label in values else direction("h-")

    def _offset_labels(self, wanted: str = "") -> None:
        options = [name for name, _ in OFFSETS[self._direction().vertical]]
        self.offset_box.configure(values=options)
        current = wanted or self.offset_label.get()
        self.offset_label.set(current if current in options else options[0])

    def _offset_sign(self) -> int:
        return dict(OFFSETS[self._direction().vertical]).get(self.offset_label.get(), 1)

    def _build_rows(self) -> None:
        for child in self.table.winfo_children():
            child.destroy()
        heads = ("#", "Speed", "Slot", "Face 1", "Face 2", "Kerf")
        for c, text in enumerate(heads):
            ttk.Label(self.table, text=text, font=("Segoe UI", 9, "bold")).grid(row=0, column=c, sticky="w", padx=4)
        self.rows = []
        self.state_labels: list[ttk.Label] = []
        for i, speed in enumerate(self.speeds):
            f1, f2, kerf, state = tk.StringVar(), tk.StringVar(), tk.StringVar(), tk.StringVar()
            ttk.Label(self.table, text=str(i + 1)).grid(row=i + 1, column=0, sticky="w", padx=4)
            ttk.Label(self.table, text=f"{speed:g} mm/min").grid(row=i + 1, column=1, sticky="w", padx=4)
            lab = ttk.Label(self.table, textvariable=state, width=9)
            lab.grid(row=i + 1, column=2, sticky="w", padx=4)
            ttk.Entry(self.table, textvariable=f1, width=7).grid(row=i + 1, column=3, sticky="w", padx=4)
            ttk.Entry(self.table, textvariable=f2, width=7).grid(row=i + 1, column=4, sticky="w", padx=4)
            ttk.Label(self.table, textvariable=kerf, width=9).grid(row=i + 1, column=5, sticky="w", padx=4)
            for var in (f1, f2):
                var.trace_add("write", lambda *_a, k=i: self._update_kerf(k))
            self.rows.append((f1, f2, kerf, state))
        self.warnings = tk.StringVar(value="")
        ttk.Label(self.table, textvariable=self.warnings, foreground="#b06000", wraplength=330, justify="left").grid(
            row=len(self.speeds) + 1, column=0, columnspan=6, sticky="w", pady=(4, 0))
        ttk.Label(self.table, foreground="#5f6368", wraplength=330, justify="left", text=(
            "Slot widths in mm, with feeler gauges at both faces, near the closed end.")).grid(
            row=len(self.speeds) + 2, column=0, columnspan=6, sticky="w")

    # --- setup ----------------------------------------------------------------------------

    def generate(self) -> None:
        """Fill the speeds from the target: target / max_speed_ratio up to a bit above it."""
        target, count = _num(self.target.get()), _num(self.count.get())
        profile = self.profile()
        ratio = profile.max_speed_ratio if profile is not None else 3.0
        try:
            if target is None or count is None:
                raise ValueError("the target speed and the number of slots must be numbers")
            speeds = calibration_speeds(target, ratio, min(int(count), MAX_SLOTS))
        except ValueError as error:
            self.note.set(str(error).capitalize() + ".")
            return
        self.speeds_text.set(", ".join(f"{s:g}" for s in speeds))
        self.note.set(f"Speeds from {target:g} / {ratio:g} (max_speed_ratio: the slowest a foam face goes at this "
                      f"target) to {target:g} x 1.2. Edit the list if you like, then Start.")

    def start(self) -> None:
        """Begin a new sequence with the speeds in the list (forgets the previous one)."""
        if self.phase in ("cutting", "pulling"):
            return
        if any(s in ("cut",) for s in self.states) and self.phase != "done" and not messagebox.askyesno(
            "Start again", "Forget the slots cut so far and start a new sequence?"
        ):
            return
        try:
            speeds = parse_speeds(self.speeds_text.get())
        except ValueError as error:
            messagebox.showwarning("Speeds", str(error).capitalize() + ".")
            return
        if len(speeds) > MAX_SLOTS:
            messagebox.showwarning("Speeds", f"At most {MAX_SLOTS} slots.")
            return
        profile = self.profile()
        if profile is not None:
            d = self._direction()
            axes = [a for a in profile.axes if a.letter in cut_move(self.machine.letters(), d, 1)]
            limit = min(a.max_rate for a in axes) if axes else None
            if limit is not None and speeds[-1] > limit:
                messagebox.showwarning("Speeds", f"{speeds[-1]:g} mm/min is above the profile's max_rate "
                                       f"({limit:g} mm/min) for those axes.")
                return
        self.speeds = speeds
        self.states = [""] * len(speeds)
        self.current = 0
        self.phase = "ready"
        self._build_rows()
        self.machine.settings.update({
            "kerf_slots": len(speeds), "kerf_direction": self._direction().key, "kerf_depth": self.depth.get(),
            "kerf_offset": self.offset_label.get(), "kerf_spacing": self.spacing.get(),
            "kerf_pullout": self.pullout.get(),
        })
        self._show()

    # --- the sequence ---------------------------------------------------------------------

    def _numbers(self) -> tuple[float, float, float] | None:
        depth, spacing, pull = _num(self.depth.get()), _num(self.spacing.get()), _num(self.pullout.get())
        if depth is None or depth <= 0 or spacing is None or spacing <= 0 or pull is None or pull <= 0:
            messagebox.showwarning("Kerf slots", "Depth, spacing and pull-out speed must be numbers above 0.")
            return None
        return depth, spacing, pull

    def cut(self) -> None:
        if self.phase != "ready" or self.current >= len(self.speeds):
            return
        nums = self._numbers()
        if nums is None:
            return
        depth, _, _ = nums
        letters = self.machine.letters()
        d = self._direction()
        speed = self.speeds[self.current]
        wpos = self.machine.work_position()
        if wpos is None or not self.machine.can_move():
            messagebox.showwarning("Not ready", "Connect on the Machine tab and wait until the machine is Idle.")
            return
        bent = straight_wire(wpos, letters)
        if bent and not messagebox.askyesno(
            "Wire not straight", f"For a kerf slot the wire should be straight, but {bent}.\n\nCut anyway?"
        ):
            return
        outside = slot_end_inside(wpos, [cut_move(letters, d, depth)], self.machine.travel())
        if outside:
            messagebox.showwarning("Outside the travel", f"The slot would leave the travel: {outside}.")
            return
        override = self.machine.reset_override()
        n = len(self.speeds)
        if self.machine.run_block(
            f"{CUT_NAME} {self.current + 1} of {n}",
            slot_program(letters, d, depth, speed),
            f"Cut kerf slot {self.current + 1} of {n}",
            f"{speed:g} mm/min, {depth:g} mm {d.label(letters).split('(')[1].rstrip(')')}"
            + (" (feed override set back to 100 %)" if override else "") + ".\n\n"
            "Check before you press OK:\n"
            "  - the wire heat is ON and has settled\n"
            "  - the wire is beside the scrap block and the slot's path is clear\n\n"
            "The wire stops at the end of the slot. Switch the heat off then; it is pulled out cold.",
        ):
            self.phase = "cutting"
            self._show()

    def pull_out(self) -> None:
        if self.phase != "cooling":
            return
        nums = self._numbers()
        if nums is None:
            return
        depth, spacing, pull = nums
        letters = self.machine.letters()
        d = self._direction()
        last = self.current >= len(self.speeds) - 1
        step = None if last else step_move(letters, d, self._offset_sign(), spacing)
        wpos = self.machine.work_position()
        if wpos is not None:
            back = {k: -v for k, v in cut_move(letters, d, depth).items()}
            outside = slot_end_inside(wpos, [back] + ([step] if step else []), self.machine.travel())
            if outside:
                messagebox.showwarning("Outside the travel", f"The next start would leave the travel: {outside}. "
                                       "Change the spacing or direction, or pull out by jogging.")
                return
        then = "" if last else f", then go {spacing:g} mm {self.offset_label.get()} to the next slot's start"
        if self.machine.run_block(
            f"{PULL_NAME} {self.current + 1}",
            pullout_program(letters, d, depth, pull, step),
            "Wire heat OFF",
            f"Slot {self.current + 1} is cut.\n\n"
            "Switch the wire heat OFF now and wait until the wire is cold.\n\n"
            f"OK pulls the cold wire back out along the slot at {pull:g} mm/min{then}.",
        ):
            self.phase = "pulling"
            self._show()

    def skip(self) -> None:
        if self.phase != "ready":
            return
        self.states[self.current] = "skipped"
        self._next()

    def _next(self) -> None:
        self.current += 1
        self.phase = "done" if self.current >= len(self.speeds) else "ready"
        self._show()

    def _block_ended(self, name: str, finished: bool, message: str) -> None:
        if name.startswith(CUT_NAME) and self.phase == "cutting":
            if finished:
                self.states[self.current] = "cut"
                self.phase = "cooling"
                self._show()
                self.after(10, self.pull_out)  # asks for heat off; Cancel leaves the Pull out button
            else:
                self.states[self.current] = "stopped"
                self.phase = "ready"
                self._show(f"Slot {self.current + 1} was stopped ({message}). Switch the heat off, pull the wire out "
                           "by jogging, then cut it again or Skip it.")
        elif name.startswith(PULL_NAME) and self.phase == "pulling":
            if finished:
                self._next()
            else:
                self.current += 1
                self.phase = "done" if self.current >= len(self.speeds) else "ready"
                self._show(f"The pull-out was stopped ({message}). Move the wire to the next slot's start by "
                           "jogging or with the section view.")

    def _show(self, note: str | None = None) -> None:
        for i, (_, _, _, state) in enumerate(self.rows):
            text = {"cut": "cut", "skipped": "skipped", "stopped": "stopped"}.get(self.states[i], "")
            if i == self.current and self.phase in ("ready", "cutting", "cooling", "pulling"):
                text = {"ready": "next", "cutting": "CUTTING", "cooling": "cooling", "pulling": "pulling"}[self.phase]
            state.set(text)
        n = len(self.speeds)
        if note is not None:
            self.note.set(note)
        elif self.phase == "ready":
            again = " again" if self.states[self.current] == "stopped" else ""
            self.note.set(f"Slot {self.current + 1} of {n}{again}: put the wire beside the block (Jog tab, or click "
                          "the section view), heat ON, then Cut.")
        elif self.phase == "cutting":
            self.note.set(f"Cutting slot {self.current + 1} at {self.speeds[self.current]:g} mm/min. "
                          "Hold or Stop are above.")
        elif self.phase == "cooling":
            self.note.set("Switch the heat off, wait until the wire is cold, then Pull out.")
        elif self.phase == "pulling":
            self.note.set("Pulling the cold wire out.")
        elif self.phase == "done":
            self.note.set("All slots done. Measure each slot's width at both faces and enter it on the right, "
                          "then Save to profile.")
        if self.current < n and self.phase != "done":
            self.cut_button.configure(text=f"Cut slot {self.current + 1} ({self.speeds[self.current]:g} mm/min)")
        else:
            self.cut_button.configure(text="Cut slot")
        self._refresh(once=True)

    def _refresh(self, once: bool = False) -> None:
        def enable(b: ttk.Button, on: bool) -> None:
            b.state(["!disabled"] if on else ["disabled"])

        can = self.machine.can_move()
        enable(self.start_button, self.phase not in ("cutting", "pulling"))
        enable(self.cut_button, self.phase == "ready" and can)
        enable(self.pull_button, self.phase == "cooling" and can)
        enable(self.skip_button, self.phase == "ready")
        enable(self.save_button, any(self._kerf(i)[0] is not None for i in range(len(self.rows))))
        if not once:
            self.after(REFRESH_MS, self._refresh)

    # --- measurements and saving ----------------------------------------------------------

    def _kerf(self, i: int) -> tuple[float | None, str]:
        f1, f2, _, _ = self.rows[i]
        a, b = f1.get().strip(), f2.get().strip()
        w1, w2 = (_num(a) if a else None), (_num(b) if b else None)
        if (a and w1 is None) or (b and w2 is None):
            return None, "not a number"
        return mean_width(w1, w2)

    def _update_kerf(self, i: int) -> None:
        kerf, note = self._kerf(i)
        text = f"{kerf:.3f}" if kerf is not None else ""
        self.rows[i][2].set(f"{text} !" if note else text)
        notes = []
        for j in range(len(self.rows)):
            _, why = self._kerf(j)
            if why:
                notes.append(f"Slot {j + 1}: {why}")
        self.warnings.set("\n".join(notes))

    def measured(self) -> list[tuple[float, float]]:
        out = []
        for i, speed in enumerate(self.speeds):
            kerf, _ = self._kerf(i)
            if kerf is not None:
                out.append((speed, kerf))
        return out

    def save(self) -> None:
        points = self.measured()
        path = self.profile_path()
        name, desc = self.table_name.get().strip(), self.table_desc.get().strip()
        try:
            text = path.read_text(encoding="utf-8")
            new = append_kerf_table(text, name, desc, points)
        except (OSError, ValueError) as error:
            messagebox.showerror("Cannot save the kerf table", str(error))
            return
        rows = "\n".join(f"  {s:g} mm/min: {k:.3f} mm" for s, k in points)
        if not messagebox.askokcancel(
            "Save to profile", f'Add kerf table "{name}" to\n{path}\n\n{rows}\n\n'
            f"The file is copied to {path.name}.bak first; nothing else in it changes."
        ):
            return
        try:
            newline = "\r\n" if b"\r\n" in path.read_bytes() else "\n"  # keep the file's line endings
            shutil.copy2(path, path.with_name(path.name + ".bak"))
            path.write_text(new, encoding="utf-8", newline=newline)
        except OSError as error:
            messagebox.showerror("Cannot save the kerf table", str(error))
            return
        self.note.set(f'Saved kerf table "{name}" to {path.name} and selected it for the next Preview.')
        self.saved(name)
