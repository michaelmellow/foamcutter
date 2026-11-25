"""The Machine tab's Cut tab: put the wire at the operator side at a height, then cut
straight across the table at a speed (crosscut.py)."""

import tkinter as tk
from collections.abc import Callable
from tkinter import messagebox, ttk

from foamcam.profile import Profile

from .crosscut import (
    CrossCut,
    away_from_start,
    cut_program,
    plan_cross_cut,
    start_program,
)
from .machine import MachinePanel, save_machine_settings


class CrossCutPanel(ttk.Frame):
    """`profile()` gives the machine profile in the form, or None."""

    def __init__(self, parent: tk.Misc, machine: MachinePanel, profile: Callable[[], Profile | None]) -> None:
        super().__init__(parent, padding=8)
        self.machine = machine
        self.profile = profile
        st = machine.settings
        self.height = tk.StringVar(value=str(st.get("cross_z", 40)))
        self.speed = tk.StringVar(value=str(st.get("cross_speed", 150)))
        self.note = tk.StringVar()
        for var in (self.height, self.speed):
            var.trace_add("write", lambda *_: self.show())

        ttk.Label(self, text="Height (wire centre, G54 z)").grid(row=0, column=0, sticky="w", pady=2)
        ttk.Entry(self, textvariable=self.height, width=9).grid(row=0, column=1, sticky="w", padx=4)
        ttk.Label(self, text="mm", foreground="#5f6368").grid(row=0, column=2, sticky="w")
        ttk.Label(self, text="Speed").grid(row=1, column=0, sticky="w", pady=2)
        ttk.Entry(self, textvariable=self.speed, width=9).grid(row=1, column=1, sticky="w", padx=4)
        ttk.Label(self, text="mm/min", foreground="#5f6368").grid(row=1, column=2, sticky="w")
        buttons = ttk.Frame(self)
        buttons.grid(row=2, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.start_button = ttk.Button(buttons, text="1. Wire to the start (cold)", command=self.go_to_start)
        self.start_button.pack(side=tk.LEFT, padx=(0, 6))
        self.cut_button = ttk.Button(buttons, text="2. Cut across (hot)", command=self.cut)
        self.cut_button.pack(side=tk.LEFT)
        ttk.Label(self, textvariable=self.note, foreground="#5f6368", wraplength=420, justify="left").grid(
            row=3, column=0, columnspan=3, sticky="w", pady=(8, 0))
        ttk.Label(self, foreground="#5f6368", wraplength=420, justify="left", text=(
            "Slices the block at one height, all the way across the table: the wire goes to the "
            "operator side, then straight across to the far side. Both towers move together, so the "
            "wire stays straight. The kerf takes half its width off each side of the height.")).grid(
            row=4, column=0, columnspan=3, sticky="w", pady=(6, 0))
        self.show()
        self.bind("<Map>", lambda _e: self.show())  # the profile may have changed meanwhile

    def plan(self) -> CrossCut | str:
        """The cut for the fields, or why there is none."""
        profile = self.profile()
        if profile is None:
            return "no machine profile: pick one in the form"
        try:
            z = float(self.height.get().replace(",", "."))
            speed = float(self.speed.get().replace(",", "."))
        except ValueError:
            return "enter the height and the speed as numbers"
        try:
            return plan_cross_cut(profile, z, speed)
        except ValueError as error:
            return str(error)

    def show(self) -> None:
        cut = self.plan()
        if isinstance(cut, str):
            self.note.set(f"Cannot cut: {cut}.")
            return
        l1, l2, l3, l4 = self.machine.letters()
        self.note.set(f"From {l1}={l3}={cut.start_x:g} (operator side) to {cut.end_x:g} at "
                      f"{l2}={l4}={cut.z:g}, {cut.speed:g} mm/min: {cut.minutes:.1f} min.")

    def _remember(self) -> None:
        self.machine.settings.update({"cross_z": self.height.get(), "cross_speed": self.speed.get()})
        save_machine_settings(self.machine.settings)

    def go_to_start(self) -> None:
        cut = self.plan()
        if isinstance(cut, str):
            messagebox.showwarning("Cannot cut", cut.capitalize() + ".")
            return
        self._remember()
        letters = self.machine.letters()
        self.machine.run_block("cross cut: wire to the start", start_program(letters, cut), "Wire to the cut start", (
            f"The wire moves to the operator side ({letters[0]}={letters[2]}={cut.start_x:g}) at its "
            f"current height, then straight up or down to {letters[1]}={letters[3]}="
            f"{cut.z:g}.\n\n"
            "Check before you press OK:\n"
            "  - the wire heat is OFF\n"
            "  - nothing is in the wire's way on either move"))

    def cut(self) -> None:
        cut = self.plan()
        if isinstance(cut, str):
            messagebox.showwarning("Cannot cut", cut.capitalize() + ".")
            return
        letters = self.machine.letters()
        wpos = self.machine.work_position()
        if wpos is None:
            messagebox.showwarning("Not connected", "Connect on the Machine tab first.")
            return
        away = away_from_start(wpos, letters, cut)
        if away:
            messagebox.showwarning("Not at the start", f"{away.capitalize()}. Press \"Wire to the start\" first.")
            return
        self._remember()
        override = self.machine.reset_override()
        self.machine.run_block(f"cross cut at z {cut.z:g}", cut_program(letters, cut), "Cut across", (
            f"Straight across to {letters[0]}={letters[2]}={cut.end_x:g} at {cut.speed:g} mm/min "
            f"({cut.minutes:.1f} min)" + (" (feed override set back to 100 %)" if override else "") + ".\n\n"
            "Check before you press OK:\n"
            "  - the wire heat is ON and has settled\n"
            "  - the block lies flat and nothing else is in the wire's way\n\n"
            "The wire stops at the far side. Switch the heat off then."), cut=True)
