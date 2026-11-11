PROJECT: 4-axis CNC hot-wire foam cutter for UAV wings.

MACHINE
- Two towers. Each tower has a horizontal and a vertical axis: 4 independent axes.
- A straight heated wire runs between the two tower carriages.
- Frame is hand-built. Dimensions are NOT fixed: nothing may be hard-coded.
  All geometry comes from a machine profile file (format defined in Brief 3).
- Drive: leadscrews on all 4 axes. Lead (mm/rev) is a profile parameter.
- Motors: Creality 42-40 NEMA 17 steppers (look up rated current; do not guess).
- No limit switches. Homing is sensorless (stall detection).
  Each axis must have a solid mechanical stop at both ends of travel.

ELECTRONICS
- Controller: BIGTREETECH SKR 3 EZ, MCU STM32H723.
- Drivers: 4x BTT EZ2209 (TMC2209, UART).
- Wire heat: external power supply. The controller does NOT control it.

SOFTWARE STACK
- Firmware: grblHAL (STM32H7xx driver) with the Trinamic motor plugin.
- Sender: ioSender on Windows.
- Custom tools: Python 3.11+, must run on Windows; Linux should work
  if it costs nothing extra. Prefer pure-Python dependencies.

AXIS CONVENTION (used in all tools)
- Left tower: horizontal = axis 1, vertical = axis 2.
- Right tower: horizontal = axis 3, vertical = axis 4.
- G-code letters for the 4 axes are a profile setting (default X Y Z A).
- Units: millimetres. Feed: inverse-time mode (G93) unless told otherwise.

RULES FOR THIS SESSION
- Do not invent facts about hardware or firmware. If you cannot verify
  something from source code or documentation, say so explicitly.
- Every deliverable needs automated tests where tests are possible.
- End with a report: what was built, what was verified and how,
  what was NOT verified, and open questions for the project director.

**Rules for every session**

- Read `CLAUDE.md` and your brief in full before writing anything.
- Work on the branch your prompt names. Never commit to `main`.
- Write only in the folders your prompt names.
- Do not edit anything in `docs/briefs/`. Do not edit `docs/formats/` after it has been approved; ask first.
- `python check.py` must pass before you report.
- Do not weaken or delete a test to make it pass. If a test is wrong, stop and explain.
- Generated files go in `output/`.
- Finish by writing `docs/reports/brief-N-report.md`: what was built, what was verified and how, what was not verified, open questions.
