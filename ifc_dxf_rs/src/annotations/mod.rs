/// IFC Annotation → DXF native entities
///
/// ObjectType → DXF mapping:
///   TEXT, TEXT_LEADER  → MTEXT / MULTILEADER
///   DIMENSION          → ALIGNED DIMENSION (native DXF entity)
///   GRID               → LINE + CIRCLE (bubble) + MTEXT (label)
///   PLAN_LEVEL         → MTEXT + symbol LINE
///   SECTION_LEVEL      → MTEXT + symbol LINE

use acadrust::{
    CadDocument,
    entities::{
        EntityType, MText, AttachmentPoint, MultiLeader, LeaderRoot, LeaderLine,
        Dimension, DimensionAligned,
        Line, Circle,
    },
    tables::Layer,
    types::{Vector3, Color, LineWeight},
};
use crate::geometry::CameraProjection;
use glam::DVec3;

// ---------------------------------------------------------------------------
// Public annotation data types (filled by Python from Bonsai)
// ---------------------------------------------------------------------------

#[derive(Debug, Clone)]
pub enum AnnotationData {
    Text {
        content: String,
        position: [f64; 3],   // world space
        height: f64,          // metres
        alignment: u8,        // DXF attachment point 1–9
    },
    TextLeader {
        content: String,
        text_pos: [f64; 3],
        leader_points: Vec<[f64; 3]>,
        height: f64,
    },
    Dimension {
        start: [f64; 3],
        end:   [f64; 3],
        offset: f64,   // perpendicular offset from measured line
    },
    Grid {
        start: [f64; 3],
        end:   [f64; 3],
        label: String,
        bubble_radius: f64,
    },
    PlanLevel {
        position: [f64; 3],
        elevation: f64,
        label: String,
    },
}

const LAYER_ANNOT: &str = "BBIM_ANNOTATION";
const LAYER_DIM:   &str = "BBIM_DIMENSION";
const LAYER_GRID:  &str = "BBIM_GRID";

// ---------------------------------------------------------------------------
// Write all annotations to the document
// ---------------------------------------------------------------------------

pub fn write_annotations(
    doc: &mut CadDocument,
    annotations: &[AnnotationData],
    proj: &CameraProjection,
) -> anyhow::Result<()> {
    ensure_annotation_layers(doc);
    for ann in annotations {
        write_annotation(doc, ann, proj)?;
    }
    Ok(())
}

fn write_annotation(
    doc: &mut CadDocument,
    ann: &AnnotationData,
    proj: &CameraProjection,
) -> anyhow::Result<()> {
    match ann {
        AnnotationData::Text { content, position, height, alignment } => {
            let p2 = proj.project(DVec3::from(*position));
            let h_mm = proj.scale_len(*height);
            let mut mtext = MText::new();
            mtext.insertion_point = Vector3::new(p2.x, p2.y, 0.0);
            mtext.height           = h_mm;
            mtext.value            = content.clone();
            mtext.attachment_point = attachment_point(*alignment);
            mtext.common.layer     = LAYER_ANNOT.to_string();
            doc.add_entity(EntityType::MText(mtext))?;
        }

        AnnotationData::TextLeader { content, text_pos, leader_points, height } => {
            let tp = proj.project(DVec3::from(*text_pos));
            let h_mm = proj.scale_len(*height);
            let mut ml = MultiLeader::new();
            ml.common.layer = LAYER_ANNOT.to_string();
            ml.text_height  = h_mm;
            ml.context.text_string = content.clone();
            ml.context.text_location = Vector3::new(tp.x, tp.y, 0.0);
            ml.context.has_text_contents = true;
            let pts: Vec<Vector3> = leader_points.iter()
                .map(|lp| { let p = proj.project(DVec3::from(*lp)); Vector3::new(p.x, p.y, 0.0) })
                .collect();
            let mut root = LeaderRoot::new(0);
            root.add_line(LeaderLine::from_points(0, pts));
            ml.context.leader_roots.push(root);
            doc.add_entity(EntityType::MultiLeader(ml))?;
        }

        AnnotationData::Dimension { start, end, offset } => {
            let s2 = proj.project(DVec3::from(*start));
            let e2 = proj.project(DVec3::from(*end));
            let s3 = Vector3::new(s2.x, s2.y, 0.0);
            let e3 = Vector3::new(e2.x, e2.y, 0.0);
            let mut dim = DimensionAligned::new(s3, e3);
            dim.base.common.layer = LAYER_DIM.to_string();
            let mid = (s2 + e2) / 2.0;
            let perp = (e2 - s2).perp().normalize() * *offset;
            dim.base.text_middle_point = Vector3::new(mid.x + perp.x, mid.y + perp.y, 0.0);
            doc.add_entity(EntityType::Dimension(Dimension::Aligned(dim)))?;
        }

        AnnotationData::Grid { start, end, label, bubble_radius } => {
            let s2 = proj.project(DVec3::from(*start));
            let e2 = proj.project(DVec3::from(*end));
            let mut line = Line::from_coords(s2.x, s2.y, 0.0, e2.x, e2.y, 0.0);
            line.common.layer = LAYER_GRID.to_string();
            doc.add_entity(EntityType::Line(line))?;
            let r_mm = proj.scale_len(*bubble_radius);
            let mut circle = Circle::new();
            circle.center = Vector3::new(e2.x, e2.y, 0.0);
            circle.radius = r_mm;
            circle.common.layer = LAYER_GRID.to_string();
            doc.add_entity(EntityType::Circle(circle))?;
            let mut mtext = MText::new();
            mtext.insertion_point = Vector3::new(e2.x, e2.y, 0.0);
            mtext.height           = r_mm * 0.6;
            mtext.value            = label.clone();
            mtext.attachment_point = AttachmentPoint::MiddleCenter;
            mtext.common.layer     = LAYER_GRID.to_string();
            doc.add_entity(EntityType::MText(mtext))?;
        }

        AnnotationData::PlanLevel { position, elevation, label } => {
            let p2 = proj.project(DVec3::from(*position));
            let text = format!("{} ({:.3})", label, elevation);
            let mut mtext = MText::new();
            mtext.insertion_point = Vector3::new(p2.x, p2.y, 0.0);
            mtext.height           = 2.5;
            mtext.value            = text;
            mtext.attachment_point = AttachmentPoint::MiddleLeft;
            mtext.common.layer     = LAYER_ANNOT.to_string();
            doc.add_entity(EntityType::MText(mtext))?;
        }
    }
    Ok(())
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn ensure_annotation_layers(doc: &mut CadDocument) {
    for name in [LAYER_ANNOT, LAYER_DIM, LAYER_GRID] {
        if !doc.layers.contains(name) {
            let mut layer = Layer::new(name);
            layer.color = Color::from_index(2);
            layer.line_weight = LineWeight::from_value(13);
            let _ = doc.layers.add(layer);
        }
    }
}

fn attachment_point(code: u8) -> AttachmentPoint {
    match code {
        1 => AttachmentPoint::TopLeft,
        2 => AttachmentPoint::TopCenter,
        3 => AttachmentPoint::TopRight,
        4 => AttachmentPoint::MiddleLeft,
        5 => AttachmentPoint::MiddleCenter,
        6 => AttachmentPoint::MiddleRight,
        7 => AttachmentPoint::BottomLeft,
        8 => AttachmentPoint::BottomCenter,
        9 => AttachmentPoint::BottomRight,
        _ => AttachmentPoint::MiddleLeft,
    }
}
