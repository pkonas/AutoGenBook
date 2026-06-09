from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import networkx as nx

from utils import sort_node_keys as _sort_node_keys


class DocKind(str, Enum):
    BOOK = "book"
    PAPER = "paper"


@dataclass
class DocGraph:
    graph: nx.DiGraph

    def __init__(self, graph: Optional[nx.DiGraph] = None) -> None:
        self.graph = graph or nx.DiGraph()

    def to_networkx(self) -> nx.DiGraph:
        return self.graph


def _get_graph(g: nx.DiGraph | DocGraph) -> nx.DiGraph:
    if isinstance(g, DocGraph):
        return g.graph
    return g


def sort_node_keys(keys: Iterable[str]) -> List[str]:
    return _sort_node_keys(list(keys))


def leaf_nodes_in_order(g: nx.DiGraph | DocGraph) -> List[str]:
    nxg = _get_graph(g)
    leaves = [n for n in nxg.nodes if n != "book" and nxg.out_degree(n) == 0]
    return sort_node_keys(leaves)


def attach_content_path(g: nx.DiGraph | DocGraph, node_key: str, path: Path) -> None:
    nxg = _get_graph(g)
    nxg.nodes[node_key]["content_file_path"] = str(path)


def save_graph_json(g: nx.DiGraph | DocGraph, out_path: Path) -> None:
    nxg = _get_graph(g)
    ordered_nodes: List[str] = []
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visited:
            return
        visited.add(node)
        ordered_nodes.append(node)
        for child in sort_node_keys(list(nxg.successors(node))):
            visit(child)

    roots = [n for n in nxg.nodes if nxg.in_degree(n) == 0]
    for root in sort_node_keys(roots):
        visit(root)
    for node in sort_node_keys(list(nxg.nodes)):
        visit(node)

    ordered_edges: List[Tuple[str, str]] = []
    edge_seen: set[Tuple[str, str]] = set()
    for parent in ordered_nodes:
        for child in sort_node_keys(list(nxg.successors(parent))):
            edge = (parent, child)
            if edge not in edge_seen:
                ordered_edges.append(edge)
                edge_seen.add(edge)
    for edge in nxg.edges:
        if edge not in edge_seen:
            ordered_edges.append(edge)
            edge_seen.add(edge)

    if out_path.exists():
        bak_path = out_path.with_suffix(out_path.suffix + ".bak")
        try:
            bak_path.write_bytes(out_path.read_bytes())
        except Exception:
            pass

    data = {
        "graph": dict(nxg.graph),
        "nodes": {n: dict(nxg.nodes[n]) for n in ordered_nodes},
        "edges": ordered_edges,
    }
    out_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def load_graph_json(path: Path) -> nx.DiGraph:
    data = json.loads(path.read_text(encoding="utf-8"))
    g = nx.DiGraph()
    g.graph.update(data.get("graph", {}))
    for n, attrs in data.get("nodes", {}).items():
        g.add_node(n, **attrs)
    for u, v in data.get("edges", []):
        g.add_edge(u, v)
    return g
