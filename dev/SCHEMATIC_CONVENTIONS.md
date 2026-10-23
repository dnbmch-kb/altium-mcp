# Generating a readable schematic

Rules for programmatic schematic generation, each one learned by getting it
wrong and having a PCB designer point at the result. An agent that follows
these should not repeat the mistakes. How a finished house sheet looks, measured
from the operator's SCB, ToF master and Phytec carrier, is [`schematic_style.md`](../../%23Notes/guides/schematic_style.md)
(c:/Projects/#Notes/guides/schematic_style.md); sections 3, 4, 7, 9, 14 and 15
here follow it.

Tooling: a circuit spec (part data from
[`make_part_spec.py`](make_part_spec.py)) →
[`build_circuit.pas`](build_circuit.pas) (build) →
[`check_connectivity.pas`](check_connectivity.pas) (verify), with
[`harvest_param_placement.pas`](harvest_param_placement.pas) as a one-time
setup step per library.

## 1. A pin's connection point is not `Pin.Location`

`ISch_Pin.Location` is the end attached to the component **body**. The
electrical end is `PinLength` further along `Pin.Orientation`
(`0/1/2/3` = right/up/left/down):

```pascal
x := CoordToMils(Pin.Location.X);  y := CoordToMils(Pin.Location.Y);
len := CoordToMils(Pin.PinLength);
if (Pin.Orientation = 0) then x := x + len
else if (Pin.Orientation = 1) then y := y + len
else if (Pin.Orientation = 2) then x := x - len
else if (Pin.Orientation = 3) then y := y - len;
```

`Pin.Orientation` already composes with the component's rotation — do not add
the parent's orientation on top.

Verified against a hand-wired production sheet
([`verify_pin_convention.pas`](verify_pin_convention.pas)): wire endpoints land
on `Location + PinLength` **119** times and on `Location` **0** times.

Wiring to `Location` instead draws the wire through the pin and out the far
side. It looks attached, leaves the pin unconnected, and shorts any neighbour
sharing that connection column.

## 2. Never run a riser in a pin's connection column

All of a symbol's pins on one side usually share an x. A vertical wire in that
column passes through every one of them on its way past. Offset risers by at
least 100 mil, and prefer 200–300 for readability.

## 3. A rail runs through a capacitor's top pin

A shunt capacitor stands vertical on the rail it serves, and the rail runs
through its top pin end with no stub between (the SCB does it for 25 of 32
vertical capacitors, the ToF master 21 of 29; style guide section 6). The tap
gap of one grid stays only where a rail cannot pass the pin, and a junction sits
directly on a pin only at such a tee:

```
rail  ──────┬──────┬──────      the rail through C1's and C2's top pin ends
           C1     C2            400 mil apart
            ┴      ┴            GND port on each bottom pin
```

## 4. Ground is one grid across, one grid down; a capacitor row grounds at the bottom

Not three. `U1.GND → 100 left → 100 down → GND port`.

A row of capacitors on a rail stands vertical, 400 mil apart (the median over
every adjacent pair in the house sheets), the first 450-550 mil from the IC
body, inputs left of a regulator and outputs right of it. Each grounds at its
bottom pin: either a GND port right on the pin (ToF 15, SCB 8, MCB 9), or one
short GND wire under the row to a single port (ToF 11, SCB 10).

## 5. Rails belong next to the pins they serve

Floating a rail 1000 mil above the part to "keep it clear" just wastes sheet.
Put the rail one pin-row above the highest pin it connects, and route signals
straight out of their pin's own height wherever possible:

```
U1 pin rows:  FB 3000, VOUT 3300, SW 3600   ->   input rail 4100
LED+ leaves VOUT at 3300 and runs straight to J1 - no riser at all
LED- leaves FB at 3000, one riser near the connector, done
```

A signal that leaves a pin, jogs down, runs across and comes back up is a
routing failure, not a style choice.

## 6. A wire must never cross a component body

A shunt part hanging off a rail occupies vertical space *below* it. If another
net runs through that band, the wire is drawn straight through the symbol - it
looks connected to the part and is not.

Concretely: LED+ at y=3300 with C2 hanging 300 mil below it occupies
y=2900..3200, and LED- ran at y=3000. The fix was not to bend the wire but to
turn LED- down to the left of C2 and drop LED+ to its right, so neither
crosses.

Before placing a shunt, check what else routes through the band it will
occupy. `check_connectivity.pas` now tests every axis-aligned segment against
each component's DRAWN body (graphics only - wires legitimately end on pins,
which stick out past the body).

## 7. Power ports: GND is power ground with its name hidden

| Net | Style | Orientation | ShowNetName |
|---|---|---|---|
| GND | **4** power ground | 3 (down) | False |
| another ground net (GND_ISO, ...) | 4 | 3 (down) | True |
| supply rails | **2** `ePowerBar` | 1 (up) | True |

