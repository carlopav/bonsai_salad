# Bonsai Salad

This repo contains a collection of random scripts that help me with my everyday work with Bonsai and IfcOpenshell.
The scripts are developed with the support of Cloud Code.
I Share them in the hope they can be useful and that they will be integrated into IfcOpenShell in the future.

## License
Gpl 3.

## More info
Each tool has it's own README.md file you can reference. 

# Sheets to pdf
Convert all existing sheets to pdf.
Require Typst available in python blender. You can install it by getting https://extensions.blender.org/add-ons/typst-importer/

# Ifc_dxf
The goal is to export a fully editable and clean dxf from a bonsai drawing view, including cuts, hatches and annotations.
Geometries should be kept as originals, without tassellation.
Dxf layer structure does match Ifc Classes, and can be customized using a template dxf created with your favourite dxf editor.

# Dxf_ifc
The inverse of ifc_dxf: import a DXF file as an IFC representation directly on a selected Bonsai element.
DXF entities (lines, polylines, arcs, circles, ellipses, splines, hatches, inserts, text) are converted to native IFC geometry (IfcPolyline, IfcTrimmedCurve, IfcCircle, IfcMappedItem, etc.) and assigned to the chosen representation subcontext (default: Plan / Annotation / PLAN_VIEW).
Layer colours and lineweights are preserved as IfcPresentationLayerWithStyle.
DXF metadata can optionally be stored in a Pset_DXFSource property set for round-trip traceability.