/**
 * Pure geometry, layout and camera maths for the person-relationship graph.
 *
 * Why a separate module: the panic bugs in this page ("cannot pan", "zoom enlarges the
 * labels until they cover everything") were rendering/interaction defects that are very
 * hard to assert through the DOM. Everything numeric lives here so it can be unit
 * tested in Node, and the canvas component only draws the result.
 *
 * Three layers are kept strictly apart (the core requirement):
 *   1. world coordinates  — the layout result, never touched by zoom/pan;
 *   2. camera             — { x, y, k }: translate + scale applied to the world;
 *   3. visual size        — node radius and font size, computed in *screen* space and
 *                           only sub-linearly dependent on k.
 */

export interface LayoutNode {
  id: number;
  x: number;
  y: number;
  vx?: number;
  vy?: number;
  degree?: number;
}

export interface LayoutLink {
  source: number;
  target: number;
}

export interface Rect {
  minX: number;
  minY: number;
  maxX: number;
  maxY: number;
}

export interface Viewport {
  width: number;
  height: number;
}

export interface Camera {
  x: number;
  y: number;
  k: number;
}

export const CAMERA_MIN_K = 0.15;
export const CAMERA_MAX_K = 6;
export const FIT_PADDING = 48;
/**
 * Upper bound for the *automatic* fit. A tiny graph would otherwise be scaled to the
 * camera limit (a 4-node graph was shown at 555 %), which makes panning feel broken.
 */
export const FIT_MAX_K = 2.2;

export function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** Node radius in *world* units, derived only from degree (bounded, no runaway hubs). */
export function baseNodeRadius(degree: number): number {
  const safe = Number.isFinite(degree) && degree > 0 ? degree : 0;
  return clamp(11 + Math.sqrt(safe) * 3.2, 11, 26);
}

/**
 * Node radius in *screen* pixels.
 *
 * Linear scaling (`r * k`) is what made the graph unreadable when zoomed in. Growth is
 * sub-linear (k^0.34) and clamped, so zooming mostly spreads nodes apart while the discs
 * stay a usable size.
 */
export function screenNodeRadius(baseRadius: number, k: number): number {
  return clamp(baseRadius * Math.pow(clamp(k, CAMERA_MIN_K, CAMERA_MAX_K), 0.34), 7, 30);
}

/** Label font size in screen pixels: nearly constant, slightly larger when zoomed in. */
export function labelFontSize(k: number): number {
  return clamp(10 * Math.pow(clamp(k, CAMERA_MIN_K, CAMERA_MAX_K), 0.18), 9, 13);
}

export function worldToScreen(
  x: number,
  y: number,
  camera: Camera,
  viewport: Viewport
): { sx: number; sy: number } {
  return {
    sx: viewport.width / 2 + camera.x + x * camera.k,
    sy: viewport.height / 2 + camera.y + y * camera.k,
  };
}

export function screenToWorld(
  sx: number,
  sy: number,
  camera: Camera,
  viewport: Viewport
): { x: number; y: number } {
  return {
    x: (sx - viewport.width / 2 - camera.x) / camera.k,
    y: (sy - viewport.height / 2 - camera.y) / camera.k,
  };
}

/**
 * Zoom around a fixed point (the cursor), so the graph does not jump to the centre.
 * `(fx, fy)` are canvas-local screen coordinates.
 */
export function zoomAtPoint(
  camera: Camera,
  factor: number,
  fx: number,
  fy: number,
  viewport: Viewport
): Camera {
  const k = clamp(camera.k * factor, CAMERA_MIN_K, CAMERA_MAX_K);
  if (k === camera.k) return camera;
  const ratio = k / camera.k;
  const cx = viewport.width / 2;
  const cy = viewport.height / 2;
  return {
    k,
    x: fx - cx - (fx - cx - camera.x) * ratio,
    y: fy - cy - (fy - cy - camera.y) * ratio,
  };
}

export function boundsOf(nodes: { x: number; y: number }[]): Rect | null {
  if (!nodes.length) return null;
  let minX = Infinity;
  let minY = Infinity;
  let maxX = -Infinity;
  let maxY = -Infinity;
  for (const node of nodes) {
    minX = Math.min(minX, node.x);
    minY = Math.min(minY, node.y);
    maxX = Math.max(maxX, node.x);
    maxY = Math.max(maxY, node.y);
  }
  return { minX, minY, maxX, maxY };
}

