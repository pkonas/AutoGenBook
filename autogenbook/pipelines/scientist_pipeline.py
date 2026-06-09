from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import networkx as nx
from pylatex import Command, Document, Package, Section, Subsection, Subsubsection
from pylatex.section import Paragraph, Subparagraph
from pylatex.utils import NoEscape, escape_latex

from autogenbook.agents import (
    AgentContext,
    AnalyzeAgent,
    CodePatchAgent,
    IdeaAgent,
    LiteratureAgent,
    PlanAgent,
    PaperSectionWriterAgent,
    ReviewAgent,
    RevisionAgent,
)
from autogenbook.citations.extract import extract_citations
from autogenbook.citations.ledger import CitationLedger
from autogenbook.citations.kb_footnotes import apply_kb_footnotes
from autogenbook.audit.latex_auditor import AuditorConfig, audit_latex
from autogenbook.audit.types import AuditSeverity
from autogenbook.length_control import enforce_section_length
from autogenbook.llm_usage import write_run_meta
from autogenbook.graph.doc_graph import attach_content_path, leaf_nodes_in_order, save_graph_json, sort_node_keys
from autogenbook.retrieval.manager import RetrievalManager
from autogenbook.retrieval.mcp_papers import MCPPaperRetriever
from autogenbook.retrieval.tavily import TavilyRetriever
from autogenbook.retrieval.types import RetrievalItem
from autogenbook.runner.patch_apply import apply_unified_diff
from autogenbook.prompts.scientist_loader import load_scientist_prompts
from autogenbook.prompts.registry import set_prompt_registry
from openrouter_llm import OpenRouterLLM
from rag_kb import KnowledgeBase
from utils import (
    build_retrieval_query_from_tex,
    generate_outline_text,
    get_depth,
    safe_filename,
    ensure_robustness_preamble,
)

from ..state import RunContext


TEMPLATE_NAME = "toy_classification"
DEFAULT_TIMEOUT_SEC = 120
DEFAULT_REVIEW_ROUNDS = 1
DEFAULT_MAX_ITERS = 1
DEFAULT_METRIC = "accuracy"
DEFAULT_MIN_IMPROVEMENT = 0.005


def _ask_text(prompt: str, default: Optional[str] = None, required: bool = False) -> str:
    suffix = f" (default {default})" if default else ""
    while True:
        ans = input(f"{prompt}{suffix}: ").strip()
        if ans:
            return ans
        if default:
            return default
        if not required:
            return ""
        print("Zadejte prosim hodnotu.")


def _build_run_item(run_id: str, name: str, text: str, loc: str) -> RetrievalItem:
    rid = f"RID:run:{run_id}:{name}"
    cite_key = f"run_{run_id}_{name.replace('.', '_')}"
    return RetrievalItem(
        rid=rid,
        kind="run",
        source="Run Artifact",
        loc=loc,
        score=1.0,
        cite_key=cite_key,
        text=text,
        title=name,
    )


def _build_run_items(run_id: str, metrics: Dict[str, Any], stdout: str, stderr: str) -> List[RetrievalItem]:
    items: List[RetrievalItem] = []
    items.append(_build_run_item(run_id, "metrics.json", json.dumps(metrics, indent=2), "metrics"))
    if stdout:
        items.append(_build_run_item(run_id, "stdout.log", stdout[-2000:], "stdout"))
    if stderr:
        items.append(_build_run_item(run_id, "stderr.log", stderr[-2000:], "stderr"))
    return items


def _parse_year(value: Any) -> Optional[int]:
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        cleaned = value.strip()
        if cleaned.isdigit():
            return int(cleaned)
    return None


def _dedupe_items(items: List[RetrievalItem]) -> List[RetrievalItem]:
    seen = set()
    deduped: List[RetrievalItem] = []
    for item in items:
        key = item.cite_key or item.rid
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _item_keys(items: List[RetrievalItem]) -> set[str]:
    keys: set[str] = set()
    for item in items:
        key = item.cite_key or item.rid
        if key:
            keys.add(str(key))
    return keys


def _literature_items(lit_out: Dict[str, Any]) -> List[RetrievalItem]:
    items: List[RetrievalItem] = []
    if not isinstance(lit_out, dict):
        return items
    for entry in lit_out.get("related_work", []):
        if not isinstance(entry, dict):
            continue
        cite_key = str(entry.get("cite_key", "")).strip()
        if not cite_key:
            continue
        title = str(entry.get("title", "")).strip() or "Untitled"
        year = _parse_year(entry.get("year"))
        key_takeaways = entry.get("key_takeaways", [])
        if isinstance(key_takeaways, list):
            text = "; ".join([str(t) for t in key_takeaways if t])
        else:
            text = ""
        rid = f"RID:web:literature:{cite_key}"
        items.append(
            RetrievalItem(
                rid=rid,
                kind="web",
                source="Literature Agent",
                loc=str(year) if year is not None else "literature",
                score=1.0,
                cite_key=cite_key,
                text=text,
                url=None,
                title=title,
                authors=[],
                year=year,
                venue=None,
                doi=None,
            )
        )
    return items


