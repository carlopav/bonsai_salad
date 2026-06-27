/// DXF/DWG writer: converts ElementBlocks + HatchRegions → acadrust document.
///
/// Output convention:
///   • IFC type (or element for standalone) → one DXF BLOCK (name = type name or GlobalId).
///   • Block entities are on layer "0" so they inherit INSERT's colour/lineweight.
///   • Each IFC element instance → one INSERT on the IFC class layer (e.g. "IfcWall").
///   • INSERT position = projected world origin of the element's ObjectPlacement.
///   • INSERT rotation = element rotation in the camera plane.
///   • Coordinates are real-world metres (1:1).  $INSUNITS = 6 (Metres).
///   • Hatch regions are written directly to model space on BBIM_HATCH_ layers.

use acadrust::{
    CadDocument, DxfWriter, DxfVersion, TableEntry,
    entities::{EntityType, Line, Arc, Circle, Ellipse, LwPolyline, LwVertex,
               Hatch, BoundaryPath, BoundaryEdge, PolylineEdge, Insert},
    tables::{Layer, BlockRecord, LineType},
    types::{Color, LineWeight, Vector3, Vector2},
};
use crate::geometry::{DxfEdge, EdgeKind};
use crate::geometry::hatch::HatchRegion;
use crate::pipeline::{ElementBlock, LayerStyle};
use glam::DVec2;
use std::path::Path;
use std::collections::{HashSet, HashMap};

// IFC class layer: colour 7 (white/black in AutoCAD), medium line weight
const COLOR_IFC_CLASS:  i16 = 7;
const LW_IFC_CLASS:     i16 = 35;   // 0.35 mm
const COLOR_HATCH:      i16 = 254;  // near-white / light grey
const LW_HATCH:         i16 = 13;   // 0.13 mm (thin)

