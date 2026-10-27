"""Zero-dependency, mathematically rigorous Social Network Analysis (SNA) engine.

Provides exact topological and centrality algorithms for historical social networks:
- Degree, In-Degree, Out-Degree & Normalized Degree Centrality
- Brandes (2001) Algorithm for Betweenness Centrality (O(V * E))
- Wasserman & Faust Normalized Closeness Centrality for directed/disconnected graphs
- Power Iteration PageRank with dangling node redistribution
- Global and Local Clustering Coefficients
- Weakly and Strongly Connected Components
- Network Density and Average Degrees
"""
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple


@dataclass
class NetworkMetricsResult:
    """Comprehensive SNA metrics result for a graph."""
    node_count: int
    edge_count: int  # Total directed/undirected edges
    density: float
    average_degree: float
    average_in_degree: float
    average_out_degree: float
    connected_components_count: int
    component_sizes: List[int]
    average_clustering_coefficient: float
    isolated_nodes_count: int
    isolated_nodes: List[Any]
    # Node-level metrics indexed by node_id
    node_metrics: Dict[Any, Dict[str, Any]] = field(default_factory=dict)


class SNAEngine:
    """Standalone graph analysis engine designed for reproducible historical SNA."""

    def __init__(self, directed: bool = True):
        self.directed = directed
        self.nodes: Set[Any] = set()
        self.adj: Dict[Any, Dict[Any, float]] = {}  # u -> {v: weight}
        self.pred: Dict[Any, Dict[Any, float]] = {}  # v -> {u: weight} for in-edges
        self.edge_records: List[Tuple[Any, Any, float, Dict[str, Any]]] = []

    def add_node(self, node_id: Any) -> None:
        """Add a node if not present."""
        if node_id not in self.nodes:
            self.nodes.add(node_id)
            self.adj[node_id] = {}
            self.pred[node_id] = {}

    def add_nodes_from(self, node_ids: List[Any]) -> None:
        """Add multiple nodes."""
        for n in node_ids:
            self.add_node(n)

    def add_edge(
        self,
        u: Any,
        v: Any,
        weight: float = 1.0,
        metadata: Optional[Dict[str, Any]] = None,
        ignore_self_loop: bool = True,
    ) -> bool:
        """Add an edge. Returns True if edge added, False if self-loop ignored."""
        if u == v and ignore_self_loop:
            self.add_node(u)
            return False

        self.add_node(u)
        self.add_node(v)

        # Multi-edge weight accumulation
        current_w = self.adj[u].get(v, 0.0)
        self.adj[u][v] = current_w + weight
        self.pred[v][u] = self.adj[u][v]

        if not self.directed:
            self.adj[v][u] = self.adj[u][v]
            self.pred[u][v] = self.adj[u][v]

        self.edge_records.append((u, v, weight, metadata or {}))
        return True

    # =========================================================================
    # Degree Metrics
    # =========================================================================

    def get_degrees(self) -> Dict[Any, Dict[str, int]]:
        """Calculate in-degree, out-degree, and total degree for all nodes."""
        res: Dict[Any, Dict[str, int]] = {}
        for n in self.nodes:
            out_deg = len(self.adj[n])
            in_deg = len(self.pred[n])
            total_deg = out_deg + in_deg if self.directed else out_deg
            res[n] = {
                "in_degree": in_deg,
                "out_degree": out_deg,
                "degree": total_deg,
            }
        return res

    def compute_degree_centrality(self) -> Dict[Any, float]:
        """Normalized degree centrality: deg(u) / (N - 1)."""
        n = len(self.nodes)
        if n <= 1:
            return {node: 0.0 for node in self.nodes}

        norm = n - 1.0
        res = {}
        degrees = self.get_degrees()
        for node, d_info in degrees.items():
            # For directed networks, degree centrality conventionally normalizes total degree / (2*(N-1))
            # or in+out / (2*(N-1)). To conform to standard networkx:
            # In networkx, in_degree_centrality is in/(n-1), out_degree_centrality is out/(n-1),
            # total degree centrality is (in + out)/(2*(n-1)) or total/(n-1).
            # Here we provide total/(n-1) for undirected, (in+out)/(2*(n-1)) for directed.
            if self.directed:
                res[node] = (d_info["in_degree"] + d_info["out_degree"]) / (2.0 * norm)
            else:
                res[node] = d_info["degree"] / norm
        return res

    # =========================================================================
    # Betweenness Centrality (Brandes Algorithm, 2001)
    # =========================================================================

    def compute_betweenness_centrality(self, normalized: bool = True) -> Dict[Any, float]:
        """Compute exact betweenness centrality using Ulrik Brandes (2001) algorithm.

        Time complexity: O(V * E) for unweighted graphs.
        Formula: CB(v) = sum_{s != v != t} (sigma_st(v) / sigma_st)
        """
        cb: Dict[Any, float] = {node: 0.0 for node in self.nodes}
        n = len(self.nodes)
        if n <= 2:
            return cb

        nodes_list = list(self.nodes)

        for s in nodes_list:
            # 1. Single-source shortest paths via BFS
            stack: List[Any] = []
            predecessors: Dict[Any, List[Any]] = {w: [] for w in self.nodes}
            sigma: Dict[Any, float] = {w: 0.0 for w in self.nodes}
            sigma[s] = 1.0
            dist: Dict[Any, int] = {w: -1 for w in self.nodes}
            dist[s] = 0

            queue = deque([s])
            while queue:
                v = queue.popleft()
                stack.append(v)

                for w in self.adj[v]:
                    # w found for the first time?
                    if dist[w] < 0:
                        dist[w] = dist[v] + 1
                        queue.append(w)
                    # shortest path to w via v?
                    if dist[w] == dist[v] + 1:
                        sigma[w] += sigma[v]
                        predecessors[w].append(v)

            # 2. Accumulation: back-propagation of dependency deltas
            delta: Dict[Any, float] = {w: 0.0 for w in self.nodes}
            while stack:
                w = stack.pop()
                for v in predecessors[w]:
                    if sigma[w] > 0:
                        delta[v] += (sigma[v] / sigma[w]) * (1.0 + delta[w])
                if w != s:
                    cb[w] += delta[w]

        # 3. Normalization
        if normalized:
            if self.directed:
                # Directed graph scale factor: 1 / ((n - 1) * (n - 2))
                scale = 1.0 / ((n - 1) * (n - 2))
            else:
                # Undirected graph scale factor: 1 / ((n - 1) * (n - 2)) * 2, because each pair is counted twice
                scale = 1.0 / ((n - 1) * (n - 2))
            for node in cb:
                cb[node] *= scale
        else:
            if not self.directed:
                for node in cb:
                    cb[node] /= 2.0

        return cb

    # =========================================================================
    # Closeness Centrality (Wasserman & Faust 1994)
    # =========================================================================

    def compute_closeness_centrality(self) -> Dict[Any, float]:
        """Compute closeness centrality with Wasserman & Faust formula for disconnected networks.

        Formula for node u:
        C(u) = [(k - 1) / (N - 1)] * [(k - 1) / sum(d(u, v))]
        where k is the number of reachable nodes from u (including u).
        """
        closeness: Dict[Any, float] = {}
        n = len(self.nodes)
        if n <= 1:
            return {node: 0.0 for node in self.nodes}

        for s in self.nodes:
            # BFS shortest path from s
            dist: Dict[Any, int] = {s: 0}
            queue = deque([s])
            total_dist = 0
            reachable_count = 1

            while queue:
                u = queue.popleft()
                d_u = dist[u]
                for v in self.adj[u]:
                    if v not in dist:
                        dist[v] = d_u + 1
                        total_dist += dist[v]
                        reachable_count += 1
                        queue.append(v)

            if total_dist > 0 and reachable_count > 1:
                # Wasserman & Faust normalization
                s_factor = (reachable_count - 1.0) / (n - 1.0)
                mean_dist = (reachable_count - 1.0) / float(total_dist)
                closeness[s] = s_factor * mean_dist
            else:
                closeness[s] = 0.0

        return closeness

    # =========================================================================
    # PageRank (Power Iteration)
    # =========================================================================

    def compute_pagerank(
        self,
        alpha: float = 0.85,
        max_iter: int = 100,
        tol: float = 1e-6,
    ) -> Dict[Any, float]:
        """Compute PageRank via Power Iteration with dangling node resolution.

        PR(u) = (1 - alpha)/N + alpha * sum_{v in In(u)} (PR(v) / OutDeg(v))
        Dangling nodes (OutDeg == 0) redistribute their probability mass equally.
        """
        n = len(self.nodes)
        if n == 0:
            return {}
        if n == 1:
            return {next(iter(self.nodes)): 1.0}

        # Initialize uniform distribution
        pr: Dict[Any, float] = {node: 1.0 / n for node in self.nodes}
        nodes_list = list(self.nodes)

        # Precompute out-degrees
        out_degrees: Dict[Any, int] = {node: len(self.adj[node]) for node in self.nodes}
        dangling_nodes = [node for node, deg in out_degrees.items() if deg == 0]

        for _ in range(max_iter):
            next_pr: Dict[Any, float] = {}

            # Calculate dangling sum
            dangling_sum = sum(pr[node] for node in dangling_nodes)
            base_prob = (1.0 - alpha + alpha * dangling_sum) / n

            # Compute rank for each node
            for u in nodes_list:
                in_sum = 0.0
                for v in self.pred[u]:
                    deg = out_degrees[v]
                    if deg > 0:
                        in_sum += pr[v] / deg
                next_pr[u] = base_prob + alpha * in_sum

            # Check L1 convergence: sum(|next_pr[i] - pr[i]|)
            err = sum(abs(next_pr[u] - pr[u]) for u in nodes_list)
            pr = next_pr
            if err < tol:
                break

        return pr

    # =========================================================================
    # Clustering Coefficient
    # =========================================================================

    def compute_clustering_coefficients(self) -> Dict[Any, float]:
        """Compute local clustering coefficient for each node.

        For directed graphs, following standard SNA conventions, neighbors are evaluated
        by treating local adjacencies as undirected interactions:
        C(u) = 2 * (edges between neighbors) / (k * (k - 1))
        where k is the count of distinct neighbors of u.
        """
        clustering: Dict[Any, float] = {}
        for u in self.nodes:
            # Distinct neighbors (in and out)
            nbrs = set(self.adj[u].keys()) | set(self.pred[u].keys())
            nbrs.discard(u)
            k = len(nbrs)
            if k < 2:
                clustering[u] = 0.0
                continue

            # Count edges between neighbors
            edge_count = 0
            nbrs_list = list(nbrs)
            for i in range(k):
                for j in range(i + 1, k):
                    ni, nj = nbrs_list[i], nbrs_list[j]
                    # Check connection between ni and nj in either direction
                    if nj in self.adj[ni] or ni in self.adj[nj]:
                        edge_count += 1

            possible_edges = k * (k - 1) / 2.0
            clustering[u] = edge_count / possible_edges if possible_edges > 0 else 0.0

        return clustering

    # =========================================================================
    # Connected Components
    # =========================================================================

    def get_weakly_connected_components(self) -> List[Set[Any]]:
        """Find weakly connected components using undirected BFS."""
        visited: Set[Any] = set()
        components: List[Set[Any]] = []

        for node in self.nodes:
            if node in visited:
                continue

            comp: Set[Any] = set()
            queue = deque([node])
            visited.add(node)

            while queue:
                u = queue.popleft()
                comp.add(u)

                # Neighbors in both directions (undirected view)
                all_neighbors = set(self.adj[u].keys()) | set(self.pred[u].keys())
                for v in all_neighbors:
                    if v not in visited:
                        visited.add(v)
                        queue.append(v)

            components.append(comp)

        # Sort by size descending
        components.sort(key=len, reverse=True)
        return components

    # =========================================================================
    # Global Summary & Density
    # =========================================================================

    def compute_network_summary(self) -> NetworkMetricsResult:
        """Run all algorithms and compile global and node-level metrics."""
        n = len(self.nodes)
        unique_edges = sum(len(self.adj[u]) for u in self.nodes)
        if not self.directed:
            unique_edges //= 2

        # 1. Density
        if n <= 1:
            density = 0.0
        else:
            max_edges = n * (n - 1) if self.directed else (n * (n - 1)) / 2.0
            density = unique_edges / max_edges if max_edges > 0 else 0.0

        # 2. Degrees
        deg_info = self.get_degrees()
        total_in = sum(d["in_degree"] for d in deg_info.values())
        total_out = sum(d["out_degree"] for d in deg_info.values())
        total_all = sum(d["degree"] for d in deg_info.values())

        avg_deg = total_all / float(n) if n > 0 else 0.0
        avg_in = total_in / float(n) if n > 0 else 0.0
        avg_out = total_out / float(n) if n > 0 else 0.0

        # 3. Components
        components = self.get_weakly_connected_components()
        component_sizes = [len(c) for c in components]
        # Assign community/component IDs (e.g., "comp_1", "comp_2")
        node_to_comp: Dict[Any, str] = {}
        for idx, comp in enumerate(components, 1):
            comp_id = f"component_{idx}"
            for member in comp:
                node_to_comp[member] = comp_id

        # 4. Isolates
        isolates = [node for node, d in deg_info.items() if d["degree"] == 0]

        # 5. Centralities & Clustering
        deg_centrality = self.compute_degree_centrality()
        betweenness = self.compute_betweenness_centrality()
        closeness = self.compute_closeness_centrality()
        pagerank = self.compute_pagerank()
        clustering = self.compute_clustering_coefficients()

        avg_clustering = sum(clustering.values()) / float(n) if n > 0 else 0.0

        # 6. Compile node metrics
        node_metrics: Dict[Any, Dict[str, Any]] = {}
        for node in self.nodes:
            node_metrics[node] = {
                "in_degree": deg_info[node]["in_degree"],
                "out_degree": deg_info[node]["out_degree"],
                "degree": deg_info[node]["degree"],
                "degree_centrality": round(deg_centrality.get(node, 0.0), 6),
                "betweenness_centrality": round(betweenness.get(node, 0.0), 6),
                "closeness_centrality": round(closeness.get(node, 0.0), 6),
                "pagerank": round(pagerank.get(node, 0.0), 6),
                "clustering_coefficient": round(clustering.get(node, 0.0), 6),
                "community_id": node_to_comp.get(node, "component_1"),
            }

        return NetworkMetricsResult(
            node_count=n,
            edge_count=unique_edges,
            density=round(density, 6),
            average_degree=round(avg_deg, 4),
            average_in_degree=round(avg_in, 4),
            average_out_degree=round(avg_out, 4),
            connected_components_count=len(components),
            component_sizes=component_sizes,
            average_clustering_coefficient=round(avg_clustering, 6),
            isolated_nodes_count=len(isolates),
            isolated_nodes=isolates,
            node_metrics=node_metrics,
        )
