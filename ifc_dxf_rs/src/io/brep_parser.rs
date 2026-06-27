/// Parse edges from CASCADE Topology V2 BRep text.
/// Extracts LINE, CIRCLE, ARC, ELLIPSE curves without OCC.
/// Used for Bucket B (2D native) and as fallback for Bucket D.

use std::f64::consts::PI;
use glam::{DVec2, DVec3, DMat4};
use crate::geometry::{DxfEdge, CameraProjection, EdgeKind, layer_name};

// OCC BRep curve type codes
const BREP_LINE:    u8 = 1;
const BREP_CIRCLE:  u8 = 2;
const BREP_ELLIPSE: u8 = 3;
const BREP_BSPLINE: u8 = 11;

#[derive(Debug, Clone)]
enum Curve {
    Line   { origin: DVec3, dir: DVec3 },
    Circle { center: DVec3, normal: DVec3, x_axis: DVec3, radius: f64 },
    Ellipse{ center: DVec3, normal: DVec3, x_axis: DVec3, r_major: f64, r_minor: f64 },
    BSpline,
}

/// Parse a BRep string and convert its curves to DxfEdge.
pub fn parse_edges(
    brep_text: &str,
    world_m4: &DMat4,
    proj: &CameraProjection,
    ifc_class: &str,
    material: &str,
    is_cut: bool,
) -> Vec<DxfEdge> {
    let layer = layer_name(ifc_class, is_cut);
    let curves = parse_curves(brep_text);
    let params  = parse_edge_params(brep_text);
    let mut out  = Vec::new();

    for (i, curve) in curves.iter().enumerate() {
        let (p1, p2) = params.get(i).copied().unwrap_or((0.0, 1.0));
        if let Some(edge) = curve_to_dxf(curve, p1, p2, world_m4, proj,
                                          &layer, ifc_class, material, is_cut) {
            out.push(edge);
        }
    }
    out
}

// ---------------------------------------------------------------------------
// Curve parser
// ---------------------------------------------------------------------------

fn parse_curves(brep: &str) -> Vec<Curve> {
    let mut curves = Vec::new();
    let body = match find_section(brep, "Curves") {
        Some(b) => b,
        None    => return curves,
    };

    let tokens: Vec<&str> = body.split_whitespace().collect();
    let n: usize = tokens.first()
        .and_then(|t| t.parse().ok())
        .unwrap_or(0);

    let mut i = 1usize;
    for _ in 0..n {
        if i >= tokens.len() { break; }
        let t: u8 = tokens[i].parse().unwrap_or(0);
        i += 1;
        match t {
            BREP_LINE => {
                if i + 5 >= tokens.len() { break; }
                let origin = dvec3(&tokens, i);     i += 3;
                let dir    = dvec3(&tokens, i);     i += 3;
                curves.push(Curve::Line { origin, dir });
            }
            BREP_CIRCLE => {
                if i + 9 >= tokens.len() { break; }
                let center = dvec3(&tokens, i);     i += 3;
                let normal = dvec3(&tokens, i);     i += 3;
                let x_axis = dvec3(&tokens, i);     i += 3;
                let radius: f64 = tokens[i].parse().unwrap_or(0.0); i += 1;
                curves.push(Curve::Circle { center, normal, x_axis, radius: radius.abs() });
            }
            BREP_ELLIPSE => {
                if i + 10 >= tokens.len() { break; }
                let center  = dvec3(&tokens, i);    i += 3;
                let normal  = dvec3(&tokens, i);    i += 3;
                let x_axis  = dvec3(&tokens, i);    i += 3;
                let r_major: f64 = tokens[i].parse().unwrap_or(0.0); i += 1;
                let r_minor: f64 = tokens[i].parse().unwrap_or(0.0); i += 1;
                curves.push(Curve::Ellipse {
                    center, normal, x_axis,
                    r_major: r_major.abs(), r_minor: r_minor.abs(),
                });
            }
            BREP_BSPLINE => {
                curves.push(Curve::BSpline);
                break; // variable-length — stop parsing
            }
            _ => break,
        }
    }
    curves
}

