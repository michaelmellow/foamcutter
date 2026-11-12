# Machine profile format

Version 1. Status: draft for approval.

A machine profile describes one hot-wire foam cutter: its axes, its geometry and
the kerf it produces. The operator writes it by hand. Every tool in this project
(CAM core, STEP front end, simulator) reads the same file and must interpret it
exactly as described here. Nothing about the machine may be assumed from
anywhere else.

## 1. File

- YAML only. Encoding UTF-8, file extension `.yaml`.
- Read with a YAML 1.1 safe loader (PyYAML `safe_load`). Write every text value
  (names, axis letters) in double quotes so that no YAML parser turns it into a
  boolean or a number.
- Keys are case-sensitive. Unknown keys are an error, not ignored.

## 2. Units and conventions

| Quantity | Unit |
| --- | --- |
| Length, position | millimetre (mm) |
| Speed | millimetres per minute (mm/min) |
| Angle | degree |
| Time | minute (in G-code feed values only; see the job file format) |

All numbers are plain decimal numbers (integers are accepted where a decimal is
expected). No unit suffixes.

## 3. Work frame

All tools work in one right-handed Cartesian frame, the **work frame**. It is
the controller's **G54** work coordinate system. Every program selects G54 and
every axis position in a program, in a profile and in a job is a G54
coordinate. The controller's own machine coordinates (after homing) are never
used by the software; the link between them and G54 is the G54 offset set by
the operator (§3.4).

### 3.1 Front, left and right

Stand facing the machine so that the left tower (the one carrying axes 1 and 2)
is on your left and the right tower (axes 3 and 4) is on your right. You are
standing at the front.

### 3.2 Physical references

The work frame is tied to two physical references, which must be in place
before the zeros are set:

- **The table top.** A flat surface under the wire. Its top is the
  **z reference plane**.
