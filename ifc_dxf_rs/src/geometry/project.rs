use glam::{DVec2, DVec3, DMat4};
use std::f64::consts::PI;

use super::{CameraProjection, DxfEdge, layer_name};

// ---------------------------------------------------------------------------
// Profile → edges  (Bucket A: IfcExtrudedAreaSolid without booleans)
// ---------------------------------------------------------------------------

/// Flat 2D profile data passed from Python after reading the IFC schema.
#[derive(Debug, Clone)]
pub enum ProfileData {
    /// IfcArbitraryClosedProfileDef / IfcPolyline
    Polygon {
        outer: Vec<[f64; 2]>,
        holes: Vec<Vec<[f64; 2]>>,
    },
    /// IfcArbitraryClosedProfileDef / IfcCompositeCurve with arc segments
    CompositeCurve {
        segments: Vec<CurveSegment>,
    },
    /// IfcRectangleProfileDef
    Rectangle {
        x_dim: f64,
        y_dim: f64,
        px: f64, py: f64, angle: f64,  // placement
    },
    /// IfcCircleProfileDef
    Circle {
        radius: f64,
        px: f64, py: f64,
    },
    /// IfcCircleHollowProfileDef
    CircleHollow {
        radius: f64,
        wall_thickness: f64,
        px: f64, py: f64,
    },
    /// IfcEllipseProfileDef
    Ellipse {
        semi1: f64, semi2: f64,
        px: f64, py: f64, angle: f64,
    },
    /// Parametric I/L/T/U — points already computed by Python
    Parametric {
        points: Vec<[f64; 2]>,
    },
}

#[derive(Debug, Clone)]
pub enum CurveSegment {
    Line { start: [f64; 2], end: [f64; 2] },
    Arc  { center: [f64; 2], radius: f64, start_angle: f64, end_angle: f64 },
}

/// Convert a profile (in local 2D coords) to DxfEdges using the
/// element's world transformation matrix and camera projection.
///
/// `world_m4` transforms profile local (x,y,0) → world 3D.
pub fn profile_to_edges(
    profile: &ProfileData,
    world_m4: &DMat4,
    proj: &CameraProjection,
    ifc_class: &str,
    material: &str,
) -> Vec<DxfEdge> {
    let layer = layer_name(ifc_class, true);
    let mut edges = Vec::new();

    // Helper: profile (x,y) → paper (u,v)
    let pt = |x: f64, y: f64| -> DVec2 {
        let w = world_m4.transform_point3(DVec3::new(x, y, 0.0));
        proj.project(w)
    };

    match profile {
        ProfileData::Polygon { outer, holes } => {
            let pts: Vec<DVec2> = outer.iter().map(|p| pt(p[0], p[1])).collect();
            edges.push(DxfEdge::polyline(pts, true, &layer, ifc_class, material, true));
            for hole in holes {
                let pts: Vec<DVec2> = hole.iter().map(|p| pt(p[0], p[1])).collect();
                edges.push(DxfEdge::polyline(pts, true, &layer, ifc_class, material, true));
            }
        }

        ProfileData::CompositeCurve { segments } => {
            for seg in segments {
                match seg {
                    CurveSegment::Line { start, end } => {
                        edges.push(DxfEdge::line(
                            pt(start[0], start[1]),
                            pt(end[0],   end[1]),
                            &layer, ifc_class, material, true,
                        ));
                    }
                    CurveSegment::Arc { center, radius, start_angle, end_angle } => {
                        let c2 = pt(center[0], center[1]);
                        let r2 = proj.scale_len(*radius);
                        edges.push(DxfEdge::arc(
                            c2, r2, *start_angle, *end_angle,
                            &layer, ifc_class, material, true,
                        ));
                    }
                }
            }
        }

        ProfileData::Rectangle { x_dim, y_dim, px, py, angle } => {
            let hx = x_dim / 2.0;
            let hy = y_dim / 2.0;
            let corners_local = [
                (-hx, -hy), ( hx, -hy), ( hx,  hy), (-hx,  hy),
            ];
            let pts: Vec<DVec2> = corners_local.iter().map(|(x, y)| {
                let (rx, ry) = rotate2d(*x, *y, *angle, *px, *py);
                pt(rx, ry)
            }).collect();
            edges.push(DxfEdge::polyline(pts, true, &layer, ifc_class, material, true));
        }

        ProfileData::Circle { radius, px, py } => {
            let center = pt(*px, *py);
            let r = proj.scale_len(*radius);
            edges.push(DxfEdge::circle(center, r, &layer, ifc_class, material, true));
        }

        ProfileData::CircleHollow { radius, wall_thickness, px, py } => {
            let center = pt(*px, *py);
            let r_out = proj.scale_len(*radius);
            let r_in  = proj.scale_len(radius - wall_thickness);
            edges.push(DxfEdge::circle(center, r_out, &layer, ifc_class, material, true));
            if r_in > 0.0 {
                edges.push(DxfEdge::circle(center, r_in, &layer, ifc_class, material, true));
            }
        }

        ProfileData::Ellipse { semi1, semi2, px, py, angle } => {
            use super::EdgeKind;
            let center = pt(*px, *py);
            let a = proj.scale_len(*semi1);
            let b = proj.scale_len(*semi2);
            let major_axis = DVec2::new(a * angle.cos(), a * angle.sin());
            edges.push(DxfEdge {
                kind: EdgeKind::Ellipse {
                    center,
                    major_axis,
                    ratio: if a > 0.0 { b / a } else { 1.0 },
                    start_param: 0.0,
                    end_param: 2.0 * PI,
                },
                layer: layer.clone(),
                is_cut: true,
                ifc_class: ifc_class.to_string(),
                material: material.to_string(),
            });
        }

        ProfileData::Parametric { points } => {
            let pts: Vec<DVec2> = points.iter().map(|p| pt(p[0], p[1])).collect();
            edges.push(DxfEdge::polyline(pts, true, &layer, ifc_class, material, true));
        }
    }

    edges
}

// ---------------------------------------------------------------------------
// Helper: 2D rotation + translation
// ---------------------------------------------------------------------------

#[inline]
fn rotate2d(x: f64, y: f64, angle: f64, ox: f64, oy: f64) -> (f64, f64) {
    let (s, c) = angle.sin_cos();
    (ox + c * x - s * y, oy + s * x + c * y)
}

// ---------------------------------------------------------------------------
// Extrusion direction check (Bucket A condition)
// ---------------------------------------------------------------------------

/// Returns true if the extrusion vector is sufficiently parallel to the
/// camera view direction (dot product > threshold).
/// Threshold = cos(15°) ≈ 0.966.
pub fn extrusion_parallel_to_view(
    extrusion_dir: [f64; 3],
    camera_dir: [f64; 3],
) -> bool {
    let e = DVec3::from(extrusion_dir).normalize();
    let c = DVec3::from(camera_dir).normalize();
    e.dot(c).abs() > 0.966
}
