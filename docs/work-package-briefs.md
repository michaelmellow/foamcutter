# 4-Axis Foam Cutter: Work Package Briefs

Nov 11, 2026 · @Michael

## Decisions so far

The project is four work packages: one firmware build, two Python tools, and one independent checker. No firmware is written from scratch.

| Layer | Decision | Reason |
| --- | --- | --- |
| Firmware | grblHAL, STM32H7xx driver, compiled locally | Supports the SKR 3 family; a local build is needed for Trinamic driver options |
| Homing | Sensorless, using EZ2209 stall detection | Your preference; mechanical switches stay a drop-in fallback on the same inputs |
| Sender | ioSender on Windows | Built for grblHAL; Linux sender is optional and unbudgeted |
| Commissioning | Manual, through ioSender, following the Brief 1 procedure | Automatic tuning wizard dropped to cut scope |
| CAM | Custom Python, in two layers: core, then STEP front end | Existing tools do not read CAD models |
| Feed rate | CAM emits inverse-time moves (G93) | Removes any dependence on how firmware interprets feed on 4 axes |
| Axis letters | Configurable in CAM; default X Y Z A | Firmware brief reports whether U V naming is available |
| Wire heat | External supply, not controlled by the board | Your choice |

**How to use the briefs.** Set up the repository with Prompt 0, then run each brief in its own Claude Code session using its prompt in the Claude Code prompts section. Each brief ends with a report format; bring that report back for review before starting the next package.

## Shared context (becomes CLAUDE.md)

This block gives each session the same machine facts and ground rules. Update it when a package reports something new.

```markdown
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
```

## Brief 1: Firmware build

**Goal.** Produce a reproducible grblHAL build for the SKR 3 EZ (STM32H723) that drives 4 independent axes through EZ2209 drivers and homes all 4 without switches.