- **The x reference face.** A straight, flat, vertical face fixed to the table
  near the back, facing the front, running from the left tower side to the
  right tower side (for example a straight aluminium bar clamped to the table,
  or the table's machined back edge). It must be perpendicular to the table top
  and square to both towers, i.e. perpendicular to both carriage planes. The
  longer it is, the more accurately it sets the zero; ideally it reaches close
  to each tower.

### 3.3 Axes and zeros

| Frame axis | Direction | Zero |
| --- | --- | --- |
| `y` | horizontal, along the wire, from the left tower toward the right tower | `y = 0` is the left carriage plane |
| `z` | vertical, up | `z = 0` is the table top (the z reference plane) |
| `x` | horizontal, toward the front | `x = 0` is the x reference face |

Check: `x = y × z`. With `y` pointing right and `z` pointing up, `x` points
toward the person standing at the front.

**Carriage planes.** Each tower's carriage moves in a vertical plane
perpendicular to `y`. The left carriage plane is `y = 0`; the right carriage
plane is `y = wire_span`.

**Wire points.** The wire passes through one fixed point on each carriage (the
point where it leaves the carriage, or its pivot). These are the **wire points**:

```text
left wire point   L = (a1, 0,         a2)
right wire point  R = (a3, wire_span, a4)
```

where `a1 … a4` are the G54 positions of axes 1 to 4 in mm. The wire is the
straight segment from `L` to `R`. The model assumes ideal, fixed wire points:
no wire sag, no lag, and no shift of the wire point when the wire tilts.

**Directions.** Axes 1 and 3 increase as their carriage moves toward the front.
Axes 2 and 4 increase as their carriage moves up.

**Zero of each axis.** Each axis has its own zero. The four zeros are defined
by where the wire is, not by the controller:

| Axis | Tower | Zero is where | Set it like this (cold wire) |
| --- | --- | --- | --- |
| 1 | left, horizontal | the left wire point is at `x = 0` | Move both horizontal axes until the wire lies against the x reference face along its whole length, touching at both ends of the face. Axis 1 reads 0 there. |
| 3 | right, horizontal | the right wire point is at `x = 0` | Same position as for axis 1. Axis 3 reads 0 there. |
| 2 | left, vertical | the left wire point is at `z = 0` | Move both vertical axes until the wire lies on the table top along its whole length, just touching (a strip of paper drags under the wire at both ends of the table but does not slide freely). Axis 2 reads 0 there. |
| 4 | right, vertical | the right wire point is at `z = 0` | Same position as for axis 2. Axis 4 reads 0 there. |

Because the wire is straight, if it touches a flat face at two points far
apart it lies in that face's plane, and so do both wire points. The x zeros
must be set with the wire clear of the table and the z zeros with the wire
clear of the x reference face.

Consequences that every tool relies on:

- When `a1 = a3` and `a2 = a4`, the wire is perpendicular to both carriage
  planes (parallel to `y`).
- The table top is at `z = 0`, so a foam block resting on the table has its
  bottom face at `z = 0`.

### 3.4 What the operator must do

1. Fit the two physical references (§3.2).
2. Set the four zeros (§3.3) by storing them as the G54 offset of each axis in
   the controller. With the wire held at the zero position of an axis, the
   grblHAL command `G10 L20 P1 <letter>0` makes that axis read 0 in G54.
3. Re-check all four zeros after every homing cycle, until the homing
   repeatability of each axis has been measured and is known to be small
   enough for the parts being cut. After that, re-check them whenever a
   reference, a wire point or the wire is changed.

The software cannot check any of this. The Brief 1 commissioning procedure
refers to this section.

```text
              (top view, z out of the page)

      back
   ┌─────────────────────────────────────────┐
   │ ════════ x reference face (x = 0) ═════ │
   │ left tower                  right tower │
   │   │                               │     │
   │   L ───────── wire ───────────────R     │   ──► +y
   │   │                               │     │
   │ y = 0                     y = wire_span │
   └─────────────────────────────────────────┘
      front                 │
                            ▼ +x
```

## 4. Top-level fields

```yaml
format: "foamcutter-machine-profile"
version: 1
name: "..."
axes: [ ... 4 entries ... ]
wire_span: 900.0
limits: { ... }
kerf_tables: [ ... ]
```

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `format` | text | yes | Must be exactly `"foamcutter-machine-profile"`. |
| `version` | integer | yes | Must be `1`. |
| `name` | text | yes | Free name of this machine set-up, written into G-code comments. Non-empty. |
| `axes` | list of 4 axis entries | yes | Entry 1 is axis 1, entry 2 is axis 2, and so on. See §5. |
| `wire_span` | number, mm | yes | Distance along `y` between the left and right wire points, i.e. between the two carriage planes. Must be > 0. |
| `limits` | mapping | yes | Cutting limits. See §6. |
| `kerf_tables` | list | yes | At least one kerf table. See §7. |

There is no table-height field. The table top is `z = 0` by definition (§3.3).

## 5. Axis entries

```yaml
axes:
  - letter: "X"      # axis 1: left tower, horizontal
    min: 5.0
    max: 600.0
    max_rate: 1200.0
    lead: 8.0
  - letter: "Y"      # axis 2: left tower, vertical
    ...
```

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `letter` | text, 1 character | yes | G-code letter for this axis. One of `X Y Z A B C U V W`, upper case. The four letters must all be different. Default set-up: `"X" "Y" "Z" "A"` for axes 1 to 4. |
| `min` | number, mm | yes | Lowest allowed G54 position of this axis (soft limit). |
| `max` | number, mm | yes | Highest allowed G54 position of this axis (soft limit). Must be > `min`. |
| `max_rate` | number, mm/min | yes | Highest allowed axis speed. Must be > 0. Every feed move in a program must keep this axis at or below this speed. |
| `lead` | number, mm/rev | yes | Leadscrew lead of this axis. Must be > 0. Not used by the CAM or the simulator; recorded here because the profile is the single record of the machine. Steps per mm on the controller = (full steps per revolution × microsteps) / `lead`. |

`min` and `max` are in the same G54 frame as every program. Measure them after
the zeros are set: jog the axis to the end of its safe travel at each end and
read its G54 position. Soft limits in this profile are checked by the
software. They are independent of any soft limits set in the controller.

Axis roles are fixed:

| Entry | Axis | Tower | Moves the wire point along |
| --- | --- | --- | --- |
| 1 | axis 1 | left | `x` |
| 2 | axis 2 | left | `z` |
| 3 | axis 3 | right | `x` |
| 4 | axis 4 | right | `z` |

## 6. Limits

```yaml
limits:
  max_wire_angle: 30.0
  max_speed_ratio: 3.0
```

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `max_wire_angle` | number, degrees | yes | Largest allowed angle between the wire and the `y` axis, at any position in a program. Must be ≥ 0 and < 90. |
| `max_speed_ratio` | number | yes | Largest allowed ratio between the faster and the slower wire speed at the two foam faces, for any feed move. Must be ≥ 1. |

Definitions used by every tool:

- **Wire angle** at axis positions `a1 … a4`:
  `angle = atan( sqrt((a3 − a1)² + (a4 − a2)²) / wire_span )`, in degrees.
- **Wire speed at a plane** `y = c` during a move: the wire meets the plane at
  the point `P(c) = L + (c / wire_span) · (R − L)`. The speed at that plane is
  the distance between `P(c)` at the start and at the end of the move, divided
  by the move duration. Because every feed move is a straight line in axis
  space, this point moves in a straight line at constant speed.
- **Speed ratio** of a move: the larger of the two foam-face speeds divided by
  the smaller. If one is zero and the other is not, the ratio is infinite. The
  foam faces are defined by the job (see the job file format, §5).

## 7. Kerf tables

The kerf is the full width of the slot that the hot wire melts, measured
perpendicular to the wire's direction of travel. It depends on the foam, the
wire temperature and the speed of the wire through the foam. Each table holds
measured values for one foam and one wire heat setting. The job file selects
one table by name.

```yaml
kerf_tables:
  - name: "eps-20-wire-3.0A"
    description: "EPS 20 kg/m3, wire supply at 3.0 A"
    points:
      - { speed: 60.0,  kerf: 1.60 }
      - { speed: 120.0, kerf: 1.10 }
      - { speed: 240.0, kerf: 0.80 }
```

| Field | Type | Required | Meaning |
| --- | --- | --- | --- |
| `name` | text | yes | Unique within the profile. Non-empty. |
| `description` | text | yes | What foam and heat setting the table was measured for. May be empty (`""`). |
| `points` | list | yes | At least 2 entries, each with `speed` and `kerf`. |
| `points[].speed` | number, mm/min | yes | Wire speed at the foam. Must be > 0. Strictly increasing down the list. |
| `points[].kerf` | number, mm | yes | Full kerf width at that speed. Must be > 0. |

**Lookup rule.** For a speed `s`:

- If `s` equals a table speed, the kerf is that entry's kerf.
- If `s` lies between two consecutive table speeds `s0 < s < s1` with kerfs
  `k0`, `k1`, the kerf is `k0 + (k1 − k0) · (s − s0) / (s1 − s0)`.
- If `s` is below the first or above the last table speed, the lookup fails.
  The tool must stop with an error that names the table, the speed `s` and the
  table's speed range (first and last table speeds). For example:
  `kerf table "eps-20-wire-3.0A": speed 31.2 mm/min is outside the table range 60.0 to 240.0 mm/min`.
  It must not extrapolate or clamp.

## 8. Validation

A profile is valid only if every rule in §4 to §7 holds. A validator reports
every violation it finds, each with the field path (for example
`axes[3].max`) and the reason. Tools refuse to use an invalid profile.

## 9. Complete example

See `examples/profiles/sample-machine.yaml`. It is a sample only; its numbers
are not measurements of the real machine.