/**
 * Camera that fits every node into the viewport with padding.
 *
 * Returns the identity camera for an empty graph (never NaN, never a wild zoom).
 */
export function fitCamera(
  nodes: { x: number; y: number }[],
  viewport: Viewport,
  padding: number = FIT_PADDING,
  maxZoom: number = FIT_MAX_K
): Camera {
  const bounds = boundsOf(nodes);
  if (!bounds || viewport.width <= 0 || viewport.height <= 0) {
    return { x: 0, y: 0, k: 1 };
  }
  const spanX = Math.max(bounds.maxX - bounds.minX, 1);
  const spanY = Math.max(bounds.maxY - bounds.minY, 1);
  const usableW = Math.max(viewport.width - padding * 2, 40);
  const usableH = Math.max(viewport.height - padding * 2, 40);
  const k = clamp(
    Math.min(usableW / spanX, usableH / spanY),
    CAMERA_MIN_K,
    clamp(maxZoom, CAMERA_MIN_K, CAMERA_MAX_K)
  );
  const centerX = (bounds.minX + bounds.maxX) / 2;
  const centerY = (bounds.minY + bounds.maxY) / 2;
  return { x: -centerX * k, y: -centerY * k, k };
}

export function resetCamera(): Camera {
  return { x: 0, y: 0, k: 1 };
}

/** Camera that centres one node at a comfortable zoom (used by search / focus). */
export function focusCamera(node: { x: number; y: number }, k = 1.6): Camera {
  const safeK = clamp(k, CAMERA_MIN_K, CAMERA_MAX_K);
  return { x: -node.x * safeK, y: -node.y * safeK, k: safeK };
}

// --------------------------------------------------------------------- components
/** Connected components by node id (small graphs: BFS is fine and deterministic). */
export function findComponents(nodeIds: number[], links: LayoutLink[]): number[][] {
  const adjacency = new Map<number, number[]>();
  for (const id of nodeIds) adjacency.set(id, []);
  for (const link of links) {
    if (!adjacency.has(link.source) || !adjacency.has(link.target)) continue;
    adjacency.get(link.source)!.push(link.target);
    adjacency.get(link.target)!.push(link.source);
  }
  const seen = new Set<number>();
  const components: number[][] = [];
  for (const id of nodeIds) {
    if (seen.has(id)) continue;
    const queue = [id];
    const group: number[] = [];
    seen.add(id);
    while (queue.length) {
      const current = queue.shift()!;
      group.push(current);
      for (const next of adjacency.get(current) ?? []) {
        if (!seen.has(next)) {
          seen.add(next);
          queue.push(next);
        }
      }
    }
    components.push(group.sort((a, b) => a - b));
  }
  return components.sort((a, b) => b.length - a.length || a[0] - b[0]);
}

/**
 * Force-directed layout with collision relief and component packing.
 *
 * Deterministic (seeded by node index) so a re-render does not reshuffle the graph, and
 * it stops as soon as the configuration is stable instead of animating forever.
 */
