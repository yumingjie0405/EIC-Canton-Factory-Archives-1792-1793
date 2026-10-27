/**
 * Tests for the canvas maths in `src/lib/graphLayout.ts`.
 *
 * These guard the two reported defects and the readability requirements of Stage 10A:
 *   1. panning must move the world under a fixed camera without touching the layout;
 *   2. zooming must separate nodes while node discs and label text stay near-constant
 *      (no `r * k` linear blow-up);
 *   3. components must not be packed into one overlapping blob.
 *
 * Run (from frontend/):
 *   node node_modules/typescript/bin/tsc src/lib/graphLayout.ts \
 *        --outDir node_modules/.cache/eic-graphlayout --module commonjs \
 *        --target es2020 --skipLibCheck
 *   node --test tests/graph-layout.test.cjs
 */
const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const layout = require(
  path.join(__dirname, "..", "node_modules", ".cache", "eic-graphlayout", "graphLayout.js")
);

const {
  CAMERA_MIN_K,
  CAMERA_MAX_K,
  FIT_MAX_K,
  baseNodeRadius,
  screenNodeRadius,
  labelFontSize,
  worldToScreen,
  screenToWorld,
  zoomAtPoint,
  boundsOf,
  fitCamera,
  resetCamera,
  focusCamera,
  findComponents,
  simulateLayout,
  packComponents,
  labelsToShow,
  edgeLabelsToShow,
  distanceToSegment,
  parallelOffset,
} = layout;

const VIEWPORT = { width: 1000, height: 800 };

test("worldToScreen and screenToWorld round-trip", () => {
  const camera = { x: 120, y: -40, k: 1.7 };
  const screen = worldToScreen(37, -12, camera, VIEWPORT);
  const world = screenToWorld(screen.sx, screen.sy, camera, VIEWPORT);
  assert.ok(Math.abs(world.x - 37) < 1e-9);
  assert.ok(Math.abs(world.y + 12) < 1e-9);
});

test("panning translates the view and leaves world layout untouched", () => {
  const node = { x: 500, y: 300 };
  const before = worldToScreen(node.x, node.y, { x: 0, y: 0, k: 1 }, VIEWPORT);
  const after = worldToScreen(node.x, node.y, { x: 80, y: -35, k: 1 }, VIEWPORT);
  assert.equal(after.sx - before.sx, 80);
  assert.equal(after.sy - before.sy, -35);
  assert.deepEqual(node, { x: 500, y: 300 });
});

test("panning does not change the scale, so node sizes cannot drift", () => {
  const radiusBefore = screenNodeRadius(18, 1);
  const radiusAfter = screenNodeRadius(18, 1);
  assert.equal(radiusBefore, radiusAfter);
});

test("zoomAtPoint keeps the cursor point anchored", () => {
  const camera = { x: 40, y: 20, k: 1 };
  const fx = 720;
  const fy = 180;
  const worldUnderCursor = screenToWorld(fx, fy, camera, VIEWPORT);

  for (const factor of [1.25, 0.8, 2, 0.5]) {
    const next = zoomAtPoint(camera, factor, fx, fy, VIEWPORT);
    const after = worldToScreen(worldUnderCursor.x, worldUnderCursor.y, next, VIEWPORT);
    assert.ok(Math.abs(after.sx - fx) < 1e-6, `x anchored for factor ${factor}`);
    assert.ok(Math.abs(after.sy - fy) < 1e-6, `y anchored for factor ${factor}`);
  }
});

test("zoomAtPoint clamps to the camera limits instead of running away", () => {
  const zoomedIn = zoomAtPoint({ x: 0, y: 0, k: 1 }, 1000, 500, 400, VIEWPORT);
  assert.equal(zoomedIn.k, CAMERA_MAX_K);
  const zoomedOut = zoomAtPoint({ x: 0, y: 0, k: 1 }, 0.0001, 500, 400, VIEWPORT);
  assert.equal(zoomedOut.k, CAMERA_MIN_K);
  const unchanged = zoomAtPoint({ x: 3, y: 4, k: CAMERA_MAX_K }, 2, 500, 400, VIEWPORT);
  assert.deepEqual(unchanged, { x: 3, y: 4, k: CAMERA_MAX_K });
});

test("node radius grows sub-linearly with zoom", () => {
  const base = baseNodeRadius(9);
  const atOne = screenNodeRadius(base, 1);
  const atFour = screenNodeRadius(base, 4);

  // Linear scaling would multiply by 4; the sub-linear curve must stay far below that.
  assert.ok(atFour / atOne < 1.7, `radius ratio ${atFour / atOne} must stay sub-linear`);
  assert.ok(atFour > atOne, "zooming in still makes nodes slightly larger");
  // A 4x zoom must never produce a disc big enough to swallow the viewport.
  assert.ok(atFour <= 30);
  // ... and maximum zoom-out must stay clickable.
  assert.ok(screenNodeRadius(base, CAMERA_MIN_K) >= 7);
});