def _prepare_run_dir(template_dir: Path, out_dir: Path, run_id: str) -> Path:
    run_dir = out_dir / "experiments" / run_id
    if run_dir.exists():
        shutil.rmtree(run_dir)
    shutil.copytree(template_dir, run_dir)
    return run_dir


def _clone_run_dir(src_dir: Path, dest_dir: Path) -> Path:
    if dest_dir.exists():
        shutil.rmtree(dest_dir)
    shutil.copytree(src_dir, dest_dir)
    return dest_dir


def _run_experiment(run_dir: Path, timeout_sec: int) -> Tuple[str, str, Dict[str, Any]]:
    cmd = [sys.executable, "run_experiment.py"]
    proc = subprocess.run(
        cmd,
        cwd=str(run_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_sec,
    )

    metrics_path = run_dir / "metrics.json"
    metrics = {}
    if metrics_path.exists():
        try:
            metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        except Exception:
            metrics = {}

    stdout = proc.stdout or ""
    stderr = proc.stderr or ""
    (run_dir / "stdout.log").write_text(stdout, encoding="utf-8")
    (run_dir / "stderr.log").write_text(stderr, encoding="utf-8")
    return stdout, stderr, metrics


def _run_artifact_paths(run_id: str, run_dir: Path) -> Dict[str, Path]:
    mapping: Dict[str, Path] = {}
    metrics = run_dir / "metrics.json"
    if metrics.exists():
        mapping[f"RID:run:{run_id}:metrics.json"] = metrics
    stdout = run_dir / "stdout.log"
    if stdout.exists():
        mapping[f"RID:run:{run_id}:stdout.log"] = stdout
    stderr = run_dir / "stderr.log"
    if stderr.exists():
        mapping[f"RID:run:{run_id}:stderr.log"] = stderr
    return mapping


def _metric_value(metrics: Dict[str, Any], metric: str) -> Optional[float]:
    if not isinstance(metrics, dict):
        return None
    if metric in metrics:
        try:
            return float(metrics[metric])
        except Exception:
            return None
    lower = metric.lower()
    if lower in metrics:
        try:
            return float(metrics[lower])
        except Exception:
            return None
    return None


def _build_code_index(run_dir: Path) -> Dict[str, Any]:
    files: List[Dict[str, Any]] = []
    for path in sorted(run_dir.rglob("*.py")):
        rel = path.relative_to(run_dir).as_posix()
        text = path.read_text(encoding="utf-8", errors="ignore")
        size = len(text.encode("utf-8", errors="ignore"))
        functions = re.findall(r"^def\s+([A-Za-z_][A-Za-z0-9_]*)", text, re.MULTILINE)
        summary_lines = [line.strip() for line in text.splitlines() if line.strip()][:5]
        summary = " ".join(summary_lines)
        files.append({"path": rel, "summary": summary, "functions": functions, "size": size})
    return {
        "files": files,
        "entrypoint": "run_experiment.py",
        "constraints": {
            "forbidden_imports": ["requests", "urllib", "socket", "subprocess", "os.system"],
            "allowed_libraries": ["scikit-learn", "numpy", "matplotlib"],
        },
    }


def _summarize_results(metrics: Dict[str, Any], metric: str) -> Dict[str, Any]:
    return {
        "metric": metric,
        "value": _metric_value(metrics, metric),
        "raw_metrics": metrics,
    }


def _build_graph_from_outline(outline: Dict[str, Any]) -> nx.DiGraph:
    g = nx.DiGraph()
    root = outline.get("root", "paper") if isinstance(outline, dict) else "paper"
    nodes = outline.get("nodes", []) if isinstance(outline, dict) else []
    edges = outline.get("edges", []) if isinstance(outline, dict) else []

    g.add_node(root, title="Paper", summary="", n_pages=1.0, needsSubdivision=False)
    for node in nodes:
        if not isinstance(node, dict):
            continue
        key = str(node.get("key", "")).strip()
        if not key:
            continue
        g.add_node(
            key,
            title=str(node.get("title", "")).strip(),
            summary=str(node.get("summary", "")).strip(),
            n_pages=float(node.get("n_pages", 1.0)),
            needsSubdivision=False,
        )
    for edge in edges:
        if not isinstance(edge, (list, tuple)) or len(edge) != 2:
            continue
        g.add_edge(str(edge[0]), str(edge[1]))
    if root not in g:
        g.add_node(root, title="Paper", summary="", n_pages=1.0, needsSubdivision=False)
    return g


def _node_children_sorted(g: nx.DiGraph, node: str) -> List[str]:
    return sort_node_keys(list(g.successors(node)))


def _build_paper_latex(g: nx.DiGraph, out_dir: Path, citation_style: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)

    geometry_options = {"a4paper": True, "margin": "25mm"}
    doc = Document(documentclass="article", geometry_options=geometry_options)
    doc.packages.append(Package("amsmath"))
    doc.packages.append(Package("amssymb"))
    doc.packages.append(Package("amsfonts"))
    doc.packages.append(Package("mathtools"))
    doc.packages.append(Package("bm"))
    doc.packages.append(Package("graphicx"))
    doc.packages.append(Package("microtype"))
    doc.packages.append(Package("iftex"))
    doc.preamble.append(
        NoEscape(
            r"\ifLuaTeX"
            r"\usepackage{fontspec}"
            r"\usepackage{unicode-math}"
            r"\else"
            r"\usepackage[utf8]{inputenc}"
            r"\usepackage[T1]{fontenc}"
            r"\fi"
            r"\usepackage[czech]{babel}"
        )
    )
    doc.preamble.append(NoEscape("\\shorthandoff{\"}"))
    doc.packages.append(Package("textcomp"))
    doc.packages.append(Package("listings"))
    doc.packages.append(Package("listingsutf8"))
    doc.packages.append(Package("xcolor"))
    doc.packages.append(Package("booktabs"))
    doc.packages.append(Package("underscore", options="strings"))
    doc.packages.append(Package("url"))
    doc.packages.append(Package("xurl"))
    doc.packages.append(Package("hyperref"))
    doc.packages.append(Package("bookmark"))
    doc.packages.append(Package("natbib"))
    doc.preamble.append(NoEscape(r"\graphicspath{{figures/}}"))
    ensure_robustness_preamble(out_dir)
    doc.preamble.append(NoEscape(r"\input{robustness.tex}"))

    def _sanitize_heading_text(text: str) -> NoEscape:
        cleaned = text.replace("\r", " ").replace("\n", " ").strip()
        return NoEscape(escape_latex(cleaned))

    title = g.graph.get("title", "Paper")
    doc.preamble.append(Command("title", _sanitize_heading_text(title)))
    author = str(g.graph.get("author", "") or "").strip() or "AutoGenBook"
    doc.preamble.append(Command("author", _sanitize_heading_text(author)))
    doc.preamble.append(Command("date", NoEscape(r"\today")))
    doc.append(NoEscape(r"\hypersetup{pageanchor=false}"))
    doc.append(NoEscape(r"\maketitle"))
    doc.append(NoEscape(r"\pagenumbering{arabic}"))
    doc.append(NoEscape(r"\hypersetup{pageanchor=true}"))

    abstract = g.graph.get("abstract", "")
    if abstract:
        doc.append(NoEscape(r"\begin{abstract}"))
        doc.append(NoEscape(escape_latex(abstract)))
        doc.append(NoEscape(r"\end{abstract}"))

    keywords = g.graph.get("keywords", [])
    if keywords:
        kw = ", ".join(keywords) if isinstance(keywords, list) else str(keywords)
        doc.append(NoEscape(r"\noindent\textbf{Keywords:} " + escape_latex(kw)))

    def heading_class_for_depth(d: int):
        if d == 1:
            return Section
        if d == 2:
            return Subsection
        if d == 3:
            return Subsubsection
        if d == 4:
            return Paragraph
        return Subparagraph

    def add_node(parent_container, node_key: str) -> None:
        depth = get_depth(node_key)
        node = g.nodes[node_key]
        title = node.get("title", "")
        Heading = heading_class_for_depth(depth)

        with parent_container.create(Heading(_sanitize_heading_text(title), label=False)):
            children = _node_children_sorted(g, node_key)
            if children:
                for ch in children:
                    add_node(parent_container, ch)
            else:
                path = node.get("content_file_path")
                if path:
                    from book_builder import _sanitize_section_tex

                    tex = Path(path).read_text(encoding="utf-8", errors="ignore")
                    tex, _ = _sanitize_section_tex(tex)
                    parent_container.append(NoEscape("\n\n"))
                    parent_container.append(NoEscape(tex))
                    parent_container.append(NoEscape("\n\n"))

    for ch in _node_children_sorted(g, "paper"):
        add_node(doc, ch)

    if citation_style == "bibtex":
        doc.append(NoEscape(r"\bibliographystyle{plain}"))
        doc.append(NoEscape(r"\bibliography{refs}"))

    tex_filename = safe_filename(title) + ".tex"
    tex_path = out_dir / tex_filename
    doc.generate_tex(filepath=str(tex_path.with_suffix("")))
    return tex_path


def run_scientist(args: Any, run_ctx: Optional[RunContext], logger: Any) -> int:
    started_at = datetime.now(timezone.utc)
    status = "ok"
    error: Optional[str] = None
    llm: Optional[OpenRouterLLM] = None

    try:
        out_dir = Path(args.out_dir).expanduser().resolve()
        if run_ctx is None:
            run_ctx = RunContext.create(out_dir=out_dir, mode="scientist")

        out_dir.mkdir(parents=True, exist_ok=True)

        prompts = load_scientist_prompts()
        set_prompt_registry("scientist", prompts)

        kb = None
        if args.kb_dir:
            kb_dir = Path(args.kb_dir).expanduser().resolve()
            kb = KnowledgeBase.build_from_directory(
                kb_dir,
                cache_dir=out_dir / ".kb_cache",
                force_rebuild=bool(args.rebuild_kb),
            )
        tavily = None
        mcp_papers = None
        if getattr(args, "enable_web_rag", False):
            mcp_papers = MCPPaperRetriever()
            api_key = os.environ.get("TAVILY_API_KEY")
            tavily = TavilyRetriever(api_key=api_key)
        retrieval_manager = RetrievalManager(
            local_kb=kb,
            mcp_papers=mcp_papers,
            tavily=tavily,
            enable_web=bool(getattr(args, "enable_web_rag", False)),
            default_k=int(getattr(args, "web_rag_k", 5)),
        )
        if getattr(args, "enable_web_rag", False):
            has_mcp = bool(mcp_papers and mcp_papers.is_available())
            has_tavily = bool(tavily and tavily.api_key)
            if not has_mcp and not has_tavily:
                print(
                    "[WARN] Web RAG enabled but no MCP paper tools or Tavily API key available; using KB only."
                )
            elif not has_mcp and has_tavily:
                print("[INFO] MCP paper tools unavailable; falling back to Tavily search.")

        llm = OpenRouterLLM()
        agent_ctx = AgentContext(
            run_id=run_ctx.run_id,
            out_dir=out_dir,
            kb=kb,
            retrieval_manager=retrieval_manager,
            logger=logger,
            llm=llm,
            mode="scientist",
            fail_fast_schema=bool(getattr(args, "fail_fast_schema", False)),
        )

        idea_agent = IdeaAgent()
        literature_agent = LiteratureAgent()
        plan_agent = PlanAgent()
        analyze_agent = AnalyzeAgent()
        review_agent = ReviewAgent()
        revision_agent = RevisionAgent()
        patch_agent = CodePatchAgent()
        writer = PaperSectionWriterAgent(llm)

        max_iters = int(getattr(args, "max_iters", DEFAULT_MAX_ITERS) or DEFAULT_MAX_ITERS)
        metric_to_optimize = str(getattr(args, "metric_to_optimize", DEFAULT_METRIC) or DEFAULT_METRIC)
        min_improvement = float(getattr(args, "min_improvement", DEFAULT_MIN_IMPROVEMENT) or DEFAULT_MIN_IMPROVEMENT)

        idea_input = {
            "n_ideas": 3,
            "template_name": TEMPLATE_NAME,
            "user_goal": "Explore a small, testable idea for a toy classification experiment.",
            "compute_budget": "CPU-only, <5 minutes per run",
            "max_iters": max_iters,
            "allowed_libraries": ["scikit-learn", "numpy", "matplotlib"],
            "forbidden_actions": ["network", "filesystem outside workdir"],
            "metric_to_optimize": metric_to_optimize,
            "baseline_desc": "Two classical baselines on Iris dataset",
        }
        idea_out = idea_agent.run(idea_input, agent_ctx)

        ideas = idea_out.get("ideas", []) if isinstance(idea_out, dict) else []
        selected_idea = ideas[0] if ideas else {"title": "Toy classification baselines"}
        literature_input = {
            "selected_idea_json": json.dumps(selected_idea, ensure_ascii=False, indent=2),
            "query": f"{selected_idea.get('title','')} toy classification baselines",
        }
        lit_out = literature_agent.run(literature_input, agent_ctx)
        lit_items = _literature_items(lit_out)

        plan_input = {
            "ideas_json": json.dumps(idea_out, ensure_ascii=False, indent=2),
            "literature_json": json.dumps(lit_out, ensure_ascii=False, indent=2),
            "compute_budget": "CPU-only, <5 minutes per run",
            "max_iters": max_iters,
            "metric_to_optimize": metric_to_optimize,
            "template_name": TEMPLATE_NAME,
        }
        plan_out = plan_agent.run(plan_input, agent_ctx)

        template_dir = Path(__file__).resolve().parent.parent / "templates" / TEMPLATE_NAME
        run_dir = _prepare_run_dir(template_dir, out_dir, run_ctx.run_id)
        stdout, stderr, metrics = _run_experiment(run_dir, timeout_sec=DEFAULT_TIMEOUT_SEC)
        current_run_id = run_ctx.run_id
        current_run_dir = run_dir
        current_stdout = stdout
        current_stderr = stderr
        current_metrics = metrics
        run_items = _build_run_items(current_run_id, metrics, stdout, stderr)

        analyze_input = {
            "metrics_json": json.dumps(metrics, ensure_ascii=False, indent=2),
            "history_csv_head": "(none)",
            "stdout_tail": stdout[-2000:] if stdout else "(none)",
            "stderr_tail": stderr[-2000:] if stderr else "(none)",
            "produced_files_list": [str(p.relative_to(run_dir)) for p in run_dir.rglob("*") if p.is_file()],
            "plan_json": json.dumps(plan_out, ensure_ascii=False, indent=2),
            "prior_iteration_summary": "(none)",
            "metric_to_optimize": metric_to_optimize,
            "retrieved_context": retrieval_manager.format_context(run_items),
        }
        analyze_out = analyze_agent.run(analyze_input, agent_ctx)

        iterations_dir = out_dir / "iterations"
        iterations_dir.mkdir(parents=True, exist_ok=True)
        for iter_idx in range(1, max_iters + 1):
            iter_id = f"{run_ctx.run_id}-iter{iter_idx}"
            iter_dir = iterations_dir / f"iter_{iter_idx:02d}"
            iter_dir.mkdir(parents=True, exist_ok=True)

            code_index = _build_code_index(current_run_dir)
            patch_input = {
                "iter_index": iter_idx,
                "metric_to_optimize": metric_to_optimize,
                "plan_json": json.dumps(plan_out, ensure_ascii=False, indent=2),
                "results_summary": json.dumps(
                    _summarize_results(current_metrics, metric_to_optimize),
                    ensure_ascii=False,
                    indent=2,
                ),
                "code_index": json.dumps(code_index, ensure_ascii=False, indent=2),
                "entrypoint": code_index.get("entrypoint", "run_experiment.py"),
                "review_required_changes": "(none)",
            }
            patch_out = patch_agent.run(patch_input, agent_ctx)
            (iter_dir / "patch.json").write_text(
                json.dumps(patch_out, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            diff_text = str(patch_out.get("diff", "")).strip()
            (iter_dir / "diff.patch").write_text(diff_text, encoding="utf-8")

            if not diff_text:
                (iter_dir / "decision.json").write_text(
                    json.dumps({"status": "stop", "reason": "empty diff"}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                break

            iter_run_dir = _clone_run_dir(
                current_run_dir, out_dir / "experiments" / iter_id
            )
            try:
                apply_result = apply_unified_diff(diff_text, iter_run_dir)
                (iter_dir / "apply.json").write_text(
                    json.dumps(
                        {
                            "changed_files": [str(p.relative_to(iter_run_dir)) for p in apply_result.changed_files],
                            "backup_dir": str(apply_result.backup_dir),
                        },
                        ensure_ascii=False,
                        indent=2,
                    ),
                    encoding="utf-8",
                )
            except Exception as exc:
                (iter_dir / "decision.json").write_text(
                    json.dumps({"status": "rejected", "reason": str(exc)}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
                break

            iter_stdout, iter_stderr, iter_metrics = _run_experiment(
                iter_run_dir, timeout_sec=DEFAULT_TIMEOUT_SEC
            )
            (iter_dir / "stdout_tail.txt").write_text(iter_stdout[-2000:], encoding="utf-8")
            (iter_dir / "stderr_tail.txt").write_text(iter_stderr[-2000:], encoding="utf-8")
            (iter_dir / "metrics.json").write_text(
                json.dumps(iter_metrics, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            iter_items = _build_run_items(iter_id, iter_metrics, iter_stdout, iter_stderr)
            iter_analyze_input = {
                "metrics_json": json.dumps(iter_metrics, ensure_ascii=False, indent=2),
                "history_csv_head": "(none)",
                "stdout_tail": iter_stdout[-2000:] if iter_stdout else "(none)",
                "stderr_tail": iter_stderr[-2000:] if iter_stderr else "(none)",
                "produced_files_list": [
                    str(p.relative_to(iter_run_dir)) for p in iter_run_dir.rglob("*") if p.is_file()
                ],
                "plan_json": json.dumps(plan_out, ensure_ascii=False, indent=2),
                "prior_iteration_summary": json.dumps(
                    _summarize_results(current_metrics, metric_to_optimize),
                    ensure_ascii=False,
                    indent=2,
                ),
                "metric_to_optimize": metric_to_optimize,
                "retrieved_context": retrieval_manager.format_context(iter_items),
            }
            iter_analyze_out = analyze_agent.run(iter_analyze_input, agent_ctx)
            (iter_dir / "analysis.json").write_text(
                json.dumps(iter_analyze_out, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            baseline_value = _metric_value(current_metrics, metric_to_optimize)
            new_value = _metric_value(iter_metrics, metric_to_optimize)
            decision = {
                "status": "rejected",
                "baseline_value": baseline_value,
                "new_value": new_value,
                "min_improvement": min_improvement,
            }
            if baseline_value is not None and new_value is not None:
                improvement = new_value - baseline_value
                decision["improvement"] = improvement
                if improvement >= min_improvement:
                    decision["status"] = "accepted"
            else:
                decision["reason"] = "missing metric"

            (iter_dir / "decision.json").write_text(
                json.dumps(decision, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

            if decision["status"] == "accepted":
                current_run_id = iter_id
                current_run_dir = iter_run_dir
                current_stdout = iter_stdout
                current_stderr = iter_stderr
                current_metrics = iter_metrics
                run_items = iter_items
                analyze_out = iter_analyze_out
            else:
                continue

        outline = plan_out.get("paper_outline_graph", {}) if isinstance(plan_out, dict) else {}
        g = _build_graph_from_outline(outline)
        g.graph["title"] = plan_out.get("title", "Scientist Paper") if isinstance(plan_out, dict) else "Scientist Paper"
        g.graph["abstract"] = ""
        g.graph["keywords"] = []
        g.graph["citation_style"] = "bibtex"
        author = str(g.graph.get("author", "") or "").strip()
        if not author:
            author = _ask_text("Zadejte autora clanku", required=True)
            g.graph["author"] = author
        save_graph_json(g, run_ctx.structure_graph_path)

        sections_dir = out_dir / "sections"
        sections_dir.mkdir(parents=True, exist_ok=True)

        citation_items: Dict[str, RetrievalItem] = {}
        rid_items: Dict[str, RetrievalItem] = {}
        for item in lit_items + run_items:
            if item.cite_key and item.cite_key not in citation_items:
                citation_items[item.cite_key] = item
            if item.rid and item.rid not in rid_items:
                rid_items[item.rid] = item

        for node_key in leaf_nodes_in_order(g):
            node = g.nodes[node_key]
            query = f"{g.graph.get('title','')}\n{node.get('title','')}\n{node.get('summary','')}"
            retrieved_items = retrieval_manager.retrieve(
                query, k=retrieval_manager.default_k, allow_web=False
            )
            context_items = _dedupe_items(retrieved_items + run_items + lit_items)
            retrieved_context = retrieval_manager.format_context(context_items)

            prompt_input = {
                "paper_title": g.graph.get("title", ""),
                "paper_venue": "arXiv",
                "paper_abstract": g.graph.get("abstract", ""),
                "paper_keywords": g.graph.get("keywords", []),
                "paper_outline_text": generate_outline_text(
                    {
                        n: {
                            "title": g.nodes[n].get("title", ""),
                            "summary": g.nodes[n].get("summary", ""),
                            "n_pages": g.nodes[n].get("n_pages", ""),
                            "children": _node_children_sorted(g, n),
                        }
                        for n in g.nodes
                    },
                    root="paper",
                ),
                "node_key": node_key,
                "section_title": node.get("title", ""),
                "section_summary": node.get("summary", ""),
                "n_pages": node.get("n_pages", 1.0),
                "plan_json": json.dumps(plan_out, ensure_ascii=False, indent=2),
                "literature_json": json.dumps(lit_out, ensure_ascii=False, indent=2),
                "analysis_json": json.dumps(analyze_out, ensure_ascii=False, indent=2),
                "figures_manifest": json.dumps(["figures/accuracy.png"], ensure_ascii=False),
                "tables_manifest": json.dumps([], ensure_ascii=False),
                "retrieved_context": retrieved_context or "(none)",
                "section_draft": "",
            }
            tex = writer.run(prompt_input, agent_ctx)
            if retrieval_manager.enable_web:
                draft_query = build_retrieval_query_from_tex(tex, max_chars=1200)
                if draft_query:
                    refined_query = f"{node.get('title','')}\n{draft_query}"
                    refined_items = retrieval_manager.retrieve(
                        refined_query, k=retrieval_manager.default_k, allow_web=True
                    )
                    merged_items = _dedupe_items(context_items + refined_items)
                    if _item_keys(merged_items) != _item_keys(context_items):
                        context_items = merged_items
                        prompt_input["retrieved_context"] = (
                            retrieval_manager.format_context(context_items) or "(none)"
                        )
                        prompt_input["section_draft"] = tex
                        tex = writer.run(prompt_input, agent_ctx)

            for item in context_items:
                if item.cite_key and item.cite_key not in citation_items:
                    citation_items[item.cite_key] = item
                if item.rid and item.rid not in rid_items:
                    rid_items[item.rid] = item
            tex, _ = enforce_section_length(
                llm,
                tex,
                target_pages=float(node.get("n_pages", 1.0)),
                out_dir=out_dir,
                label="scientist_section",
            )
            from book_builder import _sanitize_section_tex
            tex, _ = _sanitize_section_tex(tex)

            section_path = sections_dir / f"{node_key}.tex"
            section_path.write_text(tex, encoding="utf-8")
            attach_content_path(g, node_key, section_path)
            save_graph_json(g, run_ctx.structure_graph_path)

        tex_path = _build_paper_latex(g, out_dir, citation_style="bibtex")
        latex = tex_path.read_text(encoding="utf-8", errors="ignore")
        cite_keys, rids = extract_citations(latex)
        ledger = CitationLedger()
        missing = []
        for key in sorted(cite_keys):
            item = citation_items.get(key)
            if not item:
                missing.append(key)
                continue
            ledger.add_from_retrieval_item(item)
        for rid in sorted(rids):
            item = rid_items.get(rid)
            if not item:
                continue
            ledger.add_from_retrieval_item(item)
        if missing:
            missing_msg = ", ".join(missing)
            print(
                "[WARN] Missing citations for keys: "
                + missing_msg
                + ". Writing placeholder BibTeX entries."
            )
            for key in missing:
                ledger.add_placeholder(key)
        ledger.write_bib(out_dir / "refs.bib")

        citation_style = "bibtex"
        kb_cite_map = {
            key: (item.source, item.loc)
            for key, item in citation_items.items()
            if item.kind == "kb"
        }
        kb_rid_map = {
            rid: (item.source, item.loc)
            for rid, item in rid_items.items()
            if item.kind == "kb"
        }
        if citation_style == "footnote":
            latex = apply_kb_footnotes(latex, kb_cite_map, kb_rid_map)
        if "\\bibliography" not in latex and (out_dir / "refs.bib").exists():
            latex = latex.replace(
                "\\end{document}",
                "\n\\bibliographystyle{plain}\n\\bibliography{refs}\n\\end{document}",
            )
        tex_path.write_text(latex, encoding="utf-8")

        review_input = {
            "paper_latex": latex,
            "plan_json": json.dumps(plan_out, ensure_ascii=False, indent=2),
            "analysis_json": json.dumps(analyze_out, ensure_ascii=False, indent=2),
            "literature_json": json.dumps(lit_out, ensure_ascii=False, indent=2),
            "retrieved_context": retrieval_manager.format_context(run_items) or "(none)",
        }
        review_out = review_agent.run(review_input, agent_ctx)
        (out_dir / "review.json").write_text(
            json.dumps(review_out, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        revised_tex_path = None
        revised_tex = None
        if DEFAULT_REVIEW_ROUNDS > 0:
            revision_input = {
                "paper_latex": latex,
                "review_json": json.dumps(review_out, ensure_ascii=False, indent=2),
                "audit_issues_json": "[]",
                "plan_json": json.dumps(plan_out, ensure_ascii=False, indent=2),
                "analysis_json": json.dumps(analyze_out, ensure_ascii=False, indent=2),
                "literature_json": json.dumps(lit_out, ensure_ascii=False, indent=2),
                "retrieved_context": retrieval_manager.format_context(run_items) or "(none)",
            }
            revised_tex = revision_agent.run(revision_input, agent_ctx)
            if citation_style == "footnote":
                revised_tex = apply_kb_footnotes(revised_tex, kb_cite_map, kb_rid_map)
            if "\\bibliography" not in revised_tex and (out_dir / "refs.bib").exists():
                revised_tex = revised_tex.replace(
                    "\\end{document}",
                    "\n\\bibliographystyle{plain}\n\\bibliography{refs}\n\\end{document}",
                )
            revised_tex_path = out_dir / "revised.tex"
            revised_tex_path.write_text(revised_tex, encoding="utf-8")

        audit_enabled = getattr(args, "audit", None)
        if audit_enabled is None:
            audit_enabled = True
        audit_mode = str(getattr(args, "audit_mode", "warn") or "warn")
        if "--audit-mode" not in sys.argv:
            audit_mode = "strict"
        if audit_mode == "off":
            audit_enabled = False
        audit_window = int(getattr(args, "audit_window_chars", 600) or 600)
        if audit_enabled:
            audit_text = revised_tex if revised_tex is not None else latex
            audit_file = revised_tex_path if revised_tex_path is not None else tex_path
            known_cite_keys = set(citation_items.keys())
            known_rids = set(rid_items.keys())
            run_artifacts = _run_artifact_paths(current_run_id, current_run_dir)
            report = audit_latex(
                tex_path=audit_file,
                tex_text=audit_text,
                doc_kind="scientist",
                known_cite_keys=known_cite_keys,
                known_rids=known_rids,
                project_root=Path.cwd(),
                config=AuditorConfig(
                    enabled=True,
                    mode=audit_mode,
                    evidence_window_chars=audit_window,
                ),
                run_artifact_paths=run_artifacts,
            )
            report.dump(out_dir / "audit_report.json")
            if audit_mode == "strict" and report.counts_by_severity.get(AuditSeverity.ERROR.value, 0) > 0:
                if DEFAULT_REVIEW_ROUNDS > 0:
                    revision_input = {
                        "paper_latex": audit_text,
                        "review_json": json.dumps(review_out, ensure_ascii=False, indent=2),
                        "audit_issues_json": json.dumps(report.to_json().get("issues", []), ensure_ascii=False, indent=2),
                        "plan_json": json.dumps(plan_out, ensure_ascii=False, indent=2),
                        "analysis_json": json.dumps(analyze_out, ensure_ascii=False, indent=2),
                        "literature_json": json.dumps(lit_out, ensure_ascii=False, indent=2),
                        "retrieved_context": retrieval_manager.format_context(run_items) or "(none)",
                    }
                    revised_tex = revision_agent.run(revision_input, agent_ctx)
                    revised_tex_path = out_dir / "revised.tex"
                    revised_tex_path.write_text(revised_tex, encoding="utf-8")
                    audit_text = revised_tex
                    report = audit_latex(
                        tex_path=revised_tex_path,
                        tex_text=audit_text,
                        doc_kind="scientist",
                        known_cite_keys=known_cite_keys,
                        known_rids=known_rids,
                        project_root=Path.cwd(),
                        config=AuditorConfig(
                            enabled=True,
                            mode=audit_mode,
                            evidence_window_chars=audit_window,
                        ),
                        run_artifact_paths=run_artifacts,
                    )
                    report.dump(out_dir / "audit_report.json")
                if report.counts_by_severity.get(AuditSeverity.ERROR.value, 0) > 0:
                    status = "error"
                    error = "Audit failed in strict mode. See audit_report.json for details."
                    return 4

        figures_dir = current_run_dir / "figures"
        if figures_dir.exists():
            target_figures = out_dir / "figures"
            target_figures.mkdir(parents=True, exist_ok=True)
            for src in figures_dir.glob("*"):
                if src.is_file():
                    shutil.copy2(src, target_figures / src.name)

        if not args.no_pdf:
            from book_builder import compile_pdf

            compile_pdf(tex_path)

        if llm is not None:
            print(f"[TOKENS] Celkem spotrebovano tokenu: {llm.get_total_tokens()}")
            total_cost = llm.get_total_cost_usd()
            if total_cost is not None:
                print(f"[COST] Celkova cena: ${total_cost:.6f}")
        return 0
    except Exception as exc:
        status = "error"
        error = str(exc)
        raise
    finally:
        finished_at = datetime.now(timezone.utc)
        if run_ctx is not None:
            models = {"primary": llm.config.model if llm is not None else None}
            token_totals = {"primary": llm.get_total_tokens() if llm is not None else 0}
            cost_totals = {"primary": llm.get_total_cost_usd() if llm is not None else None}
            write_run_meta(
                run_ctx=run_ctx,
                args=args,
                started_at=started_at,
                finished_at=finished_at,
                status=status,
                error=error,
                models=models,
                token_totals=token_totals,
                cost_totals_usd=cost_totals,
            )
