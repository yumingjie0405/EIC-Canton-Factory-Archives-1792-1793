"use client";

import React, {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from "react";
import { GraphNode, GraphLink } from "@/types/kg";
import { ROLE_COLOR_MAP, getRoleKey } from "./GraphLegend";
import { relationTypeLabel } from "@/lib/graphScope";
import {
  Camera,
  Viewport,
  baseNodeRadius,
  distanceToSegment,
  edgeLabelsToShow,
  fitCamera,
  focusCamera,
  labelFontSize,
  labelsToShow,
  packComponents,
  parallelOffset,
  resetCamera,
  screenNodeRadius,
  screenToWorld,
  simulateLayout,
  worldToScreen,
  zoomAtPoint,
} from "@/lib/graphLayout";

export interface NetworkGraphHandle {
  zoomBy: (factor: number) => void;
  fitToView: () => void;
  resetView: () => void;
  focusNode: (nodeId: number) => void;
}

interface NetworkGraphProps {
  nodes: GraphNode[];
  links: GraphLink[];
  selectedNodeId?: number | null;
  onSelectNode: (node: GraphNode | null) => void;
  onSelectLink?: (link: GraphLink | null) => void;
  onExpandNode?: (nodeId: number) => void;
  isLoading?: boolean;
  /** Draw every label instead of the zoom-dependent subset. */
  showAllLabels?: boolean;
  /** Layout mode; changing it re-lays out the *same* data set. */
  layoutMode?: "network" | "components";
}

type SimNode = GraphNode & { x: number; y: number };
type SimLink = { link: GraphLink; source: SimNode; target: SimNode; parallelIndex: number };

const DRAG_THRESHOLD_PX = 4;

/**
 * Minimum zoom applied when a specific node is focused (search result, relationship
 * endpoint). Focusing must always zoom *in* — staying at the fitted scale made the
 * "jump to this person" action look like nothing happened on large graphs.
 */
const FOCUS_MIN_K = 1.6;

/** How long a focus request may wait for the node to appear in the layout. */
const FOCUS_REQUEST_TTL_MS = 2000;

/**
 * Canvas renderer for the person-relationship graph.
 *
 * Two reported defects, two structural fixes:
 *
 * 1. **Panning "did not work".** The transform state changed, but the canvas was only
 *    repainted from inside the force-simulation loop, which stops as soon as the layout
 *    settles — so after the first second, dragging moved nothing on screen. Every camera
 *    change now schedules an explicit repaint (`requestDraw`), and pointer capture +
 *    `pointercancel`/`lostpointercapture`/`blur` handling guarantees the drag state ends.
 *
 * 2. **Zoom inflated nodes and labels.** The old draw call did `ctx.scale(k, k)` and then
 *    drew radii and text inside that transform, so text grew linearly with zoom. Nodes and
 *    labels are now laid out in *screen space* (`screenNodeRadius`, `labelFontSize`, both
 *    sub-linear in k), so zooming spreads nodes apart instead of inflating them.
 */
export const NetworkGraph = forwardRef<NetworkGraphHandle, NetworkGraphProps>(
  function NetworkGraph(
    {
      nodes,
      links,
      selectedNodeId,
      onSelectNode,
      onSelectLink,
      isLoading,
      showAllLabels = false,
      layoutMode = "components",
    },
    ref
  ) {
    const canvasRef = useRef<HTMLCanvasElement | null>(null);
    const wrapRef = useRef<HTMLDivElement | null>(null);

    const simNodesRef = useRef<SimNode[]>([]);
    const simLinksRef = useRef<SimLink[]>([]);
    const cameraRef = useRef<Camera>(resetCamera());
    const viewportRef = useRef<Viewport>({ width: 0, height: 0 });
    // A focus request that layout passes must honour. Searching for a person adds it to
    // the graph and expands its neighbours in the same interaction, and every data change
    // re-fits the camera — without this the requested node scrolled away again as soon as
    // the new nodes (or the next live extraction poll) arrived. The lock is released as
    // soon as the reader takes control of the camera (wheel, pan, zoom buttons, or
    // selecting a different person).
    const focusLockRef = useRef<{ id: number; armedAt: number } | null>(null);

    const rafRef = useRef<number | null>(null);

    const pointerStateRef = useRef<{
      mode: "idle" | "pending" | "pan" | "node";
      pointerId: number | null;
      startX: number;
      startY: number;
      lastX: number;
      lastY: number;
      moved: boolean;
      node: SimNode | null;
    }>({
      mode: "idle",
      pointerId: null,
      startX: 0,
      startY: 0,
      lastX: 0,
      lastY: 0,
      moved: false,
      node: null,
    });

    const hoveredRef = useRef<{ node: SimNode | null; link: SimLink | null }>({
      node: null,
      link: null,
    });
    const selectedLinkRef = useRef<string | null>(null);
    /** Screen-space boxes of drawn edge labels, so clicking the text selects the edge. */
    const edgeLabelHitsRef = useRef<
      Array<{
        linkId: string;
        box: { minX: number; minY: number; maxX: number; maxY: number };
        item: SimLink;
      }>
    >([]);
    const [zoomPercent, setZoomPercent] = useState(100);

    // --------------------------------------------------------------------- draw
    const draw = useCallback(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const ctx = canvas.getContext("2d");
      if (!ctx) return;

      const dpr = window.devicePixelRatio || 1;
      const { width, height } = viewportRef.current;
      if (width <= 0 || height <= 0) return;

      ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
      ctx.clearRect(0, 0, width, height);

      const camera = cameraRef.current;
      const simNodes = simNodesRef.current;
      const simLinks = simLinksRef.current;
      const hoveredNodeId = hoveredRef.current.node?.id ?? null;
      const hoveredLinkId = hoveredRef.current.link?.link.id ?? null;

      const activeId = hoveredNodeId ?? selectedNodeId ?? null;
      const neighbours = new Set<number>();
      if (activeId != null) {
        neighbours.add(activeId);
        for (const item of simLinks) {
          if (item.source.id === activeId) neighbours.add(item.target.id);
          if (item.target.id === activeId) neighbours.add(item.source.id);
        }
      }

      const labelIds = labelsToShow(
        simNodes.map((node) => ({ id: node.id, degree: node.degree ?? 0, label: node.label })),
        camera.k,
        { selectedId: selectedNodeId ?? null, hoveredId: hoveredNodeId, showAll: showAllLabels }
      );

      // ---- edges, drawn in screen space so width/arrows never scale with zoom
      const edgeGeom: Array<{
        item: SimLink;
        labelX: number;
        labelY: number;
        lengthPx: number;
      }> = [];
      for (const item of simLinks) {
        const from = worldToScreen(item.source.x, item.source.y, camera, viewportRef.current);
        const to = worldToScreen(item.target.x, item.target.y, camera, viewportRef.current);
        const isActive =
          activeId == null ||
          (neighbours.has(item.source.id) && neighbours.has(item.target.id));
        const isSelectedEdge = selectedLinkRef.current === item.link.id;
        const isHoveredEdge = hoveredLinkId === item.link.id;

        const dx = to.sx - from.sx;
        const dy = to.sy - from.sy;
        const len = Math.hypot(dx, dy) || 1;
        const normX = -dy / len;
        const normY = dx / len;
        const offset = parallelOffset(item.parallelIndex);
        const midX = (from.sx + to.sx) / 2 + normX * offset;
        const midY = (from.sy + to.sy) / 2 + normY * offset;

        ctx.beginPath();
        ctx.moveTo(from.sx, from.sy);
        if (offset === 0) {
          ctx.lineTo(to.sx, to.sy);
        } else {
          ctx.quadraticCurveTo(midX, midY, to.sx, to.sy);
        }
        const relationType = String(item.link.relationType || "other");
        ctx.strokeStyle = isSelectedEdge
          ? "#1d4ed8"
          : isHoveredEdge
            ? "#4f46e5"
            : isActive
              ? edgeColor(relationType, 0.75)
              : "rgba(203, 213, 225, 0.35)";
        ctx.lineWidth = isSelectedEdge ? 3 : isActive ? 1.6 : 1;
        // The legend promises dashed edges for unconfirmed claims; keep that promise,
        // otherwise a candidate edge looks identical to a reviewed one.
        ctx.setLineDash(item.link.claimStatus && item.link.claimStatus !== "accepted" ? [6, 4] : []);
        ctx.stroke();
        ctx.setLineDash([]);

        if (item.link.direction !== "undirected") {
          const targetRadius = screenNodeRadius(
            baseNodeRadius(item.target.degree ?? 0),
            camera.k
          );
          const ax = to.sx - (dx / len) * (targetRadius + 3);
          const ay = to.sy - (dy / len) * (targetRadius + 3);
          const angle = Math.atan2(dy, dx);
          ctx.beginPath();
          ctx.moveTo(ax, ay);
          ctx.lineTo(ax - 8 * Math.cos(angle - Math.PI / 7), ay - 8 * Math.sin(angle - Math.PI / 7));
          ctx.lineTo(ax - 8 * Math.cos(angle + Math.PI / 7), ay - 8 * Math.sin(angle + Math.PI / 7));
          ctx.closePath();
          ctx.fillStyle = isSelectedEdge
            ? "#1d4ed8"
            : isActive
              ? edgeColor(relationType, 0.9)
              : "rgba(203, 213, 225, 0.4)";
          ctx.fill();
        }

        // Label anchor: 45 % along the edge (clear of the arrow head), pushed off the
        // line so the text never sits on top of the stroke it describes.
        const t = 0.45;
        const anchorX =
          offset === 0
            ? from.sx + (to.sx - from.sx) * t
            : (1 - t) * (1 - t) * from.sx + 2 * (1 - t) * t * midX + t * t * to.sx;
        const anchorY =
          offset === 0
            ? from.sy + (to.sy - from.sy) * t
            : (1 - t) * (1 - t) * from.sy + 2 * (1 - t) * t * midY + t * t * to.sy;
        edgeGeom.push({
          item,
          labelX: anchorX + normX * (offset >= 0 ? 10 : -10),
          labelY: anchorY + normY * (offset >= 0 ? 10 : -10),
          lengthPx: len,
        });
      }

      // ---- nodes, also screen space
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";

      // Screen-space boxes already claimed: node discs and drawn labels. A label is
      // skipped when its plate would land on top of another disc or plate, which is
      // what makes a dense cluster unreadable otherwise.
      const discBoxes = simNodes.map((node) => {
        const { sx, sy } = worldToScreen(node.x, node.y, camera, viewportRef.current);
        const radius = screenNodeRadius(baseNodeRadius(node.degree ?? 0), camera.k);
        return { minX: sx - radius, minY: sy - radius, maxX: sx + radius, maxY: sy + radius };
      });
      const labelBoxes: { minX: number; minY: number; maxX: number; maxY: number }[] = [];

      for (let nodeIndex = 0; nodeIndex < simNodes.length; nodeIndex++) {
        const node = simNodes[nodeIndex];
        const { sx, sy } = worldToScreen(node.x, node.y, camera, viewportRef.current);
        const radius = screenNodeRadius(baseNodeRadius(node.degree ?? 0), camera.k);
        const isSelected = node.id === selectedNodeId;
        const isHovered = node.id === hoveredNodeId;
        const dimmed = activeId != null && !neighbours.has(node.id);
        const role = ROLE_COLOR_MAP[getRoleKey(node.role)] ?? ROLE_COLOR_MAP.other;

        ctx.globalAlpha = dimmed ? (selectedNodeId != null ? 0.25 : 0.6) : 1;

        if (isSelected || isHovered) {
          ctx.beginPath();
          ctx.arc(sx, sy, radius + 5, 0, Math.PI * 2);
          ctx.fillStyle = isSelected ? "rgba(79, 70, 229, 0.22)" : "rgba(148, 163, 184, 0.22)";
          ctx.fill();
        }

        ctx.beginPath();
        ctx.arc(sx, sy, radius, 0, Math.PI * 2);
        ctx.fillStyle = isSelected ? "#312e81" : roleHex(role);
        ctx.fill();
        ctx.lineWidth = isSelected ? 2.5 : 1.5;
        ctx.strokeStyle = isSelected ? "#4f46e5" : "#ffffff";
        ctx.stroke();

        if (labelIds.has(node.id)) {
          const fontSize = labelFontSize(camera.k);
          ctx.font = `${isSelected ? "600 " : ""}${fontSize}px Inter, system-ui, sans-serif`;
          const text = node.label.length > 18 ? `${node.label.slice(0, 17)}…` : node.label;
          const textWidth = ctx.measureText(text).width;
          const plateW = textWidth + 8;
          const plateH = fontSize + 6;
          const plateBox = {
            minX: sx - plateW / 2,
            minY: sy + radius + 3,
            maxX: sx + plateW / 2,
            maxY: sy + radius + 3 + plateH,
          };
          const mustShow = isSelected || isHovered;
          const collides =
            labelBoxes.some((box) => rectsOverlap(box, plateBox, 1)) ||
            discBoxes.some(
              (box, discIndex) => discIndex !== nodeIndex && rectsOverlap(box, plateBox, 1)
            );
          if (mustShow || !collides) {
            labelBoxes.push(plateBox);
            ctx.fillStyle = "rgba(255,255,255,0.82)";
            roundRect(ctx, plateBox.minX, plateBox.minY, plateW, plateH, 4);
            ctx.fill();
            ctx.fillStyle = isSelected ? "#312e81" : "#334155";
            ctx.fillText(text, sx, plateBox.minY + plateH / 2);
          }
        }

        ctx.globalAlpha = 1;
      }

      // ---- edge labels (relation type, 2-4 characters) above the nodes
      const edgeLabelIds = edgeLabelsToShow(
        edgeGeom.map((geom) => ({ id: geom.item.link.id, lengthPx: geom.lengthPx })),
        {
          selectedId: selectedLinkRef.current,
          hoveredId: hoveredLinkId,
        }
      );
      const edgeFontSize = 10;
      ctx.font = `${edgeFontSize}px Inter, system-ui, sans-serif`;
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      edgeLabelHitsRef.current = [];
      for (const geom of edgeGeom) {
        const link = geom.item.link;
        if (!edgeLabelIds.has(link.id)) continue;

        const unconfirmed = Boolean(link.claimStatus && link.claimStatus !== "accepted");
        const text = `${unconfirmed ? "?" : ""}${relationTypeLabel(String(link.relationType || "other"))}`;
        const textWidth = ctx.measureText(text).width;
        const plateW = textWidth + 10;
        const plateH = edgeFontSize + 7;
        const box = {
          minX: geom.labelX - plateW / 2,
          minY: geom.labelY - plateH / 2,
          maxX: geom.labelX + plateW / 2,
          maxY: geom.labelY + plateH / 2,
        };
        const isSelectedEdge = selectedLinkRef.current === link.id;
        const isHoveredEdge = hoveredLinkId === link.id;
        const mustShow = isSelectedEdge || isHoveredEdge;
        const collides =
          labelBoxes.some((other) => rectsOverlap(other, box, 1)) ||
          discBoxes.some((other) => rectsOverlap(other, box, 1)) ||
          edgeLabelHitsRef.current.some((other) => rectsOverlap(other.box, box, 1));
        if (!mustShow && collides) continue;

        ctx.fillStyle = isSelectedEdge
          ? "rgba(219, 234, 254, 0.95)"
          : "rgba(255,255,255,0.86)";
        roundRect(ctx, box.minX, box.minY, plateW, plateH, 4);
        ctx.fill();
        ctx.strokeStyle = isSelectedEdge ? "#1d4ed8" : "rgba(148,163,184,0.45)";
        ctx.lineWidth = 1;
        ctx.stroke();
        ctx.fillStyle = unconfirmed
          ? isSelectedEdge
            ? "#1d4ed8"
            : "#64748b"
          : isSelectedEdge
            ? "#1d4ed8"
            : edgeColorLabel(String(link.relationType || "other"));
        ctx.fillText(text, geom.labelX, geom.labelY);
        edgeLabelHitsRef.current.push({ linkId: link.id, box, item: geom.item });
      }

      const percent = Math.round(camera.k * 100);
      setZoomPercent((prev) => (prev === percent ? prev : percent));
    }, [selectedNodeId, showAllLabels]);

    const requestDraw = useCallback(() => {
      // Cancel-then-schedule instead of a "pending" flag: a flag that was ever set
      // while its frame got cancelled (React StrictMode double-mount, remount, HMR)
      // would stay stuck and the canvas would never paint again.
      if (rafRef.current != null) window.cancelAnimationFrame(rafRef.current);
      rafRef.current = window.requestAnimationFrame(() => {
        rafRef.current = null;
        draw();
      });
    }, [draw]);

    // Draw requests must not be an effect dependency: `draw` changes whenever the
    // selection changes, and depending on it made the layout effect re-run on every
    // click — which re-fitted the camera, so selecting a node reset the zoom.
    const requestDrawRef = useRef(requestDraw);
    useEffect(() => {
      requestDrawRef.current = requestDraw;
    }, [requestDraw]);

    // A pure view-state change only needs a repaint, never a re-layout.
    useEffect(() => {
      requestDrawRef.current();
    }, [selectedNodeId, showAllLabels]);

    // ------------------------------------------------------------------- layout
    useEffect(() => {
      const degree = new Map<number, number>();
      for (const node of nodes) degree.set(node.id, 0);
      for (const link of links) {
        const s = typeof link.source === "object" ? (link.source as GraphNode).id : link.source;
        const t = typeof link.target === "object" ? (link.target as GraphNode).id : link.target;
        degree.set(s, (degree.get(s) ?? 0) + 1);
        degree.set(t, (degree.get(t) ?? 0) + 1);
      }

      const previous = new Map(simNodesRef.current.map((node) => [node.id, node]));
      const layoutNodes = nodes.map((node) => ({
        id: node.id,
        // Existing nodes keep their world position so a filter change does not reshuffle.
        x: previous.get(node.id)?.x ?? Number.NaN,
        y: previous.get(node.id)?.y ?? Number.NaN,
        degree: degree.get(node.id) ?? node.degree ?? 0,
      }));

      const layoutLinks = links
        .map((link) => ({
          source: typeof link.source === "object" ? (link.source as GraphNode).id : link.source,
          target: typeof link.target === "object" ? (link.target as GraphNode).id : link.target,
        }))
        .filter((link) => link.source !== link.target);

      let laidOut = simulateLayout(layoutNodes, layoutLinks);
      if (layoutMode === "components") {
        laidOut = packComponents(laidOut, layoutLinks);
      }
      const positions = new Map(laidOut.map((node) => [node.id, node]));

      const nodeIndex = new Map<number, SimNode>();
      const simNodes: SimNode[] = nodes.map((node) => {
        const pos = positions.get(node.id)!;
        const created: SimNode = {
          ...node,
          x: pos.x,
          y: pos.y,
          degree: degree.get(node.id) ?? node.degree ?? 0,
        };
        nodeIndex.set(node.id, created);
        return created;
      });

      const pairCount = new Map<string, number>();
      const simLinks: SimLink[] = [];
      for (const link of links) {
        const s = typeof link.source === "object" ? (link.source as GraphNode).id : link.source;
        const t = typeof link.target === "object" ? (link.target as GraphNode).id : link.target;
        const source = nodeIndex.get(s);
        const target = nodeIndex.get(t);
        if (!source || !target) continue;
        const key = [Math.min(s, t), Math.max(s, t)].join("-");
        const index = pairCount.get(key) ?? 0;
        pairCount.set(key, index + 1);
        simLinks.push({ link, source, target, parallelIndex: index });
      }

      simNodesRef.current = simNodes;
      simLinksRef.current = simLinks;

      // Fit once per data change so the graph can never be off-screen — unless the reader
      // asked to focus one person, in which case that node stays centred while the graph
      // grows underneath it.
      const viewport = viewportRef.current;
      const lock = focusLockRef.current;
      const lockNode = lock ? simNodes.find((node) => node.id === lock.id) : undefined;
      if (lock && !lockNode && Date.now() - lock.armedAt > FOCUS_REQUEST_TTL_MS) {
        // The requested person never made it into this layout (filtered out, removed):
        // give up on the focus instead of pinning a stale node id forever.
        focusLockRef.current = null;
      }
      if (lockNode) {
        cameraRef.current = focusCamera(
          { x: lockNode.x, y: lockNode.y },
          Math.max(cameraRef.current.k, FOCUS_MIN_K)
        );
      } else {
        cameraRef.current =
          viewport.width > 0 ? fitCamera(simNodes, viewport) : resetCamera();
      }
      setZoomPercent(Math.round(cameraRef.current.k * 100));
      requestDrawRef.current();
    }, [nodes, links, layoutMode]);

    // Following a focused person only makes sense while that person is the selection.
    useEffect(() => {
      const lock = focusLockRef.current;
      if (lock && selectedNodeId !== lock.id) focusLockRef.current = null;
    }, [selectedNodeId]);

    // ----------------------------------------------------------------- viewport
    useEffect(() => {
      const canvas = canvasRef.current;
      const wrap = wrapRef.current;
      if (!canvas || !wrap) return;

      const resize = () => {
        const dpr = window.devicePixelRatio || 1;
        const width = wrap.clientWidth;
        const height = wrap.clientHeight;
        if (width <= 0 || height <= 0) return;
        viewportRef.current = { width, height };
        canvas.width = Math.round(width * dpr);
        canvas.height = Math.round(height * dpr);
        canvas.style.width = `${width}px`;
        canvas.style.height = `${height}px`;
        requestDrawRef.current();
      };

      resize();
      const observer = new ResizeObserver(resize);
      observer.observe(wrap);
      window.addEventListener("resize", resize);
      return () => {
        observer.disconnect();
        window.removeEventListener("resize", resize);
      };
    }, []);

    // ---------------------------------------------------------------- hit test
    const hitTest = useCallback((clientX: number, clientY: number) => {
      const canvas = canvasRef.current;
      if (!canvas) return { node: null as SimNode | null, link: null as SimLink | null };
      const rect = canvas.getBoundingClientRect();
      const px = clientX - rect.left;
      const py = clientY - rect.top;
      const camera = cameraRef.current;
      const viewport = viewportRef.current;

      let nodeHit: SimNode | null = null;
      for (let i = simNodesRef.current.length - 1; i >= 0; i--) {
        const node = simNodesRef.current[i];
        const { sx, sy } = worldToScreen(node.x, node.y, camera, viewport);
        const radius = screenNodeRadius(baseNodeRadius(node.degree ?? 0), camera.k) + 4;
        if (Math.hypot(px - sx, py - sy) <= radius) {
          nodeHit = node;
          break;
        }
      }

      let linkHit: SimLink | null = null;
      if (!nodeHit) {
        // A drawn label is part of the edge's clickable area: clicking the text is the
        // most natural way to select the relationship it names.
        for (const hit of edgeLabelHitsRef.current) {
          if (
            px >= hit.box.minX &&
            px <= hit.box.maxX &&
            py >= hit.box.minY &&
            py <= hit.box.maxY
          ) {
            linkHit = hit.item;
            break;
          }
        }
      }
      if (!nodeHit && !linkHit) {
        let best = 8;
        for (const item of simLinksRef.current) {
          const from = worldToScreen(item.source.x, item.source.y, camera, viewport);
          const to = worldToScreen(item.target.x, item.target.y, camera, viewport);
          const distance = distanceToSegment(px, py, from.sx, from.sy, to.sx, to.sy);
          if (distance < best) {
            best = distance;
            linkHit = item;
          }
        }
      }
      return { node: nodeHit, link: linkHit };
    }, []);

    // ----------------------------------------------------------------- pointer
    const endPointer = useCallback(
      (cancelled: boolean) => {
        const state = pointerStateRef.current;
        const canvas = canvasRef.current;
        if (canvas && state.pointerId != null) {
          try {
            if (canvas.hasPointerCapture?.(state.pointerId)) {
              canvas.releasePointerCapture(state.pointerId);
            }
          } catch {
            /* capture already lost */
          }
        }
        if (!cancelled && !state.moved && state.mode === "pending") {
          const hit = hitTest(state.startX, state.startY);
          if (hit.node) {
            selectedLinkRef.current = null;
            onSelectNode(hit.node);
          } else if (hit.link) {
            selectedLinkRef.current = hit.link.link.id;
            onSelectLink?.(hit.link.link);
          } else {
            selectedLinkRef.current = null;
            onSelectNode(null);
          }
          requestDraw();
        }
        pointerStateRef.current = {
          mode: "idle",
          pointerId: null,
          startX: 0,
          startY: 0,
          lastX: 0,
          lastY: 0,
          moved: false,
          node: null,
        };
        if (canvas) canvas.style.cursor = "grab";
      },
      [hitTest, onSelectLink, onSelectNode, requestDraw]
    );

    const handlePointerDown = (event: React.PointerEvent<HTMLCanvasElement>) => {
      if (event.button !== 0) return;
      const canvas = canvasRef.current;
      if (!canvas) return;
      const hit = hitTest(event.clientX, event.clientY);
      try {
        canvas.setPointerCapture?.(event.pointerId);
      } catch {
        /* capture unsupported */
      }
      pointerStateRef.current = {
        mode: "pending",
        pointerId: event.pointerId,
        startX: event.clientX,
        startY: event.clientY,
        lastX: event.clientX,
        lastY: event.clientY,
        moved: false,
        node: hit.node,
      };
      canvas.style.cursor = "grabbing";
      event.preventDefault();
    };

    const handlePointerMove = (event: React.PointerEvent<HTMLCanvasElement>) => {
      const state = pointerStateRef.current;
      const canvas = canvasRef.current;

      if (state.mode === "idle") {
        const hit = hitTest(event.clientX, event.clientY);
        const changed =
          hit.node?.id !== hoveredRef.current.node?.id ||
          hit.link?.link.id !== hoveredRef.current.link?.link.id;
        hoveredRef.current = { node: hit.node, link: hit.link };
        if (canvas) canvas.style.cursor = hit.node || hit.link ? "pointer" : "grab";
        if (changed) requestDraw();
        return;
      }

      const dx = event.clientX - state.lastX;
      const dy = event.clientY - state.lastY;
      state.lastX = event.clientX;
      state.lastY = event.clientY;

      if (!state.moved) {
        const travelled = Math.hypot(event.clientX - state.startX, event.clientY - state.startY);
        if (travelled < DRAG_THRESHOLD_PX) return;
        state.moved = true;
        state.mode = state.node ? "node" : "pan";
      }

      if (state.mode === "node" && state.node && canvas) {
        const rect = canvas.getBoundingClientRect();
        const world = screenToWorld(
          event.clientX - rect.left,
          event.clientY - rect.top,
          cameraRef.current,
          viewportRef.current
        );
        state.node.x = world.x;
        state.node.y = world.y;
      } else if (state.mode === "pan") {
        focusLockRef.current = null;
        cameraRef.current = {
          ...cameraRef.current,
          x: cameraRef.current.x + dx,
          y: cameraRef.current.y + dy,
        };
      }
      requestDraw();
    };

    const handlePointerUp = (event: React.PointerEvent<HTMLCanvasElement>) => {
      if (pointerStateRef.current.pointerId !== event.pointerId) return;
      endPointer(false);
    };

    const handlePointerCancel = (event: React.PointerEvent<HTMLCanvasElement>) => {
      if (pointerStateRef.current.pointerId !== event.pointerId) return;
      endPointer(true);
    };

    // A wheel handler with { passive: false } is required: React's synthetic wheel
    // listener is passive in some browsers and preventDefault would be ignored.
    useEffect(() => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const listener = (event: WheelEvent) => {
        event.preventDefault();
        const rect = canvas.getBoundingClientRect();
        focusLockRef.current = null;
        cameraRef.current = zoomAtPoint(
          cameraRef.current,
          event.deltaY < 0 ? 1.12 : 1 / 1.12,
          event.clientX - rect.left,
          event.clientY - rect.top,
          viewportRef.current
        );
        setZoomPercent(Math.round(cameraRef.current.k * 100));
        requestDraw();
      };
      canvas.addEventListener("wheel", listener, { passive: false });
      return () => canvas.removeEventListener("wheel", listener);
    }, [requestDraw]);

    useEffect(() => {
      const cancel = () => endPointer(true);
      window.addEventListener("blur", cancel);
      return () => window.removeEventListener("blur", cancel);
    }, [endPointer]);

    useEffect(() => {
      requestDraw();
    }, [selectedNodeId, requestDraw]);

    useEffect(
      () => () => {
        if (rafRef.current != null) window.cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      },
      []
    );

    // --------------------------------------------------------- imperative API
    useImperativeHandle(
      ref,
      () => ({
        zoomBy: (factor: number) => {
          const viewport = viewportRef.current;
          cameraRef.current = zoomAtPoint(
            cameraRef.current,
            factor,
            viewport.width / 2,
            viewport.height / 2,
            viewport
          );
          focusLockRef.current = null;
          setZoomPercent(Math.round(cameraRef.current.k * 100));
          requestDraw();
        },
        fitToView: () => {
          focusLockRef.current = null;
          cameraRef.current = fitCamera(simNodesRef.current, viewportRef.current);
          setZoomPercent(Math.round(cameraRef.current.k * 100));
          requestDraw();
        },
        resetView: () => {
          focusLockRef.current = null;
          cameraRef.current = resetCamera();
          setZoomPercent(100);
          requestDraw();
        },
        focusNode: (nodeId: number) => {
          // Arm the lock first: the searched person may only reach the layout on the next
          // data change (it is appended to the graph, then its neighbours are expanded).
          focusLockRef.current = { id: nodeId, armedAt: Date.now() };
          const node = simNodesRef.current.find((candidate) => candidate.id === nodeId);
          if (!node) return;
          cameraRef.current = focusCamera(
            { x: node.x, y: node.y },
            Math.max(cameraRef.current.k, FOCUS_MIN_K)
          );
          setZoomPercent(Math.round(cameraRef.current.k * 100));
          requestDrawRef.current();
        },
      }),
      [requestDraw]
    );

    return (
      <div ref={wrapRef} className="relative h-full w-full touch-none overflow-hidden select-none">
        <canvas
          ref={canvasRef}
          onPointerDown={handlePointerDown}
          onPointerMove={handlePointerMove}
          onPointerUp={handlePointerUp}
          onPointerCancel={handlePointerCancel}
          onLostPointerCapture={() => endPointer(true)}
          className="block h-full w-full cursor-grab"
          data-network-canvas="true"
        />

        <div
          className="pointer-events-none absolute bottom-2 left-2 rounded-md bg-white/80 px-1.5 py-0.5 text-[10px] text-slate-500"
          data-zoom-readout="true"
        >
          {zoomPercent}%
        </div>

        {nodes.length === 0 && !isLoading && (
          <div className="absolute inset-0 flex flex-col items-center justify-center p-6 text-center">
            <h3 className="text-base font-semibold text-slate-800">当前筛选条件下暂无人物节点</h3>
            <p className="mt-1.5 max-w-sm text-xs text-slate-500">
              可尝试切换布局、放宽关系类型，或勾选“包含孤立节点”与“包含未证实待定实体”。
            </p>
          </div>
        )}

        {isLoading && (
          <div className="absolute inset-0 flex items-center justify-center bg-white/40 backdrop-blur-sm">
            <div className="rounded-xl bg-white px-3 py-2 text-xs text-slate-600 shadow">
              正在计算网络…
            </div>
          </div>
        )}
      </div>
    );
  }
);

