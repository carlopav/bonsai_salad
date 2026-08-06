# Rapporti aeroilluminanti

Checks, for every `IfcSpace`, the ratio between its window area and its net floor
area — one ratio for daylight, one for ventilation — and writes the result as an
ODS schedule. Lives under **Schedules > Rapporti aeroilluminanti**.

## What it computes and what you decide

The tool measures what geometry can settle and leaves the regulation to you.
Every quantity exists twice: a computed property it always rewrites, and an
override property only you write. An absent override means the computed value
applies, so recalculating can never destroy a correction.

- **Luce architettonica** — the smallest section of the opening taken across the
  wall's thickness: the hole you see looking at the wall head-on. A void that
  overshoots the wall faces, or that is cut for a sill, cannot inflate it. An
  opening filled by several windows splits it equally among them.
- **Superficie illuminante / aerante** on the window — your override. Left empty,
  the whole clear opening counts for both.
- **Requisito illuminazione / aerazione** on the room — the ratio it has to
  reach, 0.125 unless you set otherwise. `0` marks a room the regulation asks
  nothing of.

## The room a window serves

Recorded as `IfcRelSpaceBoundary`, and only ever created where none exists. A
boundary already in the file is the association, whoever wrote it. A window
counts for a room when its boundary to that room is `EXTERNAL`.

That is also how you correct the tool. A loggia modelled as an `IfcSpace` makes
its window `INTERNAL` on both sides; open Bonsai's Boundary module, set the one
facing the room to `EXTERNAL`, delete the other, and nothing will overwrite it.

Windows moved after a calculation keep their old boundary. The panel reports
those whose boundary names a room the geometry does not put them near, and
**Aggiorna** replaces them. A boundary you corrected by hand is never reported.

## Not yet implemented

The default is that the whole clear opening counts for both ratios. Discounting
the part below 0.80 m from the floor for daylight, and the fixed panels for
ventilation, are aids to overriding that are not built yet.
