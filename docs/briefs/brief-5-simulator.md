## Brief 5: Simulator and independent check

**Goal.** A separate Python tool that reads finished G-code and the machine profile, reconstructs the part the wire would cut, and compares it with the intended sections. It is the check that runs before any foam is cut.

**Independence rule.** Run this in a session that has never seen the Brief 3 code. Give it only the shared context, the machine profile format, the job file format and this brief. Two implementations that agree are strong evidence; one implementation checking itself is not.

**Tasks**

1. Parse the G-code subset used here: G0, G1, G21, G54, G90, G91, G93, G94, G4, and the configured axis letters. Reject anything else.
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
