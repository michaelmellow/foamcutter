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