export function simulateLayout(
  nodes: LayoutNode[],
  links: LayoutLink[],
  options: {
    iterations?: number;
    repulsion?: number;
    springLength?: number;
    springStrength?: number;
    centerStrength?: number;
    damping?: number;
    collisionPadding?: number;
  } = {}
): LayoutNode[] {
  const iterations = options.iterations ?? 320;
  const repulsion = options.repulsion ?? 9000;
  const springLength = options.springLength ?? 130;
  const springStrength = options.springStrength ?? 0.035;
  const centerStrength = options.centerStrength ?? 0.012;
  const damping = options.damping ?? 0.86;
  const collisionPadding = options.collisionPadding ?? 16;

  const work = nodes.map((node) => ({ ...node }));
  const index = new Map<number, number>();
  work.forEach((node, i) => index.set(node.id, i));

  // Seed deterministically on a spiral so symmetric graphs do not start degenerate.
  work.forEach((node, i) => {
    if (!Number.isFinite(node.x) || !Number.isFinite(node.y) || (node.x === 0 && node.y === 0)) {
      const angle = i * 2.399963; // golden angle
      const radius = 40 + Math.sqrt(i) * 46;
      node.x = Math.cos(angle) * radius;
      node.y = Math.sin(angle) * radius;
    }
    node.vx = 0;
    node.vy = 0;
  });

  const pairLinks = links
    .map((link) => ({ a: index.get(link.source), b: index.get(link.target) }))
    .filter((pair): pair is { a: number; b: number } => pair.a !== undefined && pair.b !== undefined);

  for (let step = 0; step < iterations; step++) {
    // repulsion
    for (let i = 0; i < work.length; i++) {
      for (let j = i + 1; j < work.length; j++) {
        const u = work[i];
        const v = work[j];
        let dx = u.x - v.x;
        let dy = u.y - v.y;
        let distSq = dx * dx + dy * dy;
        if (distSq < 0.01) {
          dx = (i % 7) - 3 + 0.5;
          dy = (j % 5) - 2 + 0.5;
          distSq = dx * dx + dy * dy;
        }
        const dist = Math.sqrt(distSq);
        const force = repulsion / distSq;
        const fx = (dx / dist) * force;
        const fy = (dy / dist) * force;
        u.vx = (u.vx ?? 0) + fx;
        u.vy = (u.vy ?? 0) + fy;
        v.vx = (v.vx ?? 0) - fx;
        v.vy = (v.vy ?? 0) - fy;
      }
    }

    // springs
    for (const { a, b } of pairLinks) {
      const u = work[a];
      const v = work[b];
      const dx = v.x - u.x;
      const dy = v.y - u.y;
      const dist = Math.sqrt(dx * dx + dy * dy) + 0.1;
      const force = (dist - springLength) * springStrength;
      const fx = (dx / dist) * force;
      const fy = (dy / dist) * force;
      u.vx = (u.vx ?? 0) + fx;
      u.vy = (u.vy ?? 0) + fy;
      v.vx = (v.vx ?? 0) - fx;
      v.vy = (v.vy ?? 0) - fy;
    }

    // centering + integrate
    let movement = 0;
    for (const node of work) {
      node.vx = ((node.vx ?? 0) - node.x * centerStrength) * damping;
      node.vy = ((node.vy ?? 0) - node.y * centerStrength) * damping;
      const stepX = clamp(node.vx, -40, 40);
      const stepY = clamp(node.vy, -40, 40);
      node.x += stepX;
      node.y += stepY;
      movement += Math.abs(stepX) + Math.abs(stepY);
    }

    // collision relief: labels need breathing room, discs must not overlap
    for (let i = 0; i < work.length; i++) {
      for (let j = i + 1; j < work.length; j++) {
        const u = work[i];
        const v = work[j];
        const minDist =
          baseNodeRadius(u.degree ?? 0) + baseNodeRadius(v.degree ?? 0) + collisionPadding;
        const dx = v.x - u.x;
        const dy = v.y - u.y;
        const dist = Math.hypot(dx, dy) || 0.01;
        if (dist < minDist) {
          const push = (minDist - dist) / dist / 2;
          u.x -= dx * push;
          u.y -= dy * push;
          v.x += dx * push;
          v.y += dy * push;
        }
      }
    }

    if (movement / Math.max(work.length, 1) < 0.02) break; // stable: stop animating
  }

  return work;
}

/**
 * Pack connected components into a grid so separate clusters never pile into one blob.
 *
 * Components are sorted by size, laid out in a near-square grid, and each one is
 * centred inside its cell. Isolated nodes therefore form their own sparse ring instead
 * of being dragged into the giant component.
 */
