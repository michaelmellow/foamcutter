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
