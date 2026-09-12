# Rapporti aeroilluminanti

Checks, for every room, the ratio between its window area and its net floor
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
  the whole clear opening counts for both. **Prepara override** writes the
  measured clear opening into both properties of the selected windows and doors,
  so there is a value to edit in Bonsai's property panel instead of a property
  name to type from nothing. It never touches an override already written, and a
  window with no clear opening yet has nothing to copy. Once prepared, those two
  areas are yours: the calculation keeps measuring the clear opening, but it no
  longer reaches the count for that window.
- **Requisito illuminazione / aerazione** on the room — the ratio it has to
  reach, 0.125 unless you set otherwise. `0` marks a room the regulation asks
  nothing of. Setting it drops the room's stored `Verificato` and
  `Superficie minima aeroilluminante`: both were reached against the requirement
  you just replaced, and only a recalculation can earn new ones.
- **Superficie minima aeroilluminante** on the room — the requirement in square
  metres rather than as a ratio, rewritten on every calculation. It is there so
  a schedule can print the bar as a column instead of working it out from the
  ratio and the floor: the room schedule under **Schedules > IfcSpaces** reads
  exactly this. Only where one area answers for the room — where illuminazione
  and aerazione ask for different fractions there are two, and where the
  regulation asks nothing there is none, so the property is absent and the
  schedule prints a dash.

A window's clear opening can fail to measure. The void has no body, or it does
not overlap its host along the wall's thickness — a broken or unusual model — or
the window fills no void at all: placed straight into the model, or exported as a
curtain wall panel, which is common in an imported file. Whatever the reason,
such a window contributes nothing, and every one of them that bounds a room is
counted apart from the orphans, under `serramenti non misurati` — a window that
bounds no room is an orphan instead, and no room is waiting on it. Rather than
give the room it serves a false pass or fail, the tool withholds its verdict
entirely — no `Verificato` in the property set, `da verificare` in the ODS
table, counted in the panel as `locali senza verdetto` and never among the ones
that failed — until you look at the flagged window and either fix its geometry, or,
if you can't, type its area by hand as a `Superficie illuminante` /
`Superficie aerante` override, which is exactly what the override is for. An
area you typed is a known area: fill in **both**, recalculate, and the room gets
its verdict back. One of the two leaves the other side unknown, and the verdict
stays withheld.

## The exported table

One row per room, grouped by storey: identifier, name, net floor area, the area
required, the lighting area, the ventilation area, the verdict.

The requirement is printed as the square metres the room has to reach, not as
the ratio it is taken over, so the row is read across: what it needs, then what
it has. One value where the two requirements agree, two — illuminazione first —
where they do not, and a dash for a room the regulation asks nothing of.

Required area and verdict are formulas over the net floor area beside them:
correct an area in the sheet and both follow.

The table no longer carries the measured **Luce architettonica** next to the
counted areas, so a corrected area and a measured one read alike there. The
panel still marks the difference on the window's line, as `≠ misurato`.

## The room a window serves

Recorded as `IfcRelSpaceBoundary`, and only ever created where none exists. A
boundary already in the file is the association, whoever wrote it. A window
counts for a room when its boundary to that room is `EXTERNAL`.

An `IfcSpace` whose `PredefinedType` is `EXTERNAL` — a balcony, a loggia, a
portico, a terrace — is outdoor space, not a room; `PARKING` is a parking bay and
`GFA` a floor-area overlay, which are not rooms either. They are left out of the
check entirely: nothing is measured over them, they get no requirement and no row
in the table. A window between a room and one of them gets a single `EXTERNAL`
boundary, to the room, and counts for it exactly like one giving onto open air.

Every other value is a room, including `USERDEFINED` and a type left unset: a
project that carries its own classification, or an exporter that writes none,
still has rooms, and dropping them from the check would say nothing on screen.
A room the regulation asks nothing of — a garage, a cellar — is handled by
setting its requirement to `0`, where the table still shows it.

You can still correct any pairing by hand in Bonsai's Boundary module — set the
boundary facing the room to `EXTERNAL`, delete the other — and nothing will
overwrite it.

Windows moved after a calculation keep their old boundary. The panel reports
those whose boundary names a room the geometry does not put them near, and
**Aggiorna** replaces them. A boundary naming a space that is not a room is
reported too: it was written when the tool still took a balcony for one, and it stands
between the window and the only room it serves — so a file calculated before this
rule needs one **Aggiorna** to pick up the windows onto its loggias. The property
sets those spaces already carry are left where they are: nothing but **Aggiorna**
deletes anything. A boundary you corrected by hand is never reported.
A boundary written by another authoring tool — one carrying a contact surface,
or a 2nd level one paired with the boundary on the other side — is left
untouched even when it is reported: **Aggiorna** says how many it left alone,
because neither the surface nor the pairing is something this tool could write
back. Such a filling is reported again on every run, and left alone again every
time. That count never falling is not the button failing: the disagreement is
real, and settling it means editing the boundary yourself in Bonsai's Boundary
module, or moving the window until the geometry agrees with it.

## Diagnostica associazioni

The pairing between a room and its windows drives every ratio, and nothing else on screen shows
it. **Diagnostica associazioni** colours each room and the openings counted for it alike, so a
mismatch is visible in one look over the floor plan. Red marks everything the check knows about
and does not count: an internal or orphan opening, one whose area could not be established, and a
room left without a single valid one. Outdoor space is neither coloured nor reddened: the check
says nothing about a balcony. An opening that legitimately counts for two rooms — an
external, measured boundary to both — is coloured in each; on screen it ends up in whichever
room's colour was painted last, which is accepted rather than fixed. It writes nothing to the IFC.
Switching off uses Bonsai's own Reset Colours, which whitens every visible object, not only the
ones this tool coloured.

## Not yet implemented

The default is that the whole clear opening counts for both ratios. Discounting
the part below 0.80 m from the floor for daylight, and the fixed panels for
ventilation, are aids to overriding that are not built yet.