/// Write element blocks, hatches, and flat wall edges to DXF or DWG.
///
/// Draw order (back → front):
///   1. wall_hatches  — fill behind wall outlines
///   2. element blocks (non-wall, Bucket B/C)
///   3. flat_walls    — wall outlines (Section + View LWPOLYLINE)
///   4. element_hatches — material hatches from non-wall elements
pub fn write_document(
    element_blocks: &[ElementBlock],
    element_hatches: &[HatchRegion],
    wall_hatches: &[HatchRegion],
    flat_walls: &[DxfEdge],
    output_path: &str,
    dxf_version: &str,
    layer_styles: &HashMap<String, LayerStyle>,
) -> anyhow::Result<()> {
    let version = parse_version(dxf_version);
    let mut doc = CadDocument::with_version(version);

    doc.header.insertion_units = 6;
    doc.header.measurement      = 1;

    let all_hatches: Vec<&HatchRegion> = wall_hatches.iter().chain(element_hatches.iter()).collect();
    ensure_layers(&mut doc, element_blocks, &all_hatches, flat_walls, layer_styles);

    // 1. Wall hatches first — drawn behind everything
    for hatch in wall_hatches {
        write_hatch(&mut doc, hatch)?;
    }

    // 2. Non-wall element blocks (Bucket B/C)
    for block in element_blocks {
        if !block.edges.is_empty() {
            write_element_block(&mut doc, block)?;
        }
    }

    // 3. Wall outlines (Section + View LWPOLYLINE) — above hatches
    for edge in flat_walls {
        let mut entity = edge_to_entity(edge);
        entity.common_mut().layer = edge.layer.clone();
        doc.add_entity(entity)?;
    }

    // 4. Non-wall element hatches
    for hatch in element_hatches {
        write_hatch(&mut doc, hatch)?;
    }

    let path = Path::new(output_path);
    match path.extension().and_then(|e| e.to_str()) {
        Some("dwg") => {
            use acadrust::io::dwg::DwgWriter;
            DwgWriter::write_to_file(output_path, &doc)?;
        }
        _ => {
            DxfWriter::new(&doc).write_to_file(output_path)?;
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Layer management
// ---------------------------------------------------------------------------

fn ensure_linetype(doc: &mut CadDocument, name: &str) {
    if name.eq_ignore_ascii_case("continuous") || name.is_empty() { return; }
    if doc.line_types.contains(name) { return; }
    let mut lt = match name.to_lowercase().as_str() {
        "dashed"  => LineType::dashed(),
        "dotted"  | "dot" => LineType::dotted(),
        other => LineType::new(other),
    };
    lt.set_handle(doc.allocate_handle());
    let _ = doc.line_types.add(lt);
}

fn ensure_layers(
    doc: &mut CadDocument,
    element_blocks: &[ElementBlock],
    hatches: &[&HatchRegion],
    flat_walls: &[DxfEdge],
    layer_styles: &HashMap<String, LayerStyle>,
) {
    let mut layer_names: HashSet<String> = HashSet::new();

    for b in element_blocks {
        if !b.edges.is_empty() {
            layer_names.insert(b.ifc_class.clone());
        }
    }
    for h in hatches {
        layer_names.insert(h.layer.clone());
    }
    for e in flat_walls {
        layer_names.insert(e.layer.clone());
    }

    for name in layer_names {
        if doc.layers.contains(&name) { continue; }

        let (color, lw, linetype) = if let Some(s) = layer_styles.get(&name) {
            (s.color, s.lineweight, s.linetype.clone())
        } else if name.starts_with("BBIM_HATCH") || name.ends_with("_Hatches") {
            (COLOR_HATCH, LW_HATCH, "Continuous".to_string())
        } else {
            (COLOR_IFC_CLASS, LW_IFC_CLASS, "Continuous".to_string())
        };

        ensure_linetype(doc, &linetype);

        let mut layer = Layer::new(&name);
        layer.set_handle(doc.allocate_handle());
        layer.color       = Color::from_index(color);
        layer.line_weight = LineWeight::from_value(lw);
        layer.line_type   = linetype;
        let _ = doc.layers.add(layer);
    }
}

// ---------------------------------------------------------------------------
// Element → BLOCK + INSERT
// ---------------------------------------------------------------------------

fn write_element_block(doc: &mut CadDocument, block: &ElementBlock) -> anyhow::Result<()> {
    let block_name = &block.block_name;
    let ifc_class  = &block.ifc_class;

    // 1. Create and register a BlockRecord
    let mut br = BlockRecord::new(block_name);
    let br_handle = doc.allocate_handle();
    br.set_handle(br_handle);
    br.block_entity_handle = doc.allocate_handle();
    br.block_end_handle    = doc.allocate_handle();
    br.units = 6; // Metres
    doc.block_records.add(br).ok();

    // 2. Add geometry entities to the block on layer "0", properties BYBLOCK
    //    so they inherit color/linetype/lineweight from the INSERT.
    for edge in &block.edges {
        let mut entity = edge_to_entity(edge);
        {
            let c = entity.common_mut();
            c.layer        = "0".to_string();
            c.color        = Color::ByBlock;
            c.line_weight  = LineWeight::ByBlock;
            c.linetype     = "BYBLOCK".to_string();
            c.owner_handle = br_handle;
        }
        doc.add_entity(entity)?;
    }

    // 3. One INSERT per element instance, on the IFC class layer
    for ins in &block.inserts {
        let mut insert = Insert::new(block_name, Vector3::new(ins.position.x, ins.position.y, 0.0));
        insert.rotation   = ins.rotation;
        insert.common.layer = ifc_class.clone();
        doc.add_entity(EntityType::Insert(insert))?;
    }

    Ok(())
}

// ---------------------------------------------------------------------------
// DxfEdge → acadrust EntityType
// ---------------------------------------------------------------------------

fn edge_to_entity(edge: &DxfEdge) -> EntityType {
    match &edge.kind {
        EdgeKind::Line { start, end } => {
            EntityType::Line(Line::from_coords(
                start.x, start.y, 0.0,
                end.x,   end.y,   0.0,
            ))
        }

        EdgeKind::Circle { center, radius } => {
            let mut c = Circle::new();
            c.center = Vector3::new(center.x, center.y, 0.0);
            c.radius = *radius;
            EntityType::Circle(c)
        }

        EdgeKind::Arc { center, radius, start_angle, end_angle } => {
            let mut a = Arc::new();
            a.center      = Vector3::new(center.x, center.y, 0.0);
            a.radius      = *radius;
            a.start_angle = *start_angle;
            a.end_angle   = *end_angle;
            EntityType::Arc(a)
        }

        EdgeKind::Ellipse { center, major_axis, ratio, start_param, end_param } => {
            let mut e = Ellipse::new();
            e.center           = Vector3::new(center.x, center.y, 0.0);
            e.major_axis       = Vector3::new(major_axis.x, major_axis.y, 0.0);
            e.minor_axis_ratio = *ratio;
            e.start_parameter  = *start_param;
            e.end_parameter    = *end_param;
            EntityType::Ellipse(e)
        }

        EdgeKind::Polyline { points, closed } => {
            let mut poly = LwPolyline::new();
            poly.is_closed = *closed;
            for p in points {
                poly.vertices.push(LwVertex::from_coords(p.x, p.y));
            }
            EntityType::LwPolyline(poly)
        }

        EdgeKind::Spline { points } => {
            // Approximate spline as LwPolyline
            let mut poly = LwPolyline::new();
            for p in points {
                poly.vertices.push(LwVertex::from_coords(p.x, p.y));
            }
            EntityType::LwPolyline(poly)
        }
    }
}

// ---------------------------------------------------------------------------
// Hatch → model space
// ---------------------------------------------------------------------------

fn write_hatch(doc: &mut CadDocument, region: &HatchRegion) -> anyhow::Result<()> {
    let mut hatch = Hatch::new();
    hatch.common.layer   = region.layer.clone();
    hatch.pattern.name   = region.pattern.name.clone();
    hatch.pattern_scale  = region.pattern.scale;
    hatch.pattern_angle  = region.pattern.angle;
    hatch.is_associative = false;

    hatch.paths.push(polyline_boundary(&region.exterior));
    for hole in &region.interiors {
        hatch.paths.push(polyline_boundary(hole));
    }

    doc.add_entity(EntityType::Hatch(hatch))?;
    Ok(())
}

fn polyline_boundary(pts: &[DVec2]) -> BoundaryPath {
    let verts: Vec<Vector2> = pts.iter().map(|p| Vector2::new(p.x, p.y)).collect();
    let polyedge = PolylineEdge::new(verts, true);
    let mut path = BoundaryPath::new();
    path.add_edge(BoundaryEdge::Polyline(polyedge));
    path
}

// ---------------------------------------------------------------------------
// Version helper
// ---------------------------------------------------------------------------

fn parse_version(s: &str) -> DxfVersion {
    match s {
        "AC1009" | "AC1012" => DxfVersion::AC1012,
        "AC1014"            => DxfVersion::AC1014,
        "AC1015"            => DxfVersion::AC1015,
        "AC1018"            => DxfVersion::AC1018,
        "AC1021"            => DxfVersion::AC1021,
        "AC1024"            => DxfVersion::AC1024,
        "AC1027"            => DxfVersion::AC1027,
        "AC1032"            => DxfVersion::AC1032,
        _                   => DxfVersion::AC1027,
    }
}