**Background to verify, not assume.** The [grblHAL controller list](https://github.com/grblhal/controllers) names the Bigtreetech SKR 3 under the STM32H7xx driver. The [Trinamic motor plugin](https://github.com/grblHAL/Plugins_motor/blob/master/README.md) supports the TMC2209 and provides sensorless homing. A developer in [grblHAL core issue 152](https://github.com/grblHAL/core/issues/152) reported working SKR 3 builds with TMC2209 support.

**Tasks**

1. Read the STM32H7xx driver source. Identify the board define and pin map for the SKR 3 EZ, and confirm the H723 build target, crystal frequency and flash offset for the BTT bootloader.
2. Configure 4 axes, USB serial, settings storage, and the Trinamic plugin for TMC2209 over UART on driver slots X, Y, Z and E0.
3. Confirm from the pin map and BTT schematic how each driver's DIAG output reaches a limit input, and which jumpers must be fitted.
4. Enable sensorless homing. Document the settings involved: `$338` (driver enable), `$339` (sensorless homing enable), `$14x` (current), `$15x` (microsteps), `$20x` and `$22x` (stall sensitivity, 0 to 255 for the TMC2209).
5. List which Trinamic parameters are hard-coded at compile time and expose the stall-detection ones as runtime settings.
6. Confirm that inverse-time feed (G93) works on 4-axis moves. Report how G94 feed is interpreted on 4-axis moves.
7. Report whether the third and fourth axes can be named U and V, and how.
8. Define an input for an emergency stop and a feed hold.
9. Write build instructions (PlatformIO preferred) and flashing instructions for this board.
10. Write a bench-test script: a list of commands and the expected response for each.

**Deliverables**

- Configuration files and build instructions that produce the binary from a clean checkout, pinned to specific grblHAL commits.
- The firmware binary.
- A one-page wiring sheet: driver slots, DIAG jumpers, power, e-stop.
- A baseline settings file (`$` values) with comments.
- The bench-test script.
- A manual commissioning procedure for ioSender: steps/mm, direction, motor current, stall sensitivity, homing, travel and soft limits, speed and acceleration.

**Acceptance**

- `$I` reports the board and the Trinamic plugin.
- `M122` reports four TMC2209 drivers with live values.
- Each axis jogs alone, in the right direction, by the commanded distance.
- A 4-axis G93 move completes in the commanded time.

**Out of scope.** Carrying out the commissioning on the machine, any CAM work.

**Known difficulty.** The plugin's own documentation says sensorless tuning is not trivial and that switches are likely the easier option for one-off machines. Keep the limit inputs usable for mechanical switches without a rebuild.

## Brief 3: CAM core

**Goal.** A Python library and command-line tool that turns one panel, defined by two cross-sections and their positions in space, into 4-axis G-code. It must handle any shape a straight wire can cut.

**Inputs.** None. This package defines the machine profile format and can start before the machine is commissioned, using a sample profile.

**Job definition.** A job file describes one panel:

- Two sections, root and tip. Each is an airfoil `.dat` file or an arbitrary polyline, with chord, twist angle, twist axis position, and a 3D placement.
- Optional inner features per section: spar slots, lightening holes, hinge cuts. Each exists on both sections and is matched by name.
- Foam block size and its position on the table.
- Cut speed at the foam, kerf table name, cut direction, entry and exit paths.
- A mirror flag for the opposite wing.

**Processing steps**

1. **Load and clean sections.** Parse Selig and Lednicer `.dat` formats. Enforce a minimum trailing-edge thickness.
2. **Synchronise.** Pair every point on the root with a point on the tip. Use leading edge, trailing edge and feature corners as fixed pairs, and distribute the rest by normalised arc length between them.
3. **Kerf compensation.** Offset each section outward by half the local kerf. Kerf differs between the two ends because the wire moves at different speeds; take it from the kerf-versus-speed table.
4. **Project to the towers.** Extend the line through each root and tip point pair to the two carriage planes, using the wire span from the profile. This gives the four axis positions.
5. **Timing.** Give each move a duration so that the faster end of the wire moves at the requested cut speed. Emit inverse-time feed (G93).
6. **Path assembly.** Entry from outside the block, the profile, inner features reached through a slit and exited along the same slit, exit, return to a safe position.
7. **Checks.** Every position inside soft limits, wire angle below a configurable maximum, speed ratio between ends below a configurable maximum. Fail with a clear message, never clip silently.
8. **Output.** G-code with configurable axis letters, a text report (travel used, speed ratio, kerf values), and a preview image.

**Deliverables**

- The library, the command-line tool, and a job file format with a validator.
- The machine profile format (YAML or JSON), documented field by field, with a validator: axis letters, travel per axis, wire span, table height, and kerf-versus-speed tables. The operator fills it in by hand.
- A kerf test-cut generator: slots at several speeds, which the operator measures and enters in the profile.
- A test suite including the cases below.
- Three worked examples: straight wing, tapered swept wing with washout, tapered wing with a spar slot.

**Acceptance tests**

- Identical root and tip, block centred: axis 1 equals axis 3 and axis 2 equals axis 4 on every line.
- Tapered case with a hand-calculated projection: output matches to 0.01 mm.
- Mirrored job: output is the exact mirror of the original.
- A job exceeding travel is rejected with the axis and the amount named.
- Total cut time equals path length at the faster end divided by cut speed, within 1%.

**Out of scope.** Reading CAD files, a graphical interface, sending G-code to the machine.

## Brief 4: STEP front end

**Goal.** Read a wing exported from SolidWorks 2025 as STEP, split it into panels a straight wire can cut, and write one Brief 3 job file per panel together with a report of how closely the panels match the model.

**Inputs.** The Brief 3 job file format and library. The machine profile. Sample STEP files from you: one simple tapered wing and one hard case.

**Tasks**

1. **Import.** Load STEP with an OpenCascade-based Python library installable by pip. Confirm units and report the bounding box.
2. **Orientation.** Detect the spanwise axis and the chord direction, and let the operator override both.
3. **Choose panel breaks.** Place breaks at planform kinks and airfoil transitions. Add breaks until the straight-line surface between neighbouring sections deviates from the model by less than a tolerance (default 0.2 mm). Keep every panel shorter than the cuttable span in the profile.
4. **Extract sections.** Cut the solid with a plane at each break. Convert outer and inner loops to polylines at a set chord tolerance.
5. **Match features.** Identify leading edge, trailing edge and inner loops on each section and pair them across the panel. Flag a feature that exists on only one end.
6. **Use `.dat` files where supplied.** If the operator names the airfoil for a station, fit the `.dat` profile to the extracted section and use it when the fit error is below tolerance.
7. **Deviation report.** For each panel give maximum and RMS deviation and a colour map image. List regions that cannot be cut and say why.
8. **Block plan.** For each panel give the minimum foam block size, its orientation on the table, and a cut list.
9. **Write job files** and run them through the Brief 3 library to confirm each passes its checks.

**What counts as cuttable.** Any surface made of straight lines running from one section to the next. Taper, sweep, twist, dihedral, airfoil blending, spar slots and through-holes qualify. Surfaces curved along the span are approximated by more panels. Pockets that do not pass through the full panel, and blended winglet corners, are reported as not cuttable.

**Deliverables**

- The command-line tool and library.
- Tests on synthetic STEP files generated in code, where the correct answer is known.
- A report template for the deviation output.

**Acceptance**

- A straight tapered wing produces one panel with deviation below 0.01 mm.
- An elliptical planform is split into panels that each meet the tolerance, and tightening the tolerance increases the panel count.
- Sections extracted from a model built from a known `.dat` file match that file within 0.05 mm at 200 mm chord.

**Out of scope.** Editing the model, structural decisions, a graphical interface.

## Brief 5: Simulator and independent check

**Goal.** A separate Python tool that reads finished G-code and the machine profile, reconstructs the part the wire would cut, and compares it with the intended sections. It is the check that runs before any foam is cut.

**Independence rule.** Run this in a session that has never seen the Brief 3 code. Give it only the shared context, the machine profile format, the job file format and this brief. Two implementations that agree are strong evidence; one implementation checking itself is not.

**Tasks**

1. Parse the G-code subset used here: G0, G1, G90, G91, G93, G94, G4, and the configured axis letters. Reject anything else.
2. Compute the wire line in 3D at every step from the four axis positions and the wire span.
3. Intersect the wire with the root and tip planes of the block, and with any plane the operator asks for.
4. Remove the kerf using the profile's table, and compare the result with the target sections in the job file. Report maximum deviation per section.
5. Check soft limits, wire speed at the foam at both ends, total time, and that the wire starts and ends outside the block.
6. Show a 3D animation of the cut and a 2D overlay of target against simulated sections.
7. Return a pass or fail result with a machine-readable report.

**Deliverables**

- The simulator, with tests built from hand-written G-code whose result is known.
- A first-cut plan: an ordered list of test cuts on scrap foam, from a square block to a tapered swept panel, with what to measure after each.

**Acceptance**

- All three Brief 3 worked examples pass with deviation below 0.05 mm.
- A deliberately corrupted file (one axis scaled by 1%) fails, and the report names the affected section.

**Out of scope.** Thermal modelling of the wire, wire sag or lag.

## Order of work

Briefs 1 and 3 start now, in parallel; nothing else starts until its gate is passed.

| Brief | Starts after | Gate before the next step |
| --- | --- | --- |
| 1 Firmware | Now | All four bench acceptance checks pass on your board |
| 3 CAM core | Now, with a sample profile | Test suite passes; I review the projection and timing maths |
| 5 Simulator | Brief 3 job format is fixed | Passes its own tests, then passes all Brief 3 examples |
| First foam cuts | Briefs 1, 3 and 5 gates; machine commissioned by hand | Test cuts measured against the first-cut plan |
| 4 STEP front end | First foam cuts succeed | Synthetic tests pass; one real wing passes the simulator |

**Review step.** After each brief, paste its final report here. I check it against the acceptance list, decide whether the gate is passed, and update the shared context block for the next session.

## Project file layout

Everything lives in one Git repository, with one folder per work package and the shared context in `CLAUDE.md`, which Claude Code reads automatically at the start of every session.

```text
foamcutter/
├── CLAUDE.md                  shared context + rules for every session
├── README.md
├── pyproject.toml             one Python project, three packages
├── check.py                   runs lint, type check and all tests
├── .github/workflows/check.yml   runs check.py on Windows and Linux
├── docs/
│   ├── work-package-briefs.md    Markdown export of this document
│   ├── briefs/
│   │   ├── brief-1-firmware.md
│   │   ├── brief-3-cam-core.md
│   │   ├── brief-4-step-frontend.md
│   │   └── brief-5-simulator.md
│   ├── formats/
│   │   ├── machine-profile.md    contract between all tools
│   │   └── job-file.md           contract between CAM, STEP and simulator
│   ├── reports/                  one final report per brief
│   └── first-cut-plan.md
├── firmware/                  Brief 1: build config, settings file,
│                              wiring sheet, bench test, commissioning procedure
├── src/
│   ├── foamcam/               Brief 3: CAM core
│   ├── foamstep/              Brief 4: STEP front end
│   └── foamsim/               Brief 5: simulator (never imports foamcam)
├── tests/
│   ├── cam/
│   ├── step/
│   └── sim/
├── examples/
│   ├── profiles/              sample and real machine profiles
│   ├── airfoils/              your .dat files
│   ├── step/                  your SolidWorks STEP exports
│   ├── jobs/                  worked example job files
│   └── gcode/                 G-code of the worked examples
└── output/                    generated files, not committed
```

**Before you start**

1. Install Git, Python 3.11 or newer, and Claude Code on the Windows PC.
2. Create an empty folder named `foamcutter` with a `docs` folder inside it.
3. Export this document as Markdown and save it as `docs/work-package-briefs.md`.
4. Put your airfoil `.dat` files and STEP exports aside; Prompt 0 creates the folders for them.
5. Open Claude Code in the `foamcutter` folder and run Prompt 0.

**Rules for every session**

- Read `CLAUDE.md` and your brief in full before writing anything.
- Work on the branch your prompt names. Never commit to `main`.
- Write only in the folders your prompt names.
- Do not edit anything in `docs/briefs/`. Do not edit `docs/formats/` after it has been approved; ask first.
- `python check.py` must pass before you report.
- Do not weaken or delete a test to make it pass. If a test is wrong, stop and explain.
- Generated files go in `output/`.
- Finish by writing `docs/reports/brief-N-report.md`: what was built, what was verified and how, what was not verified, open questions.

## Claude Code prompts

There are five prompts: one setup prompt, then one per brief. Start a new Claude Code session in the `foamcutter` folder for each, and paste the prompt as the first message.

**Prompt 0: repository setup.** Run once, before anything else.

```text
You are setting up the repository for a 4-axis hot-wire foam cutter project.
The file docs/work-package-briefs.md is the project plan. Read all of it first.

Do the following, and nothing else:

1. Run git init if this folder is not already a repository.
2. Create the folder layout shown in the "Project file layout" section of the plan.
3. Create CLAUDE.md at the root. It must contain, in this order:
   a. the content of the code block under the "Shared context" heading,
      copied verbatim;
   b. the "Rules for every session" list from "Project file layout",
      copied verbatim.
4. Copy each "Brief N" section of the plan, verbatim, into its own file
   under docs/briefs/, using the file names shown in the layout.
5. Create pyproject.toml for Python 3.11+ with three empty packages under
   src/ (foamcam, foamstep, foamsim) and pytest, ruff and mypy as
   development dependencies.
6. Create check.py at the root. It runs ruff, mypy and pytest in turn and
   exits non-zero if any of them fails. It must work on Windows and Linux
   without make.
7. Add a .gitignore covering Python, PlatformIO and the output/ folder.
8. Add a GitHub Actions workflow that runs "python check.py" on
   windows-latest and ubuntu-latest for every push and pull request.
9. Run "python check.py" and confirm it passes on the empty project.
10. Commit everything to the main branch with the message "Project skeleton".

Do not write any firmware, CAM, STEP or simulator code.
Do not reword, shorten or "improve" the briefs or the shared context.

Finish by listing the files you created and anything in the plan that
was unclear or contradictory.
```

**Prompt 1: firmware.** Can run at the same time as Prompt 3.

```text
Carry out docs/briefs/brief-1-firmware.md. Read it and CLAUDE.md in full
before you start.

Work on a new git branch named brief-1-firmware.
Write only inside firmware/ and docs/reports/.

You have no access to the controller board. Everything that needs the
board is run by me. So:

1. Do every task that can be done from source code and documentation, and
   build the firmware binary on this computer.
2. Check every hardware fact (board define, pin map, DIAG routing, crystal
   frequency, flash offset, setting numbers) against the grblHAL source and
   BIGTREETECH's published schematic and pinout. For each fact, cite the
   file and line, or the document and page. Mark anything you could not
   check as UNVERIFIED. Do not fill gaps from memory.
3. Write the bench test as a numbered checklist I can follow in ioSender:
   the command to send, the expected response, and a blank for my result.
4. Write the manual commissioning procedure the same way, in the order I
   should do it, with a safe starting value for every setting.
5. Then stop and ask me to flash the board and run the bench test.
6. When I paste my results, fix what failed and repeat until every item in
   the brief's acceptance list passes.

When the acceptance list is met, write docs/reports/brief-1-report.md,
commit, and tell me the branch is ready for review.
```

**Prompt 3: CAM core.** Can run at the same time as Prompt 1.

```text
Carry out docs/briefs/brief-3-cam-core.md. Read it and CLAUDE.md in full
before you start.

Work on a new git branch named brief-3-cam-core.
Write only inside src/foamcam/, tests/cam/, examples/, docs/formats/ and
docs/reports/.

Work in this order:

1. Write docs/formats/machine-profile.md and docs/formats/job-file.md, and
   a sample profile in examples/profiles/. Another session will build a
   simulator from these two documents alone, without seeing your code, so
   they must be complete and unambiguous on their own: every field, its
   unit, its sign convention, the coordinate system and its origin.
   Then stop and show me both formats for approval.
2. After I approve, turn every item in the brief's acceptance list into a
   failing test in tests/cam/.
3. Implement until "python check.py" passes.
4. Produce the three worked examples: job files in examples/jobs/ and
   their G-code in examples/gcode/.

For the hand-calculated projection test, write the calculation out in a
comment so I can check it independently.

When the acceptance list is met, write docs/reports/brief-3-report.md,
commit, and tell me the branch is ready for review.
```

**Prompt 5: simulator.** Run after the Brief 3 branch is merged into `main`.

```text
Carry out docs/briefs/brief-5-simulator.md. Read it and CLAUDE.md in full
before you start.

Independence rule. This overrides convenience:
- Do NOT open, search, run or import anything in src/foamcam/ or tests/cam/.
- Do NOT read docs/reports/brief-3-report.md.
- Your only knowledge of the CAM tool is docs/formats/machine-profile.md
  and docs/formats/job-file.md.
- If those two documents are not enough to do the job, stop and tell me
  exactly what is missing. Do not look at the CAM code to find out.

Work on a new git branch named brief-5-simulator.
Write only inside src/foamsim/, tests/sim/, docs/first-cut-plan.md and
docs/reports/.

Work in this order:

1. Turn the brief's acceptance list into failing tests in tests/sim/,
   using hand-written G-code whose result you can calculate. Show each
   calculation in a comment.
2. Implement until "python check.py" passes.
3. Only then run the simulator on the files in examples/gcode/ with their
   job files in examples/jobs/. Report every result.
4. If an example fails, do not change the simulator to make it pass. Work
   out from the geometry which side is wrong, and report your reasoning.
5. Write the first-cut plan to docs/first-cut-plan.md.

When done, write docs/reports/brief-5-report.md, commit, and tell me the
branch is ready for review.
```

**Prompt 4: STEP front end.** Run after the first foam cuts succeed. Put your STEP files in `examples/step/` first.

```text
Carry out docs/briefs/brief-4-step-frontend.md. Read it and CLAUDE.md in
full before you start.

Work on a new git branch named brief-4-step-frontend.
Write only inside src/foamstep/, tests/step/ and docs/reports/.
You may import foamcam and foamsim but must not change them. If you need
a change in either, stop and tell me what and why.

Work in this order:

1. Choose the OpenCascade-based library. Confirm it installs with pip on
   this computer and loads the files in examples/step/. Report the choice
   and any installation problems before going further.
2. Turn the brief's acceptance list into failing tests in tests/step/,
   using STEP files generated in code so the correct answer is known.
3. Implement until "python check.py" passes.
4. Run the tool on each of my files in examples/step/. Put every job file
   it produces through foamcam, and the resulting G-code through foamsim.
   All must pass. Write the outputs to output/.
5. For anything in my models that cannot be cut, say which region and why.

When the acceptance list is met, write docs/reports/brief-4-report.md,
commit, and tell me the branch is ready for review.
```

**Between sessions.** Paste each report into this chat for review before merging its branch. If a session stops to ask you something you cannot answer, bring the question here.

## Open risks and manual work

Sensorless homing on leadscrews is the largest risk, and it is tuned by hand.

| Risk | Effect | Fallback |
| --- | --- | --- |
| Stall detection is unreliable on a leadscrew axis | Homing position varies; wings come out twisted | Fit a switch on that axis; same input, no rebuild |
| A stalled leadscrew jams the nut against the stop | Axis sticks after homing | Lower homing current; add a compliant stop |
| Kerf varies with foam batch and wire temperature | Dimensional error of a few tenths of a millimetre | Cut and measure a kerf test per foam type |
| OpenCascade library is awkward to install | Brief 4 slips | Core tool still works from `.dat` files and exported section curves |

**What stays manual, whatever the software does**

- Fitting solid end stops on all four axes, at both ends.
- Fitting the DIAG jumpers named in the Brief 1 wiring sheet.
- Commissioning through ioSender with the Brief 1 procedure: stall sensitivity, homing, travel, soft limits and speed limits.
- Measuring the wire span between carriage pivots and the travel of each axis, and entering them in the machine profile.
- Checking the two towers are parallel and square to the table.
- Measuring test cuts with calipers and entering the kerf values in the profile.
- Wiring an emergency stop that also cuts the wire supply.

## Sources

- [grblHAL supported controllers](https://github.com/grblhal/controllers)
- [grblHAL Trinamic motor plugin README](https://github.com/grblHAL/Plugins_motor/blob/master/README.md)
- [grblHAL core issue 152: SKR 3 support](https://github.com/grblHAL/core/issues/152)