Measured in the house sheets (style guide section 5): power ground pointing
down with no name on 27 SCB, 40 ToF, 28 MCB and 73 Elkbox ports; a supply is a
bar pointing up with its name, 32 of 32 on both the SCB and the ToF master. A
port never points sideways: a power pin pointing the other way gets a jog
beside it, or a label. `ePowerGndPower`=4, `ePowerGndSignal`=5 and
`ePowerGndEarth`=6 are easy to confuse, and the wrong one looks plausible.

On style 5, `ShowNetName := False` read back False and Altium still drew "GND"
below the symbol, so a style-5 ground occupied about 300 mil below itself. The
ToF master's print shows style-4 ports with no text; whether the script's
`ShowNetName := False` hides the name on style 4 is the live check to run
before relying on it.

## 8. Net labels must touch the wire

A label near a wire names nothing. Place it **on** a wire coordinate.
`check_connectivity.pas` tests each label against every segment (endpoint or
interior) and reports any that miss.

Caveat: that proves the geometric precondition, not the compiled net name.
Confirming the net Altium actually assigns needs the project compiled, which a
free document cannot do.

## 9. Parameter text: harvest it, or use the house offsets

Placement is an offset from the component's `Location`, plus `Justification`
and `Orientation` — the method in the user's `CopyParamPlacement.pas`.
**`Justification` is what makes a column line up**; setting position alone
leaves it ragged, and setting `Orientation`/`Autoposition` alone does nothing
visible at all.

`harvest_param_placement.pas` reads a hand-drawn sheet and records, per
`LibReference`:

```
PLACE|CAP-NP|1|DESIGNATOR|0|300|2|0      # dx, dy, justification, orientation
PLACE|CAP-NP|1|VALUE|0|200|2|0
```

For a library with no harvest, the house offsets are the defaults (style guide
section 8; all text Times New Roman 10, bottom-left justified, horizontal even on
a rotated part):

| Part | Designator | Second line |
|---|---|---|
| two-pin, vertical | a two-line block starting 10 mil right of the body, the upper baseline on the part's centre line + 10 | 100 mil below the upper line |
| two-pin, horizontal | baseline 10 mil above the body, flush with its left end | value, baseline 110 mil below the body |
| IC, module, connector | the body's top-left corner, baseline on the top edge | the MPN comment, flush left, baseline 100 mil below the body |

**Visible parameters:** a resistor, capacitor, inductor or ferrite shows its
value only; every other part shows its comment, the MPN. `DNP` shows as a further
line where it applies; everything else (part number, tolerance, manufacturer,
the composed comment such as "CAP 0.10µF 25V 0402") stays hidden.

Treat harvested numbers as relative **spacing**, then translate the block:

- **Clear the body.** Applied raw, a right-justified column anchors at the
  component origin, which for `CAP-NP` is over the plates.
- **Keep the block together.** The designator's `dy` can reach above the top
  pin, leaving it on the far side of a rail from its own parameters.
- **Allocate room for it.** A block is roughly `records × 100` mil tall and
  hangs below the body. Two parts stacked in one column will collide through
  their text long before their symbols touch.

### Measuring the body: not with `BoundingRectangle`

`Component.BoundingRectangle` **includes the parameter text**, so anchoring
text to it feeds back on itself and walks the block off the sheet. Measure the
extent from a child iterator filtered to drawable primitives:

```pascal
Iter.AddFilter_ObjectSet(MkSet(ePin, eLine, eRectangle, eArc, ePolyline, eEllipse));
```

Unfiltered, the iterator returns objects whose `BoundingRectangle` is invalid
and kills the script.

## 10. Mirroring: `Mirror(Axis)`, not `IsMirrored`

| Call | Effect |
|---|---|
| `Component.IsMirrored := True` | flag sets, **pins do not move** — display only |
| `Component.Mirror(Axis)` | real transform; pins moved `+200 → -200` |

`Axis` can be the component's own `Location`. This is the editor's **X** key.
Check which way a symbol's pins already point before rotating: `HEADER-2X1`
points left natively, so rotating it 180° puts the body between the incoming
wires and its own connection points.

## 11. Rotating a part rotates its text

Straighten designator/comment/parameters **after** any parameter-position
reset, not before — the reset re-derives placement from the body and discards
an orientation assigned earlier.

## 12. Verify with something that does not share the builder's assumptions

The original `check_connectivity.pas` computed hot ends with the **same
formula as the builder**. They agreed with each other while both could have
been wrong, and it passed a sheet with two pins wired to dead ends. A checker
that inherits the hypothesis under test catches typos, never systematic error.

What the checker distinguishes now:

| Situation | Verdict |
|---|---|
| wire endpoint on a pin's hot end | connected |
| wire crossing a pin, **junction present** | valid tap (shunt on a rail) |
| wire crossing a pin, no junction | **short** |
| junction with < 3 branches | decoration — usually an overshoot |
| net label not on a wire | **names nothing** |
| wire crossing a drawn body | **routed through a symbol** |