fn find_section<'a>(brep: &'a str, name: &str) -> Option<&'a str> {
    let marker = format!("{}\n", name);
    let start  = brep.find(marker.as_str())?;
    let start  = start + marker.len();
    let rest   = &brep[start..];
    Some(rest)
}

fn dvec3(tokens: &[&str], i: usize) -> DVec3 {
    DVec3::new(
        tokens[i    ].parse().unwrap_or(0.0),
        tokens[i + 1].parse().unwrap_or(0.0),
        tokens[i + 2].parse().unwrap_or(0.0),
    )
}

// ---------------------------------------------------------------------------
// Edge parameter parser (p_start, p_end per edge)
// ---------------------------------------------------------------------------

fn parse_edge_params(brep: &str) -> Vec<(f64, f64)> {
    let mut params = Vec::new();
    for line in brep.lines() {
        let trimmed = line.trim();
        if !trimmed.starts_with("Ed ") { continue; }
        let floats: Vec<f64> = trimmed.split_whitespace()
            .skip(1)
            .filter_map(|t| t.parse::<f64>().ok())
            .collect();
        if floats.len() >= 2 {
            params.push((floats[0], floats[1]));
        }
    }
    params
}

// ---------------------------------------------------------------------------
// Curve → DxfEdge
// ---------------------------------------------------------------------------

fn curve_to_dxf(
    curve: &Curve,
    p1: f64, p2: f64,
    world_m4: &DMat4,
    proj: &CameraProjection,
    layer: &str,
    ifc_class: &str,
    material: &str,
    is_cut: bool,
) -> Option<DxfEdge> {
    match curve {
        Curve::Line { origin, dir } => {
            let w_origin = world_m4.transform_point3(*origin);
            let w_dir    = world_m4.transform_vector3(*dir);
            let s = proj.project(w_origin + w_dir * p1);
            let e = proj.project(w_origin + w_dir * p2);
            Some(DxfEdge::line(s, e, layer, ifc_class, material, is_cut))
        }

        Curve::Circle { center, normal: _, x_axis, radius } => {
            let w_center = world_m4.transform_point3(*center);
            let w_xaxis  = world_m4.transform_vector3(*x_axis);
            let c2 = proj.project(w_center);
            let r2 = proj.scale_len(*radius);
            let angle_offset = w_xaxis.y.atan2(w_xaxis.x);
            let full = (p2 - p1 - 2.0 * PI).abs() < 1e-4;
            if full {
                Some(DxfEdge::circle(c2, r2, layer, ifc_class, material, is_cut))
            } else {
                let a1 = (p1 + angle_offset).to_degrees();
                let a2 = (p2 + angle_offset).to_degrees();
                Some(DxfEdge::arc(c2, r2, a1, a2, layer, ifc_class, material, is_cut))
            }
        }

        Curve::Ellipse { center, normal: _, x_axis, r_major, r_minor } => {
            let w_center = world_m4.transform_point3(*center);
            let w_xaxis  = world_m4.transform_vector3(*x_axis);
            let c2   = proj.project(w_center);
            let a    = proj.scale_len(*r_major);
            let b    = proj.scale_len(*r_minor);
            let angle = w_xaxis.y.atan2(w_xaxis.x);
            let major_axis = DVec2::new(a * angle.cos(), a * angle.sin());
            Some(DxfEdge {
                kind: EdgeKind::Ellipse {
                    center: c2,
                    major_axis,
                    ratio: if a > 0.0 { b / a } else { 1.0 },
                    start_param: p1,
                    end_param: p2,
                },
                layer: layer.to_string(),
                is_cut,
                ifc_class: ifc_class.to_string(),
                material: material.to_string(),
            })
        }

        Curve::BSpline => None, // caller uses HLR or wireframe
    }
}