// ---------------------------------------------------------------------- helpers
function roundRect(
  ctx: CanvasRenderingContext2D,
  x: number,
  y: number,
  width: number,
  height: number,
  radius: number
) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.arcTo(x + width, y, x + width, y + height, r);
  ctx.arcTo(x + width, y + height, x, y + height, r);
  ctx.arcTo(x, y + height, x, y, r);
  ctx.arcTo(x, y, x + width, y, r);
  ctx.closePath();
}

function rectsOverlap(
  a: { minX: number; minY: number; maxX: number; maxY: number },
  b: { minX: number; minY: number; maxX: number; maxY: number },
  gap = 0
): boolean {
  return !(
    a.maxX + gap <= b.minX ||
    b.maxX + gap <= a.minX ||
    a.maxY + gap <= b.minY ||
    b.maxY + gap <= a.minY
  );
}

function roleHex(role: { bg?: string } | undefined): string {
  const bg = role?.bg ?? "";
  if (bg.includes("amber")) return "#f59e0b";
  if (bg.includes("indigo")) return "#6366f1";
  if (bg.includes("rose")) return "#f43f5e";
  if (bg.includes("emerald")) return "#10b981";
  if (bg.includes("sky")) return "#0ea5e9";
  return "#94a3b8";
}

const RELATION_COLORS: Record<string, string> = {
  commercial: "rgba(37, 99, 235, ALPHA)",
  correspondence: "rgba(147, 51, 234, ALPHA)",
  guarantee: "rgba(217, 119, 6, ALPHA)",
  debt: "rgba(220, 38, 38, ALPHA)",
  diplomatic: "rgba(13, 148, 136, ALPHA)",
  kinship: "rgba(219, 39, 119, ALPHA)",
  employment: "rgba(22, 163, 74, ALPHA)",
  accompaniment: "rgba(2, 132, 199, ALPHA)",
  translation: "rgba(120, 53, 15, ALPHA)",
  other: "rgba(100, 116, 139, ALPHA)",
};

function edgeColor(relationType: string, alpha: number): string {
  const template = RELATION_COLORS[relationType] ?? RELATION_COLORS.other;
  return template.replace("ALPHA", String(alpha));
}

/** Solid, readable version of the edge colour (labels sit on a white plate). */
function edgeColorLabel(relationType: string): string {
  switch (relationType) {
    case "commercial":
      return "#1d4ed8";
    case "correspondence":
      return "#7e22ce";
    case "guarantee":
      return "#b45309";
    case "debt":
      return "#b91c1c";
    case "diplomatic":
      return "#0f766e";
    case "kinship":
      return "#be185d";
    case "employment":
      return "#15803d";
    case "accompaniment":
      return "#0369a1";
    case "translation":
      return "#78350f";
    default:
      return "#475569";
  }
}