export function packComponents(
  nodes: LayoutNode[],
  links: LayoutLink[],
  spacing: number = 120
): LayoutNode[] {
  const ids = nodes.map((node) => node.id);
  const components = findComponents(ids, links);
  if (components.length <= 1) return nodes;

  const byId = new Map(nodes.map((node) => [node.id, { ...node }]));
  const sizes = components.map((group) => {
    const members = group.map((id) => byId.get(id)!);
    const bounds = boundsOf(members)!;
    return {
      group,
      width: Math.max(bounds.maxX - bounds.minX, 1),
      height: Math.max(bounds.maxY - bounds.minY, 1),
      centerX: (bounds.minX + bounds.maxX) / 2,
      centerY: (bounds.minY + bounds.maxY) / 2,
    };
  });

  const columns = Math.max(1, Math.ceil(Math.sqrt(components.length)));
  const cellWidth = Math.max(...sizes.map((s) => s.width)) + spacing;
  const cellHeight = Math.max(...sizes.map((s) => s.height)) + spacing;

  sizes.forEach((size, i) => {
    const row = Math.floor(i / columns);
    const col = i % columns;
    const cellCenterX = (col - (columns - 1) / 2) * cellWidth;
    const cellCenterY = (row - (Math.ceil(components.length / columns) - 1) / 2) * cellHeight;
    for (const id of size.group) {
      const node = byId.get(id)!;
      node.x = node.x - size.centerX + cellCenterX;
      node.y = node.y - size.centerY + cellCenterY;
      byId.set(id, node);
    }
  });

  return nodes.map((node) => byId.get(node.id)!);
}

// ------------------------------------------------------------------------- labels
export interface LabelRuleInput {
  id: number;
  degree: number;
  label: string;
}

/**
 * Which labels are drawn at the current zoom.
 *
 * Never "all or nothing": at low zoom only hubs and the focused nodes are labelled, so
 * names stay readable and the canvas is not a wall of text.
 */
export function labelsToShow(
  nodes: LabelRuleInput[],
  k: number,
  options: {
    selectedId?: number | null;
    hoveredId?: number | null;
    showAll?: boolean;
    maxLabels?: number;
  } = {}
): Set<number> {
  const { selectedId = null, hoveredId = null, showAll = false, maxLabels = 90 } = options;
  const result = new Set<number>();
  if (selectedId != null) result.add(selectedId);
  if (hoveredId != null) result.add(hoveredId);

  const ranked = [...nodes].sort((a, b) => b.degree - a.degree || a.id - b.id);
  const budget = showAll ? maxLabels : k < 0.7 ? 14 : k < 1.4 ? 34 : maxLabels;
  for (const node of ranked.slice(0, Math.max(budget, result.size + 1))) {
    result.add(node.id);
  }
  return result;
}

/** Distance from a point to a segment, in screen pixels (used for edge hit-testing). */
export function distanceToSegment(  px: number,
  py: number,
  ax: number,
  ay: number,
  bx: number,
  by: number
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq === 0) return Math.hypot(px - ax, py - ay);
  const t = clamp(((px - ax) * dx + (py - ay) * dy) / lengthSq, 0, 1);
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

/** Perpendicular offset so parallel / reciprocal edges are visually distinguishable. */
export function parallelOffset(pairIndex: number, spacing = 9): number {
  if (pairIndex === 0) return 0;
  const step = Math.ceil(pairIndex / 2) * spacing;
  return pairIndex % 2 === 1 ? step : -step;
}

// ------------------------------------------------------------------ edge labels

export interface EdgeLabelInput {
  id: string;
  /** On-screen length of the edge, in pixels. */
  lengthPx: number;
}

/**
 * Which edges get a short relation-type label.
 *
 * The rule is "is there room on the *screen*", not "is the zoom factor high enough":
 * at one zoom level a long edge can hold four characters while a short one cannot, and
 * using `k` made dense clusters sprout a wall of unreadable text. Selected and hovered
 * edges are always labelled so the user can confirm what they clicked.
 */
export function edgeLabelsToShow(
  edges: EdgeLabelInput[],
  options: {
    selectedId?: string | null;
    hoveredId?: string | null;
    minLengthPx?: number;
    maxLabels?: number;
  } = {}
): Set<string> {
  const { selectedId = null, hoveredId = null, minLengthPx = 56, maxLabels = 40 } = options;
  const result = new Set<string>();
  if (selectedId) result.add(selectedId);
  if (hoveredId) result.add(hoveredId);

  const candidates = edges
    .filter((edge) => edge.lengthPx >= minLengthPx && !result.has(edge.id))
    .sort((a, b) => b.lengthPx - a.lengthPx || (a.id < b.id ? -1 : 1));

  for (const edge of candidates) {
    if (result.size >= maxLabels) break;
    result.add(edge.id);
  }
  return result;
}