Branch counting: a wire *ending* at a point is one branch, a wire *passing
through* is two, a pin landing there is one more.

## 13. A connection goes on the pin's side and touches nothing

`edit_schematic` drew every label left-justified at a 100 mil stub end and every power port by its net's
fixed direction, whatever way the pin pointed: on a left pin the label ran back over the pins and the body,
JP1's ground port fell onto a label 100 mil below, and the raw-head adapter's 56-part sheet counted 214
overlaps (laser-calib `hw/raw_head_adapter/altium_native/sheet.py`, 2026-10-23). It now places each pin's
connection from the placed symbols, as Altium reports them after placement (`server/sheet_plan.py`), in the
house style (style guide section 4):

| Pin | Drawn |
|---|---|
| signal, pointing right | stub of 60 mil a character + 200; a horizontal label starting 100 mil out, along the stub |
| signal, pointing left | the same stub; the label at its far end, the text running back toward the pin |
| signal, pointing up / down | 100 mil stub, the label horizontal at its end |
| power, pointing the port's way or sideways | at the stub end, 200 or 400 mil further out: a supply up, ground down, the first that touches no body, pin or text; else a label |
| power, pointing against the port's way | a jog of 200 or 300 mil beside the pin, the port at its end pointing the right way |
| neighbouring pins of one part on one power net (100 mil apart, one line) | one stub each, one shared wire, a junction at each inner end, one port |

A port never points sideways. Nothing new may touch a pin end, wire, label or port of another connection: that
joins two nets, so such a spot is never drawn and the op is reported. Text extents are estimates (90 mil high,
70 mil a character; a port glyph's box reaches 10 mil past it, so a glyph ending on a wire counts as touching);
the result's `connections` carries every box and `overlaps` names what still touches.

## 14. Layout

From the house sheets (style guide sections 1-3):

- **One sheet per board**; a further sheet only for a repeated channel, drawn once.
- **Titled blocks.** Each functional block has a title (Roboto 14 or Times 12, bold underlined) 100-200 mil
  above it, flush with its left edge; titles in one row share a baseline.
- **Power on top**, the supply chain reading left to right; interfaces and I/O below; output connectors down
  the right edge.
- **In left, out right; supply above, ground below.** Inside a block the input or controller side is on the
  left and the output or connector side on the right.
- **Spacing.** The median gap from a part to its nearest neighbour is 220-300 mil; capacitors on a rail 400 mil
  apart; blocks side by side 400-600 mil apart, stacked ones 300-850 with the title in the gap.
- **The sheet is sized to the drawing**, with no empty area larger than about a tenth of it.
- **Template:** snap 50 mil, visible grid 100 mil, hotspot 20 mil (`build_schematic` sets it on a new sheet).

## 15. Wiring

- **Wires inside a block** (the `wire` op, from pin end to pin end through given corners): in the house
  sheets 41-57 % of all pins sit on a wire reaching another part's pin. Wires are orthogonal and short.
- **Labels only across blocks.** A label hop spans the sheet (median 5850-7200 mil); a label joining two pins a
  short wire apart is a wire drawn as a label. Each label sits on a stub sized to its text (section 13).
- **Junctions only at tees**, never a four-way, at most two crossings without one.
- **Each block takes its supplies from power ports** (the `port` op places one at a chosen point with its own
  orientation), not from a wire run across from another block.
- **Ports appear only on child sheets.** Every `_P`/`_N` pair takes a Differential Pair directive; every open
  pin a No-ERC marker.

## DelphiScript hazards that bit repeatedly

- **Shared scratch variables.** The sandbox convention (`I1..I5`, `S1..S5`) is
  the single most repeated bug here - THREE times in one session, each time
  silent. Reusing `I1` as a loop counter after storing a coordinate in it gave
  an anchor of `24 - 50 = -26` and flung text off-sheet; reusing `I4`/`I5` for
  bounding-box edges overwrote result counters and reported `1840` pins
  shorted. Every occurrence produced plausible-looking wrong output rather
  than an error. Stash a result before any loop that might reuse its variable
  (`SavedCounts`), and an MCP port should use properly scoped locals.
- `try/except` does **not** catch runtime errors; a bad call leaves the script
  paused in the debugger and every later run silently does nothing. Recover
  with `X2.EXE -REditScript:Stop` (see [`unwedge.py`](unwedge.py)), and verify
  recovery by running a probe rather than assuming.
- Nil is not guarded for you. `GetSchDocumentByPath` returns nil for a document
  that belongs to the project but is not open; the next call kills the script.
- Do not predict a rotated symbol's pin coordinates. Place, dump `pin_map.txt`,
  then route from the measured values.