test("label font size stays legible across the whole zoom range", () => {
  for (const k of [CAMERA_MIN_K, 0.3, 0.7, 1, 2, CAMERA_MAX_K]) {
    const size = labelFontSize(k);
    assert.ok(size >= 9 && size <= 13, `font ${size} for k=${k}`);
  }
  assert.ok(labelFontSize(CAMERA_MAX_K) > labelFontSize(CAMERA_MIN_K));
});

test("baseNodeRadius is bounded for hubs and safe for junk input", () => {
  assert.equal(baseNodeRadius(0), 11);
  assert.ok(baseNodeRadius(1) > baseNodeRadius(0));
  assert.ok(baseNodeRadius(5000) <= 26);
  assert.equal(baseNodeRadius(Number.NaN), 11);
  assert.equal(baseNodeRadius(-4), 11);
});

test("fitCamera frames every node inside the viewport", () => {
  const nodes = [
    { x: -300, y: -120 },
    { x: 260, y: 80 },
    { x: 0, y: 400 },
  ];
  const camera = fitCamera(nodes, VIEWPORT, 48);
  for (const node of nodes) {
    const { sx, sy } = worldToScreen(node.x, node.y, camera, VIEWPORT);
    assert.ok(sx >= 48 - 1e-6 && sx <= VIEWPORT.width - 48 + 1e-6, `x ${sx} in frame`);
    assert.ok(sy >= 48 - 1e-6 && sy <= VIEWPORT.height - 48 + 1e-6, `y ${sy} in frame`);
  }
  assert.ok(camera.k >= CAMERA_MIN_K && camera.k <= CAMERA_MAX_K);
});

test("fitCamera does not blow a tiny graph up to the camera limit", () => {
  // Two nodes 40 px apart would fit at k=6 (the camera max); auto-fit must stay
  // readable so panning still feels like panning.
  const camera = fitCamera([{ x: 0, y: 0 }, { x: 40, y: 0 }], VIEWPORT);
  assert.ok(camera.k <= FIT_MAX_K, `fit k ${camera.k} must stay <= ${FIT_MAX_K}`);
  assert.ok(camera.k > 1, "but it still fills the viewport as much as allowed");
  const wide = fitCamera([{ x: 0, y: 0 }, { x: 4000, y: 0 }], VIEWPORT);
  assert.ok(wide.k < 1, "large graphs are still scaled down");
  assert.ok(wide.k >= CAMERA_MIN_K);
});

test("fitCamera is safe for empty graphs and degenerate viewports", () => {  assert.deepEqual(fitCamera([], VIEWPORT), resetCamera());
  assert.deepEqual(fitCamera([{ x: 5, y: 5 }], { width: 0, height: 0 }), resetCamera());
  const single = fitCamera([{ x: 5, y: 5 }], VIEWPORT);
  const { sx, sy } = worldToScreen(5, 5, single, VIEWPORT);
  assert.ok(Math.abs(sx - VIEWPORT.width / 2) < 1e-6);
  assert.ok(Math.abs(sy - VIEWPORT.height / 2) < 1e-6);
  assert.ok(Number.isFinite(single.k));
});

test("focusCamera centres the requested node", () => {
  const camera = focusCamera({ x: 640, y: -210 }, 1.6);
  const { sx, sy } = worldToScreen(640, -210, camera, VIEWPORT);
  assert.ok(Math.abs(sx - VIEWPORT.width / 2) < 1e-6);
  assert.ok(Math.abs(sy - VIEWPORT.height / 2) < 1e-6);
  assert.equal(camera.k, 1.6);
});

test("boundsOf returns null for no nodes", () => {
  assert.equal(boundsOf([]), null);
  assert.deepEqual(boundsOf([{ x: 2, y: 9 }]), { minX: 2, minY: 9, maxX: 2, maxY: 9 });
});

test("findComponents separates islands and sorts by size", () => {
  const nodeIds = [1, 2, 3, 4, 5, 6];
  const links = [
    { source: 1, target: 2 },
    { source: 2, target: 3 },
    { source: 4, target: 5 },
  ];
  const components = findComponents(nodeIds, links);
  assert.equal(components.length, 3);
  assert.deepEqual(components[0], [1, 2, 3]);
  assert.deepEqual(components[1], [4, 5]);
  assert.deepEqual(components[2], [6]);
});

test("findComponents ignores links pointing outside the node set", () => {
  const components = findComponents([1, 2], [{ source: 1, target: 99 }]);
  assert.deepEqual(components, [[1], [2]]);
});

