use glam::DVec2;
use super::{DxfEdge, EdgeKind};

const TOL: f64 = 1e-6; // metres (1 μm — negligible in architectural scale)
const COLLINEAR_DEG: f64 = 0.5;

/// Run all cleanup passes on a per-element edge list.
pub fn cleanup(edges: Vec<DxfEdge>) -> Vec<DxfEdge> {
    let edges = remove_degenerate(edges);
    let edges = remove_duplicates(edges);
    let edges = merge_collinear(edges);
    close_gaps(edges)
}

// ---------------------------------------------------------------------------
// Pass 1: remove degenerate edges
// ---------------------------------------------------------------------------

fn remove_degenerate(edges: Vec<DxfEdge>) -> Vec<DxfEdge> {
    edges.into_iter().filter(|e| !is_degenerate(e)).collect()
}

fn is_degenerate(e: &DxfEdge) -> bool {
    match &e.kind {
        EdgeKind::Line { start, end } => start.distance(*end) < TOL,
        EdgeKind::Circle { radius, .. } => *radius < TOL,
        EdgeKind::Arc { radius, .. } => *radius < TOL,
        EdgeKind::Polyline { points, .. } => points.len() < 2,
        EdgeKind::Spline { points } => points.len() < 2,
        EdgeKind::Ellipse { ratio, .. } => *ratio < TOL,
    }
}

// ---------------------------------------------------------------------------
// Pass 2: remove duplicates
// ---------------------------------------------------------------------------

fn remove_duplicates(edges: Vec<DxfEdge>) -> Vec<DxfEdge> {
    let mut seen: Vec<EdgeSig> = Vec::with_capacity(edges.len());
    let mut out   = Vec::with_capacity(edges.len());
    for e in edges {
        let sig = EdgeSig::from(&e);
        if !seen.iter().any(|s| s.approx_eq(&sig)) {
            seen.push(sig);
            out.push(e);
        }
    }
    out
}

#[derive(Debug)]
enum EdgeSig {
    Line(OrderedPair),
    Circle([i64; 3]),      // cx, cy, r — rounded to 3 decimals × 1000
    Arc([i64; 5]),         // cx, cy, r, a1, a2
    Other,
}

#[derive(Debug)]
struct OrderedPair(DVec2, DVec2);  // smaller point first

impl From<&DxfEdge> for EdgeSig {
    fn from(e: &DxfEdge) -> Self {
        match &e.kind {
            EdgeKind::Line { start, end } => {
                let a = r3(*start);
                let b = r3(*end);
                EdgeSig::Line(if a[0] < b[0] || (a[0] == b[0] && a[1] <= b[1]) {
                    OrderedPair(DVec2::new(a[0] as f64, a[1] as f64),
                                DVec2::new(b[0] as f64, b[1] as f64))
                } else {
                    OrderedPair(DVec2::new(b[0] as f64, b[1] as f64),
                                DVec2::new(a[0] as f64, a[1] as f64))
                })
            }
            EdgeKind::Circle { center, radius } => {
                let c = r3(*center);
                EdgeSig::Circle([c[0], c[1], (radius * 1000.0).round() as i64])
            }
            EdgeKind::Arc { center, radius, start_angle, end_angle } => {
                let c = r3(*center);
                EdgeSig::Arc([
                    c[0], c[1],
                    (radius * 1000.0).round() as i64,
                    (start_angle * 10.0).round() as i64,
                    (end_angle   * 10.0).round() as i64,
                ])
            }
            _ => EdgeSig::Other,
        }
    }
}

impl EdgeSig {
    fn approx_eq(&self, other: &Self) -> bool {
        match (self, other) {
            (EdgeSig::Line(a), EdgeSig::Line(b)) => {
                (a.0.distance(b.0) < TOL && a.1.distance(b.1) < TOL) ||
                (a.0.distance(b.1) < TOL && a.1.distance(b.0) < TOL)
            }
            (EdgeSig::Circle(a), EdgeSig::Circle(b)) => a == b,
            (EdgeSig::Arc(a),    EdgeSig::Arc(b))    => a == b,
            _ => false,
        }
    }
}

fn r3(v: DVec2) -> [i64; 2] {
    [(v.x * 1000.0).round() as i64,
     (v.y * 1000.0).round() as i64]
}

// ---------------------------------------------------------------------------
// Pass 3: merge collinear consecutive lines
// ---------------------------------------------------------------------------

fn merge_collinear(edges: Vec<DxfEdge>) -> Vec<DxfEdge> {
    let (mut lines, others): (Vec<_>, Vec<_>) = edges
        .into_iter()
        .partition(|e| matches!(e.kind, EdgeKind::Line { .. }));

    let mut changed = true;
    while changed {
        changed = false;
        let mut merged: Vec<DxfEdge> = Vec::with_capacity(lines.len());
        let mut used = vec![false; lines.len()];

        for i in 0..lines.len() {
            if used[i] { continue; }
            let mut current = lines[i].clone();
            for j in (i + 1)..lines.len() {
                if used[j] { continue; }
                if let Some(fused) = try_merge(&current, &lines[j]) {
                    current = fused;
                    used[j] = true;
                    changed = true;
                }
            }
            merged.push(current);
        }
        lines = merged;
    }
    others.into_iter().chain(lines).collect()
}

fn try_merge(a: &DxfEdge, b: &DxfEdge) -> Option<DxfEdge> {
    let (as_, ae) = match &a.kind {
        EdgeKind::Line { start, end } => (*start, *end),
        _ => return None,
    };
    let (bs, be) = match &b.kind {
        EdgeKind::Line { start, end } => (*start, *end),
        _ => return None,
    };

    if !collinear(as_, ae, bs, be) {
        return None;
    }

    let joined = if as_.distance(bs) < TOL { Some((ae, be)) }
    else if as_.distance(be) < TOL         { Some((ae, bs)) }
    else if ae.distance(bs) < TOL          { Some((as_, be)) }
    else if ae.distance(be) < TOL          { Some((as_, bs)) }
    else { None }?;

    let mut result = a.clone();
    result.kind = EdgeKind::Line { start: joined.0, end: joined.1 };
    Some(result)
}

fn collinear(p1: DVec2, p2: DVec2, p3: DVec2, p4: DVec2) -> bool {
    let a1 = (p2 - p1).to_angle().to_degrees();
    let a2 = (p4 - p3).to_angle().to_degrees();
    let diff = (a1 - a2).abs() % 180.0;
    diff < COLLINEAR_DEG || diff > 180.0 - COLLINEAR_DEG
}

// ---------------------------------------------------------------------------
// Pass 4: close near-closed polylines
// ---------------------------------------------------------------------------

fn close_gaps(edges: Vec<DxfEdge>) -> Vec<DxfEdge> {
    edges.into_iter().map(|mut e| {
        if let EdgeKind::Polyline { ref mut points, ref mut closed } = e.kind {
            if !*closed && points.len() >= 3 {
                let first = points[0];
                let last  = *points.last().unwrap();
                if first.distance(last) < TOL {
                    points.pop();
                    *closed = true;
                }
            }
        }
        e
    }).collect()
}
