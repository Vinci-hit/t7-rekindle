//! Walking-only navigation. Geometry is supplied by the operator; no client
//! resources are bundled. Disconnected surfaces never gain synthetic links.
use serde::{Deserialize, Serialize};
use std::{
    cmp::Reverse,
    collections::{BTreeMap, BinaryHeap},
};

const CELL: f32 = 8.;
const MAX_FACES: usize = 16_384;
const HEIGHT_TOLERANCE: f32 = 1.5;
/// How far off the mesh (horizontally) and how far above or below it a target may be and still
/// be chased to the nearest walkable point.
const SNAP_RADIUS: f32 = 3.;
const SNAP_HEIGHT: f32 = 6.;
const SNAP_INSET: f32 = 0.05;

#[derive(Clone, Debug, Deserialize, Serialize, PartialEq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct Navigation {
    pub polygons: Vec<Vec<[f32; 3]>>,
    pub links: Vec<Vec<usize>>,
    #[serde(skip)]
    cells: BTreeMap<(i32, i32), Vec<usize>>,
    #[serde(skip)]
    centers: Vec<[f32; 3]>,
    #[serde(skip)]
    components: Vec<usize>,
}
impl Navigation {
    pub(crate) fn prepare(&mut self) -> Result<(), &'static str> {
        if self.polygons.is_empty()
            || self.polygons.len() > MAX_FACES
            || self.links.len() != self.polygons.len()
        {
            return Err("invalid PVE navigation size");
        }
        self.cells.clear();
        self.centers.clear();
        for (id, polygon) in self.polygons.iter().enumerate() {
            if !(3..=32).contains(&polygon.len())
                || polygon
                    .iter()
                    .flatten()
                    .any(|v| !v.is_finite() || v.abs() >= 100_000.)
                || self.links[id].len() > 32
            {
                return Err("invalid PVE navigation face");
            }
            let area: f32 = polygon[1..]
                .windows(2)
                .map(|pair| cross(polygon[0], pair[0], pair[1]))
                .sum();
            if area <= 0.00001
                || (0..polygon.len()).any(|i| {
                    cross(
                        polygon[i],
                        polygon[(i + 1) % polygon.len()],
                        polygon[(i + 2) % polygon.len()],
                    ) < -0.0001
                })
            {
                return Err("PVE navigation must contain convex counterclockwise faces");
            }
            for &neighbor in &self.links[id] {
                if neighbor >= self.polygons.len()
                    || neighbor == id
                    || !self.links[neighbor].contains(&id)
                    || portal(polygon, &self.polygons[neighbor]).is_none()
                {
                    return Err("invalid PVE navigation adjacency");
                }
            }
            self.centers.push(std::array::from_fn(|d| {
                polygon.iter().map(|p| p[d]).sum::<f32>() / polygon.len() as f32
            }));
            let bounds: [(i32, i32); 2] = std::array::from_fn(|d| {
                (
                    (polygon.iter().map(|p| p[d]).fold(f32::INFINITY, f32::min) / CELL).floor()
                        as i32,
                    (polygon
                        .iter()
                        .map(|p| p[d])
                        .fold(f32::NEG_INFINITY, f32::max)
                        / CELL)
                        .floor() as i32,
                )
            });
            if (i64::from(bounds[0].1 - bounds[0].0) + 1)
                * (i64::from(bounds[1].1 - bounds[1].0) + 1)
                > 256
            {
                return Err("PVE navigation face extent exceeds limit");
            }
            for x in bounds[0].0..=bounds[0].1 {
                for y in bounds[1].0..=bounds[1].1 {
                    self.cells.entry((x, y)).or_default().push(id);
                }
            }
        }
        self.components = vec![usize::MAX; self.polygons.len()];
        for first in 0..self.polygons.len() {
            if self.components[first] != usize::MAX {
                continue;
            }
            let mut pending = vec![first];
            self.components[first] = first;
            while let Some(id) = pending.pop() {
                for &next in &self.links[id] {
                    if self.components[next] == usize::MAX {
                        self.components[next] = first;
                        pending.push(next);
                    }
                }
            }
        }
        Ok(())
    }
    pub(crate) fn connected(&self, from: [f32; 3], to: [f32; 3]) -> bool {
        match (self.locate(from), self.locate(to)) {
            (Some((a, _)), Some((b, _))) => self.components[a] == self.components[b],
            _ => false,
        }
    }
    fn locate(&self, point: [f32; 3]) -> Option<(usize, f32)> {
        self.cells
            .get(&(
                (point[0] / CELL).floor() as i32,
                (point[1] / CELL).floor() as i32,
            ))?
            .iter()
            .filter_map(|&id| {
                let p = &self.polygons[id];
                if p.iter()
                    .zip(p.iter().cycle().skip(1))
                    .any(|(&a, &b)| cross(a, b, point) < -0.0001)
                {
                    return None;
                }
                let z = height(p, point)?;
                ((z - point[2]).abs() <= HEIGHT_TOLERANCE).then_some((id, z))
            })
            .min_by(|a, b| {
                (a.1 - point[2])
                    .abs()
                    .total_cmp(&(b.1 - point[2]).abs())
                    .then(a.0.cmp(&b.0))
            })
    }
    /// The walkable point a monster should path to for a target that may be off the mesh:
    /// jumping or standing on something too high, or pressed against a wall. `None` only when the
    /// target is genuinely far from every surface.
    pub(crate) fn anchor(&self, point: [f32; 3]) -> Option<[f32; 3]> {
        if let Some((_, z)) = self.locate(point) {
            return Some([point[0], point[1], z]);
        }
        let key = |v: f32| (v / CELL).floor() as i32;
        let (cx, cy) = (key(point[0]), key(point[1]));
        let mut best: Option<(f32, [f32; 3])> = None;
        for x in cx - 1..=cx + 1 {
            for y in cy - 1..=cy + 1 {
                for &id in self.cells.get(&(x, y)).into_iter().flatten() {
                    let polygon = &self.polygons[id];
                    // Directly above or below a face: its surface point, at any height within range.
                    let contained = !polygon
                        .iter()
                        .zip(polygon.iter().cycle().skip(1))
                        .any(|(&a, &b)| cross(a, b, point) < -0.0001);
                    if contained {
                        if let Some(z) = height(polygon, point) {
                            let gap = (z - point[2]).abs();
                            if gap <= SNAP_HEIGHT && best.is_none_or(|(d, _)| gap * 0.001 < d) {
                                best = Some((gap * 0.001, [point[0], point[1], z]));
                            }
                        }
                        continue;
                    }
                    // Beside a face: the nearest boundary point, nudged inside so it locates.
                    for (&a, &b) in polygon.iter().zip(polygon.iter().cycle().skip(1)) {
                        let edge = [b[0] - a[0], b[1] - a[1]];
                        let length_sq = edge[0] * edge[0] + edge[1] * edge[1];
                        let t = (((point[0] - a[0]) * edge[0] + (point[1] - a[1]) * edge[1])
                            / length_sq.max(1e-9))
                        .clamp(0., 1.);
                        let on_edge: [f32; 3] = std::array::from_fn(|d| a[d] + (b[d] - a[d]) * t);
                        let d = (on_edge[0] - point[0]).hypot(on_edge[1] - point[1]);
                        if d > SNAP_RADIUS
                            || (on_edge[2] - point[2]).abs() > SNAP_HEIGHT
                            || best.is_some_and(|(best_d, _)| d + 0.01 >= best_d)
                        {
                            continue;
                        }
                        let center = self.centers[id];
                        let inward = [center[0] - on_edge[0], center[1] - on_edge[1]];
                        let inward_len = inward[0].hypot(inward[1]);
                        let mut inside = on_edge;
                        if inward_len > SNAP_INSET {
                            inside[0] += inward[0] / inward_len * SNAP_INSET;
                            inside[1] += inward[1] / inward_len * SNAP_INSET;
                        }
                        inside[2] = height(polygon, inside).unwrap_or(on_edge[2]);
                        best = Some((d + 0.01, inside));
                    }
                }
            }
        }
        best.map(|(_, anchor)| anchor)
    }
    pub(crate) fn project(&self, point: [f32; 3]) -> Option<[f32; 3]> {
        self.locate(point).map(|(_, z)| [point[0], point[1], z])
    }
    pub(crate) fn route(&self, from: [f32; 3], to: [f32; 3]) -> Vec<[f32; 3]> {
        let (Some((first, _)), Some((last, z))) = (self.locate(from), self.locate(to)) else {
            return Vec::new();
        };
        if self.components[first] != self.components[last] {
            return Vec::new();
        }
        if first == last {
            return vec![[to[0], to[1], z]];
        }
        let mut costs = vec![u64::MAX; self.polygons.len()];
        let mut previous = vec![None; self.polygons.len()];
        let mut queue = BinaryHeap::new();
        costs[first] = 0;
        queue.push(Reverse((0u64, first)));
        while let Some(Reverse((cost, id))) = queue.pop() {
            if cost != costs[id] {
                continue;
            }
            if id == last {
                break;
            }
            for &next in &self.links[id] {
                let a = self.centers[id];
                let b = self.centers[next];
                let edge = ((a[0] - b[0]).hypot(a[1] - b[1]) * 1000.).ceil() as u64 + 1;
                let candidate = cost + edge;
                if candidate < costs[next] {
                    costs[next] = candidate;
                    previous[next] = Some(id);
                    queue.push(Reverse((candidate, next)));
                }
            }
        }
        if previous[last].is_none() {
            return Vec::new();
        }
        let mut chain = vec![last];
        while *chain.last().unwrap() != first {
            chain.push(previous[*chain.last().unwrap()].unwrap());
        }
        chain.reverse();
        let mut route = Vec::new();
        for pair in chain.windows(2) {
            route.push(
                portal(&self.polygons[pair[0]], &self.polygons[pair[1]]).expect("validated portal"),
            );
        }
        route.push([to[0], to[1], z]);
        route
    }
}
fn cross(a: [f32; 3], b: [f32; 3], p: [f32; 3]) -> f32 {
    (b[0] - a[0]) * (p[1] - a[1]) - (b[1] - a[1]) * (p[0] - a[0])
}
fn height(p: &[[f32; 3]], at: [f32; 3]) -> Option<f32> {
    let a = p[0];
    for pair in p[1..].windows(2) {
        let b = pair[0];
        let c = pair[1];
        let area = cross(a, b, c);
        if area.abs() < 0.00001 {
            continue;
        }
        let u = cross(a, at, c) / area;
        let v = cross(a, b, at) / area;
        if u >= -0.001 && v >= -0.001 && u + v <= 1.001 {
            return Some(a[2] + u * (b[2] - a[2]) + v * (c[2] - a[2]));
        }
    }
    None
}
fn portal(a: &[[f32; 3]], b: &[[f32; 3]]) -> Option<[f32; 3]> {
    for (&start, &end) in a.iter().zip(a.iter().cycle().skip(1)) {
        for (&other_start, &other_end) in b.iter().zip(b.iter().cycle().skip(1)) {
            let near = |p: [f32; 3], q: [f32; 3]| {
                (p[0] - q[0]).hypot(p[1] - q[1]) <= 0.011 && (p[2] - q[2]).abs() <= 0.4
            };
            if near(start, other_end) && near(end, other_start) {
                return Some(std::array::from_fn(|d| {
                    (start[d] + end[d] + other_start[d] + other_end[d]) / 4.
                }));
            }
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;
    fn mesh() -> Navigation {
        let mut nav: Navigation = serde_json::from_value(serde_json::json!({"polygons":[
            [[0,0,0],[2,0,0],[2,2,1],[0,2,1]],
            [[2,0,0],[4,0,0],[4,2,1],[2,2,1]],
            [[8,0,0],[10,0,0],[10,2,0],[8,2,0]]],"links":[[1],[0],[]]}))
        .unwrap();
        nav.prepare().unwrap();
        nav
    }
    #[test]
    fn slope_and_disconnected_routes() {
        let nav = mesh();
        assert_eq!(nav.project([1., 1., 0.]), Some([1., 1., 0.5]));
        assert_eq!(
            nav.route([1., 1., 0.5], [3., 1., 0.5]),
            vec![[2., 1., 0.5], [3., 1., 0.5]]
        );
        assert!(nav.route([1., 1., 0.5], [9., 1., 0.]).is_empty());
        assert!(nav.project([1., 1., 5.]).is_none());
    }
    #[test]
    fn off_mesh_targets_anchor_to_the_nearest_walkable_point() {
        let nav = mesh();
        // On the surface: unchanged.
        assert_eq!(nav.anchor([1., 1., 0.5]), Some([1., 1., 0.5]));
        // Jumping above the mesh: the surface below.
        assert_eq!(nav.anchor([1., 1., 4.]), Some([1., 1., 0.5]));
        // Beside the mesh (against a wall): a point inside, on the same component.
        let beside = nav.anchor([-0.4, 1., 0.5]).unwrap();
        assert!(beside[0] > 0. && beside[0] < 0.2, "{beside:?}");
        assert!(nav.connected([1., 1., 0.5], beside));
        let route = nav.route([3., 1., 0.5], beside);
        assert!(!route.is_empty());
        // Far from every surface, or hopelessly high: no anchor.
        assert!(nav.anchor([50., 50., 0.]).is_none());
        assert!(nav.anchor([1., 1., 40.]).is_none());
    }

    #[test]
    fn invalid_geometry_and_links_are_rejected() {
        let mut nav = mesh();
        nav.links[0] = vec![2];
        assert!(nav.prepare().is_err());
        let mut nav = mesh();
        nav.polygons[0].reverse();
        assert!(nav.prepare().is_err());
    }
}