test("simulateLayout is deterministic and keeps nodes apart", () => {
  const nodes = [
    { id: 1, x: 0, y: 0, degree: 2 },
    { id: 2, x: 0, y: 0, degree: 2 },
    { id: 3, x: 0, y: 0, degree: 2 },
    { id: 4, x: 0, y: 0, degree: 2 },
  ];
  const links = [
    { source: 1, target: 2 },
    { source: 2, target: 3 },
    { source: 3, target: 4 },
    { source: 4, target: 1 },
  ];
  const first = simulateLayout(nodes, links, { iterations: 60 });
  const second = simulateLayout(nodes, links, { iterations: 60 });
  assert.deepEqual(
    first.map((n) => [n.id, Math.round(n.x * 1e6) / 1e6, Math.round(n.y * 1e6) / 1e6]),
    second.map((n) => [n.id, Math.round(n.x * 1e6) / 1e6, Math.round(n.y * 1e6) / 1e6])
  );
  for (let i = 0; i < first.length; i++) {
    for (let j = i + 1; j < first.length; j++) {
      const dist = Math.hypot(first[i].x - first[j].x, first[i].y - first[j].y);
      const minDist =
        baseNodeRadius(first[i].degree) + baseNodeRadius(first[j].degree);
      assert.ok(dist > minDist * 0.5, `nodes ${i}/${j} too close: ${dist}`);
    }
  }
  // The caller's array must not be mutated in place.
  assert.ok(nodes.every((node) => node.x === 0 && node.y === 0));
});

test("packComponents spreads separate components instead of stacking them", () => {
  const nodes = [
    { id: 1, x: 0, y: 0, degree: 1 },
    { id: 2, x: 20, y: 0, degree: 1 },
    { id: 3, x: 0, y: 0, degree: 1 },
    { id: 4, x: 20, y: 0, degree: 1 },
  ];
  const links = [
    { source: 1, target: 2 },
    { source: 3, target: 4 },
  ];
  const packed = packComponents(nodes, links, 200);
  const dist = Math.hypot(
    packed[0].x + packed[1].x - packed[2].x - packed[3].x,
    packed[0].y + packed[1].y - packed[2].y - packed[3].y
  );
  assert.ok(dist > 150, `component centres must separate, got ${dist}`);
});

test("packComponents is a no-op for a single component", () => {
  const nodes = [
    { id: 1, x: 0, y: 0 },
    { id: 2, x: 10, y: 10 },
  ];
  assert.deepEqual(packComponents(nodes, [{ source: 1, target: 2 }]), nodes);
});

test("labelsToShow budgets labels by zoom and always keeps the focus", () => {
  const nodes = Array.from({ length: 200 }, (_, index) => ({
    id: index + 1,
    degree: 200 - index,
    label: `Person ${index + 1}`,
  }));

  const far = labelsToShow(nodes, 0.4, { selectedId: 180 });
  assert.ok(far.size <= 15, `low zoom drew ${far.size} labels`);
  assert.ok(far.has(180), "selected label is always kept");

  const mid = labelsToShow(nodes, 1, { hoveredId: 190 });
  assert.ok(mid.size <= 35 && mid.size > far.size, `mid zoom drew ${mid.size}`);

  const near = labelsToShow(nodes, 2);
  assert.ok(near.size > mid.size);
  assert.ok(near.has(1), "highest-degree node is labelled first");

  const all = labelsToShow(nodes, 0.4, { showAll: true, maxLabels: 500 });
  assert.equal(all.size, 200);
});

test("distanceToSegment measures perpendicular and endpoint distances", () => {
  assert.equal(distanceToSegment(5, 3, 0, 0, 10, 0), 3);
  assert.equal(distanceToSegment(-4, 0, 0, 0, 10, 0), 4);
  assert.equal(distanceToSegment(13, 4, 0, 0, 10, 0), 5);
  assert.equal(distanceToSegment(5, 0, 0, 0, 10, 0), 0);
});

test("parallelOffset separates opposite and multi edges without overlap", () => {
  assert.equal(parallelOffset(0), 0);
  assert.notEqual(parallelOffset(1), parallelOffset(2));
  assert.equal(parallelOffset(1), -parallelOffset(2));
  assert.ok(Math.abs(parallelOffset(3)) > Math.abs(parallelOffset(2)));
});

test("edge labels appear only where the line is long enough", () => {
  const edges = [
    { id: "link_1", lengthPx: 120 },
    { id: "link_2", lengthPx: 40 },
  ];
  const labels = edgeLabelsToShow(edges);
  assert.ok(labels.has("link_1"), "long edge gets its relation type");
  assert.ok(!labels.has("link_2"), "short edge stays clean");
});

test("selected and hovered edges are always labelled even when short", () => {
  const edges = [{ id: "link_short", lengthPx: 12 }];
  assert.ok(edgeLabelsToShow(edges, { selectedId: "link_short" }).has("link_short"));
  assert.ok(edgeLabelsToShow(edges, { hoveredId: "link_short" }).has("link_short"));
});

test("edge label budget prefers the longest lines", () => {
  const edges = Array.from({ length: 60 }, (_, index) => ({
    id: `link_${index}`,
    lengthPx: 60 + index,
  }));
  const labels = edgeLabelsToShow(edges, { maxLabels: 10 });
  assert.equal(labels.size, 10);
  assert.ok(labels.has("link_59"), "longest edge is labelled");
  assert.ok(!labels.has("link_0"), "shortest candidates are dropped first");
});

test("the fit zoom of a dense graph produces no edge labels", () => {
  // 367-person group at ~47 %: most edges are far below the 56 px threshold.
  const edges = [
    { id: "a", lengthPx: 30 },
    { id: "b", lengthPx: 44 },
    { id: "c", lengthPx: 55 },
  ];
  assert.equal(edgeLabelsToShow(edges).size, 0);
});
