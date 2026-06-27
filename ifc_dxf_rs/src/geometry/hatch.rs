use glam::DVec2;
use geo::{BooleanOps, MultiPolygon, Polygon, LineString, Coord};
use std::collections::HashMap;

use super::{DxfEdge, EdgeKind, hatch_layer_name};

/// A hatch region ready for DXF output.
#[derive(Debug, Clone)]
pub struct HatchRegion {
    pub layer: String,
    pub pattern: HatchPattern,
    pub exterior: Vec<DVec2>,
    pub interiors: Vec<Vec<DVec2>>,
}

#[derive(Debug, Clone)]
pub struct HatchPattern {
    pub name: String,   // e.g. "ANSI31"
    pub scale: f64,
    pub angle: f64,     // degrees
}

impl HatchPattern {
    pub fn for_material(material: &str) -> Self {
        let name_lower = material.to_lowercase();
        let (name, scale) = if name_lower.contains("concrete")
                           || name_lower.contains("calcestruzzo")
                           || name_lower.contains("cls") {
            ("AR-CONC", 0.5)
        } else if name_lower.contains("brick")
               || name_lower.contains("mattone")
               || name_lower.contains("muratura") {
            ("ANSI31", 1.0)
        } else if name_lower.contains("wood")
               || name_lower.contains("legno") {
            ("ANSI37", 1.0)
        } else if name_lower.contains("steel")
               || name_lower.contains("acciaio") {
            ("ANSI32", 1.0)
        } else if name_lower.contains("insul")
               || name_lower.contains("lana") {
            ("ANSI33", 1.0)
        } else if name_lower.contains("glass")
               || name_lower.contains("vetro") {
            ("ANSI36", 1.0)
        } else {
            ("ANSI31", 1.0)
        };
        HatchPattern { name: name.to_string(), scale, angle: 0.0 }
    }
}

/// Collect closed cut contours from edges, group by material, union with geo,
/// return HatchRegion list ready for DXF output.
pub fn build_hatch_regions(edges: &[DxfEdge]) -> Vec<HatchRegion> {
    let mut by_material: HashMap<String, Vec<Vec<DVec2>>> = HashMap::new();
    for e in edges {
        if !e.is_cut { continue; }
        if let EdgeKind::Polyline { points, closed: true } = &e.kind {
            if points.len() >= 3 {
                by_material
                    .entry(e.material.clone())
                    .or_default()
                    .push(points.clone());
            }
        }
    }

    let mut regions = Vec::new();
    for (material, contours) in &by_material {
        let layer   = hatch_layer_name(material);
        let pattern = HatchPattern::for_material(material);
        let unified = union_contours(contours);
        for poly in unified {
            regions.push(HatchRegion {
                layer: layer.clone(),
                pattern: pattern.clone(),
                exterior:  poly.exterior,
                interiors: poly.interiors,
            });
        }
    }
    regions
}

// ---------------------------------------------------------------------------
// Polygon union via `geo` crate
// ---------------------------------------------------------------------------

struct SimplePolygon {
    exterior:  Vec<DVec2>,
    interiors: Vec<Vec<DVec2>>,
}

fn union_contours(contours: &[Vec<DVec2>]) -> Vec<SimplePolygon> {
    let polys: Vec<Polygon<f64>> = contours
        .iter()
        .filter_map(|pts| dvec2_slice_to_polygon(pts))
        .collect();

    union_geo_polygons(&polys)
        .0
        .into_iter()
        .map(|poly| {
            let exterior = geo_ring_to_dvec2(poly.exterior());
            let interiors = poly.interiors().iter().map(|r| geo_ring_to_dvec2(r)).collect();
            SimplePolygon { exterior, interiors }
        })
        .collect()
}

/// Progressive union of a slice of geo Polygons. Returns the merged MultiPolygon.
///
/// The `geo` sweep-line can panic on near-degenerate geometry.
/// Each step is guarded with `catch_unwind`; if a particular polygon causes a
/// panic it is appended as-is (no merge) so the export continues.
pub fn union_geo_polygons(polys: &[Polygon<f64>]) -> MultiPolygon<f64> {
    if polys.is_empty() {
        return MultiPolygon::new(vec![]);
    }
    let mut result = MultiPolygon::new(vec![polys[0].clone()]);
    for poly in &polys[1..] {
        let next = MultiPolygon::new(vec![poly.clone()]);
        match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| result.union(&next))) {
            Ok(merged) => result = merged,
            Err(_) => {
                // Sweep-line failed for this polygon; keep it unmerged.
                let mut inner = result.0.clone();
                inner.push(poly.clone());
                result = MultiPolygon::new(inner);
            }
        }
    }
    result
}

/// Convert a flat DVec2 slice to a geo Polygon (no holes). Returns None if < 3 points.
///
/// Automatically removes:
/// - The closing duplicate (last ≈ first), which IfcPolyline adds explicitly.
/// - Consecutive identical vertices (zero-length edges), which cause sweep-line panics.
pub fn dvec2_slice_to_polygon(pts: &[DVec2]) -> Option<Polygon<f64>> {
    // Deduplicate: keep each vertex that is not equal to the previous one.
    // Also strip explicit closing duplicate (IFC IfcPolyline convention).
    let mut unique: Vec<DVec2> = Vec::with_capacity(pts.len());
    for p in pts {
        if let Some(last) = unique.last() {
            if (*p - *last).length_squared() < 1e-18 { continue; }
        }
        unique.push(*p);
    }
    // Remove closing duplicate if present
    if unique.len() >= 2 {
        let first = unique[0];
        let last  = *unique.last().unwrap();
        if (last - first).length_squared() < 1e-18 {
            unique.pop();
        }
    }
    if unique.len() < 3 { return None; }
    let coords: Vec<Coord<f64>> = unique.iter().map(|p| Coord { x: p.x, y: p.y }).collect();
    Some(Polygon::new(LineString::from(coords), vec![]))
}

pub fn geo_ring_to_dvec2(ring: &geo::LineString<f64>) -> Vec<DVec2> {
    ring.coords().map(|c| DVec2::new(c.x, c.y)).collect()
}
