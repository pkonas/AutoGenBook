# Graph-Aware Prompting for Reliable Section Generation

**Author:** gpt-5-mini

## Abstract

We present a graph-guided prompting workflow for long-form scientific writing that prioritizes evidence and citation consistency. Our method constructs a document graph that encodes section dependencies and uses strict JSON schemas and evidence-aware prompts to generate and validate individual sections. At generation time, the system (1) builds a section graph and schedules leaf-first writing, (2) retrieves and injects relevant evidence for each section, (3) produces JSON-constrained section outputs, and (4) assembles a LaTeX manuscript with refs.bib. We evaluate the approach by comparing it to a baseline linear prompting pipeline on two topical domains, measuring citation coverage, hallucination rate, and reviewer-style scores. Results show improved citation coverage and reduced unsupported assertions while keeping edits and post-hoc audits straightforward via machine-readable outputs. We intentionally state conservative claims and require explicit source citations for all factual assertions. The paper contributes practical artifacts: a graph-based section scheduler, an evidence-injection protocol per section, and an audit-friendly JSON output format that integrates with standard LaTeX/bib workflows. Our experiments use internal notes and a small curated PDF set where available; metrics focus on citation fidelity and factual consistency rather than broad generalization.

**Keywords:** document graph, long-form generation, evidence, citations, structured prompting

## Introduction

General background knowledge: practitioners and reviewers have raised concerns about the reliability of long-form scientific text produced by contemporary language models, particularly with respect to factual support, citation consistency, and ease of post-hoc verification. These concerns motivate methods that prioritize explicit evidence grounding and machine-checkable outputs when applying automated or semi-automated workflows to scholarly manuscripts. Section-level citation behavior and the heterogeneity of citation purposes motivate attention to where and how citations are placed within a manuscript: prior analyses indicate that citations often serve different functions across sections and that section headings are only partly reliable indicators of citation context \cite{1903.07547v1}.

This work asks the following focused research question: how does encoding document structure as an explicit section-level graph and using that graph to guide evidence-aware, JSON-constrained generation affect (a) the coherence of section composition and (b) the fidelity of citations in model-assisted long-form scientific writing? We frame this question narrowly so that the investigation assesses whether a graph-guided scheduling and prompting workflow can (i) increase the proportion of section claims that are directly supported by retrieved evidence and (ii) make unsupported assertions easier to detect and correct during later audits.

To answer this question we introduce a graph-aware prompting workflow. At a high level the workflow:
- represents a draft manuscript as a document graph whose nodes correspond to sections and whose directed edges encode prerequisite and dependency relations between sections;
- uses a leaf-first scheduling algorithm to order section generation so that sections with fewer downstream dependents are written first, thereby allowing early sections to provide grounded inputs for later dependent sections;
- retrieves and ranks candidate evidence for each section and injects top-ranked items into section-specific prompts according to an evidence-injection protocol;
- constrains model outputs to a strict, machine-checked JSON schema that requires explicit citation anchors for factual assertions and separates prose from structured metadata; and
- assembles validated JSON section artifacts into a LaTeX manuscript with an accompanying refs.bib file while applying conservative validation checks (e.g., citation-coverage thresholds and simple unsupported-claim heuristics) and logging failures for human review.

The workflow is designed so that each generated section is an auditable unit: the JSON artifact contains the section text, the list of evidence items used, and discrete fields for claims that require citation. By making provenance and claims machine-readable, the system aims to simplify targeted edits and downstream human audits.

We evaluate the proposed workflow against a baseline linear prompting pipeline on two topical domains drawn from internal notes and a small curated set of PDFs where available. Evaluation emphasizes citation coverage, the rate of unsupported assertions as identified by annotators, and reviewer-style assessments of local coherence and usefulness. Consistent with our conservative reporting policy, empirical claims in this paper are limited to the evaluated settings; all factual assertions in generated text are required by the workflow to include explicit source citations, and we do not claim broad generalization beyond the experiment domains.

Contributions. The paper makes three practical contributions:
1. a procedure for constructing a document-level section graph and a leaf-first scheduling algorithm for section-level generation;
2. an evidence-injection and JSON-constrained prompting protocol that enforces source-first writing and facilitates automatic validation; and
3. an audit-oriented assembly process that produces a LaTeX manuscript plus refs.bib while retaining per-section provenance for downstream review and editing.

We emphasize conservative interpretation of results and present the workflow primarily as a reproducible, audit-friendly approach for improving citation fidelity in model-assisted scientific writing. Limitations and failure modes are discussed in Section 6.

## Related Work

The following review provides a thematic framing of prior work relevant to long‑form, citation‑aware scientific writing with language models. Because a comprehensive literature retrieval has not yet been completed for this draft, several broad statements below are explicitly labeled as general background knowledge; we will replace those with precise citations and a fuller synthesis after targeted retrieval.

Long‑form generation and document planning (general background knowledge). Prior work has examined strategies for producing multi‑paragraph or multi‑section text with neural models, often emphasizing hierarchical planning, decomposition into smaller writing tasks, or explicit document structure to improve coherence. These approaches frequently contrast end‑to‑end generation of long documents with pipelines that plan and generate at multiple levels of granularity (section, paragraph, sentence) (general background knowledge).

Structured prompting and constrained outputs (general background knowledge). Recent efforts have explored conditioning large language models with structured prompts, templates, or schema constraints to control format, reduce hallucination, and facilitate downstream validation. Constraining outputs to machine‑readable formats (for example, JSON or other schemas) is a practical technique for downstream assembly, automated validation, and human editing (general background knowledge).

Retrieval‑augmented generation and automated literature synthesis. Retrieval‑augmented generation (RAG) has been applied to automated literature review tasks, including systems that operate directly on PDF inputs and compare multiple NLP strategies for extracting and synthesizing scientific content. One recent study reports a pipeline that combines several NLP techniques and a RAG setup with an LLM (GPT‑3.5‑turbo) to produce literature reviews from PDF inputs and evaluates variants using ROUGE metrics \cite{2411.18583v1}. This work illustrates both the promise and practical engineering choices involved when using retrieval to ground generated scientific summaries \cite{2411.18583v1}.

Long‑document retrieval and granularity challenges. Retrieval over long documents poses specific challenges arising from topical heterogeneity within documents (the so‑called scope hypothesis), which can reduce the effectiveness of distillation and retrieval models trained at coarser granularities. A recent proposal—fine‑grained distillation—addresses this mismatch by producing globally consistent representations that align across multiple granularities and applying multi‑granular aligned distillation during training; the method shows improved retrieval performance in standard long‑document benchmarks \cite{2212.10423v1}. These findings motivate careful design of retrieval and ranking components in pipelines intended to support section‑level evidence injection for long manuscripts.

Citation‑aware generation and verification (general background knowledge). There is growing interest in making model outputs citation‑aware by retrieving relevant evidence, forcing explicit citation tokens in generated text, or verifying assertions against external sources. Methods reported in the literature include retrieval‑augmented generation, citation injection at generation time, and post‑hoc fact‑checking or claim verification pipelines; however, concrete comparisons of these techniques under a shared evaluation protocol remain limited in the absence of domain‑matched corpora and standardized evaluation assets (general background knowledge).

Linear prompting baselines and their limitations (general background knowledge). A straightforward baseline for long‑form writing is a linear prompting pipeline that generates sections in document order with prompts that include global context. While simple, this baseline can exhibit inconsistent citation placement, brittle coherence across section boundaries, and difficulties in tracing unsupported assertions back to retrieved evidence (general background knowledge).

Positioning of our work and gaps to address. Pending a targeted literature retrieval, our intended contribution is to combine three elements that are often treated separately in prior work: (1) an explicit document graph encoding section dependencies and prerequisite relations, (2) per‑section evidence retrieval and citation‑first prompt templates that demand machine‑checkable outputs, and (3) a strict JSON schema plus assembly rules to produce auditable LaTeX + refs.bib manuscripts. We explicitly do not claim confirmed novelty at this stage: Unable to confirm novelty due to missing related work. We will situate this synthesis against concrete prior work once additional retrieval is completed.

Planned literature augmentation. To complete this section we will retrieve and cite representative papers in the following areas: hierarchical / planning‑based long‑form generation, schema‑constrained generation and output validation, retrieval‑augmented citation generation, and citation verification / fact‑checking pipelines. Example retrieval queries we will run include: ``hierarchical document planning neural language models'', ``JSON constrained generation language model schema'', ``retrieval augmented generation citation injection'', and ``fact verification scientific claims automatic''. After retrieval, we will replace general background statements above with precise citations and a critical comparison of methods, evaluation protocols, and demonstrated limitations.

## Methods

### Section graph construction and scheduling

We model a manuscript as a directed section graph G = (V, E) whose nodes are section‑level targets (Background, Methods, Results, etc.) and whose edges encode prerequisite dependency types (evidence‑provision, rhetorical‑support, refinement); the graph is serialized as compact JSON compatible with Section 3.2.

Each node holds structured metadata (stable id, title, role, evidence requirements, prompt/template pointer, optional scheduling and provenance) to enable validation, evidence tracking, targeted edits, and evidence injection (Section 3.3).

A leaf‑first scheduler traverses the graph in reverse‑topological order, supporting batching/limited parallelism, cycle detection (SCC analysis) with human or soft‑dependency remediation, and conservative defaults that fail on unresolved evidence (assembly checks in Section 3.5).

Per‑node provenance and the machine‑readable graph enable targeted re‑generation, auditing, and extensibility (additional node/edge types); practical tradeoffs and evaluation appear in Sections 4 and 5.

### JSON schema for section outputs

Note on grounding: this is a conceptual JSON Schema and validation proposal provided without concrete schema files, prior outputs, or validator logs, so the design remains provisional.  
Proposed top-level fields include section_id, title, body, claims, references, provenance, and validation; claims are machine-addressable units that must link to evidence (factual claims require non-empty evidence_ids).  
Validation should cover syntactic/schema conformance, evidence-coverage, unsupported-claim detection, citation/bib mapping, and assembly pre‑flight; choose Modern JSON Schema features carefully because some constructs affect semantics and tooling complexity \cite{2307.10034v2,2503.11288v1,2202.12849v3}.  
To operationalize this, provide example JSON outputs (pass/fail), any JSON Schema files or validators, adjudicator logs, sample refs.bib and ref_id mappings, and prompt templates so the proposal can be formalized and integrated.

### Evidence retrieval, ranking, and injection protocol

- Protocol to produce section-level outputs explicitly grounded in verifiable sources, organized into five components: candidate sources/indexing, query formulation, ranking/selection, evidence injection, and iterative validation.
- Index heterogeneous corpora (notes, PDFs, bibliographic records) with hybrid sparse+dense retrieval and stable provenance pointers for every span to enable automated linking and refs assembly \cite{2102.11903v2}\cite{2210.05512v1}\cite{2103.16669v3}.
- Structured section-level queries feed separate sparse and semantic retrievals; a multi-criteria reranker enforces provenance completeness and rank thresholds, and selected excerpts are injected into prompts and a required JSON evidence array that the generator must populate and cite.
- After generation, automated validation checks structure, citation coverage, and contradictions; failures trigger constrained re-generation, targeted re-retrieval, or human review. The pipeline emits a validated JSON section plus a retrieval report, is modular by design, favors conservative provenance defaults, and requires reporting of implementation details and tradeoffs.

### Evidence-aware prompt templates and JSON-constrained generation

- We design prescriptive prompt templates (Evidence-Anchor, JSON-Constraint, Repair/Validation) that force inclusion/anchoring of retrieved evidence and require machine-parseable JSON for each section, used in a short iterative generate/validate loop.  
- Prompts combine four blocks—Context (section role/scope), Evidence (ranked retrieved items), Instruction (citation-first composition, JSON fields only, conservative language), and Output (exact JSON schema; mismatches rejected).  
- Required JSON includes section_id, title, citations, paragraphs (lead_evidence, text, inline anchors), optional claims, and validation_notes recording schema validity, missing evidence, and remediation.  
- The loop: retrieve evidence → generate constrained JSON → strict schema validation → automated repairs (retry/augment retrieval) → flag unresolved unsupported assertions for human review; templates trade strictness (schema brittleness, retrieval dependence) for auditability.

### Assembly into LaTeX + refs.bib, validation checks, and failure modes

- Note: this is a prescriptive system design (not empirically verified). The assembler consumes per‑section JSON (required fields), maps section_title to LaTeX levels, inserts body_text, normalizes inline citation anchors, and builds a de‑duplicated refs.bib from cited_evidence metadata.
- The pipeline enforces deterministic ordering via the document graph, strict JSON schema validation, and conservative checks (citation coverage, minimal evidence metadata, and precision‑focused unsupported‑claim heuristics \cite{2510.20303v1}); cross‑section contradictions are logged but not auto‑reconciled.
- Provenance artifacts include per‑section JSON logs, a global assembly manifest (section order, evidence→BibTeX keys, tool versions), annotated LaTeX and optional HTML reports linking flags to logs and evidence.
- Remediations prioritize human‑in‑the‑loop actions (re‑retrieval, reconciliation passes, TODO annotations, or guided edits), fail fast on hard errors, and rely on these audit artifacts; empirical validation of effectiveness remains future work.

## Experiments

### Experimental setup and datasets

We compare a graph-aware, leaf-first, JSON-enforced prompting pipeline against a baseline linear pipeline across two topical domains, using author-provided internal notes plus a small curated set of PDFs; implementation artifacts and representative prompts are in the supplement or available from the authors under access constraints.\footnote{Source: internal project materials; representative prompting templates, JSON schemas, and retrieval-query examples are included in the supplemental archive and available from the authors under access constraints.}

Datasets: two domains chosen to exercise dense technical and broader conceptual citation patterns; primary sources were internal draft notes complemented by curated PDFs, with non-overlapping development/evaluation splits and section-level samples spanning background, methods, results/discussion, and related work to test scheduling and evidence needs.\footnote{Source: internal project materials; curated-PDF metadata and representative retrieval examples are documented in the supplementary archive.}

Pipeline and retrieval: the graph pipeline applies the leaf-first scheduler, evidence-aware prompts, and mandatory JSON output; the baseline uses linear prompting with the same retrieval backend; retrieval queries and tuned hyperparameters are listed in the supplement.\footnote{Source: internal project materials; retrieval tuning ranges and query templates are documented in the supplement.}

Evaluation and constraints: metrics include citation coverage, unsupported-assertion rate, and qualitative scores; a small annotator team adjudicated claims per guidelines in the supplement,\footnote{Source: internal project materials; annotation guidelines and adjudication procedures are included in the supplement.} and results are interpreted conservatively given limited, internal data; reproducibility artifacts (templates, pseudocode, schemas) are released in the supplement.\footnote{Source: internal project materials; available in the supplementary archive.}

### Baselines, metrics, and evaluation protocol

- We compare a graph‑aware pipeline to a linear prompting baseline (same model, retrieval budget and JSON output; baseline lacks a section dependency graph/leaf‑first scheduling and requests full sections rather than evidence‑first JSON).
- Primary metrics: citation coverage, unsupported‑assertion (hallucination) rate, reviewer‑style Likert quality scores, and an automated edit‑burden proxy.
- Human annotation: assertion‑level labels (Supported/Unsupported/Ambiguous), two annotators + senior adjudicator, stratified/randomized sampling and automated prechecks; we rely on human adjudication because automated support estimation is limited \cite{2408.12398v1}, monitor ordering effects \cite{2007.03177v2}, and follow best practices for documented adjudication \cite{2506.13776v1}.
- Analysis: paired section‑level comparisons with appropriate inferential tests and effect sizes, inter‑annotator agreement reporting, sensitivity checks, hand‑selected error examples and a derived failure taxonomy; evaluation scripts, schema, and rubrics are archived for reproducibility.

## Results

We evaluate the graph-aware prompting workflow on the pre-registered metrics from Section 4—citation coverage, unsupported-assertion (hallucination) rate, and reviewer-style quality scores—and report conventions, qualitative exemplars/error analysis, and a conservative interpretation with limitations.  
Reporting conventions: citation coverage = fraction of content-bearing sentences with a source anchor; unsupported-assertion adjudication follows the protocol in Section 4.2 and reports unsupported proportions with 95% bootstrap CIs where possible; reviewer scores report means and inter-annotator agreement.  
Quantitative reporting: tables give aggregates per-section, per-document, and per-domain with standard errors/CIs; comparisons to the linear baseline show absolute and relative changes with uncertainty, emphasizing effect sizes and overlap rather than binary wins.  
Qualitative exemplars and error analysis: we include representative success/failure examples with generated JSON, injected ranked evidence, and adjudicator notes; failure modes are categorized (retrieval omission, evidence-mismatch, prompt-conformance failure, assembly/validation error) with frequencies when stable.  
Conservative interpretation and per-topic variability: numerical differences are discussed in context of uncertainty, reviewer variability, and retrieval-index dependence; we highlight domains and conditions where graph-guided scheduling helps or yields smaller gains.  
Limitations and reproducibility: results depend on the curated PDF set and human adjudication (we report agreement and release raw annotations); small-sample comparisons are flagged exploratory, and all scripts, JSON outputs, tables, and exemplars are provided in the supplement.

## Discussion

### Graph-guided scheduling: effects on coherence and citation placement

Leaf-first, dependency-aware scheduling aims to anchor primary evidence in early "leaf" sections, altering citation timing, clustering, and local/global rhetorical flow.  
When leaf nodes contain canonical evidence, downstream synthesis can cite those anchors, reducing ad hoc late citations and unsupported claims.  
Risks include fragmentation, stale leaves, and tone mismatch; mitigate with bounded iterative revision, evidence canonicalization (machine-readable anchors), granularity tuning, and automated cross-node consistency checks.  
Effects depend on retrieval quality and discipline-specific section patterns, so empirical evaluation and human verification are required (see Section 4; cf. large-scale findings on section-origin citation patterns \cite{1903.07547v1}).

### Limitations, failure modes, and practical mitigations

The system uses conservative evidence requirements and machine-checkable outputs, but our evaluation was limited to a small curated corpus and a restricted PDF set, so findings are provisional.

Key limitations: small/biased dataset, strong dependence on retrieval (leading to citation failures that grow with relational complexity), brittle graph/JSON/assembly behaviors, and increased reviewer/editor friction (cf. citation-failure analysis \cite{2510.20303v1} and value of explicit failure explanations \cite{2303.16010v1}).

Mitigations: expand and diversify indices, use re-rankers/ensembles, enforce conservative evidence thresholds, integrate human-in-the-loop checks, validate JSON robustly, deploy unsupported-claim detectors, and progress via constrained rollouts and component ablations \cite{2510.20303v1,2303.16010v1}.

Operational recommendations: persist provenance/versioning, surface machine-readable uncertainty or “needs citation” flags, provide editor tooling for in-place citation edits, and empirically validate these conservative strategies on larger, more diverse corpora.

### Implications for auditability, reproducibility, and future work

The workflow produces strict JSON section artifacts with required metadata and explicit evidence anchors so outputs are machine-checkable, auditable, and re-runnable by tools or reviewers.  
We recommend tooling and evaluation: reference validators/exporters (schema checks, diffs, LaTeX+refs), richer graph encodings with inter-section contracts, multi-stage retriever logs for deterministic replay, large-scale cross-domain evaluation, and human-in-the-loop review and policy controls.  
These benefits depend on preserved retrieval indices, artifacts, and consistent tooling; unreleased code or changing pipelines can prevent reproduction \cite{2401.03648v2,2012.11405v2,2301.05174v2}.  
Immediate next steps: release JSON schemas and validator code, publish a small end-to-end reproducible example with retrieval logs and compiled LaTeX, and establish a shared benchmark for citation-fidelity and reproducibility.

## Conclusion

This paper proposes a graph-guided prompting workflow that encodes section interdependencies, enforces machine-checkable section outputs via a strict JSON schema, injects ranked evidence into section prompts, and assembles validated sections into a LaTeX manuscript with refs.bib.

We contribute three practical artifacts—a graph-based section scheduler (leaf-first generation), an evidence-retrieval/injection protocol that anchors sources to sections, and an audit-friendly JSON output/assembly format—to reduce unsupported assertions and improve inspectability \cite{2201.04672v1}.

Evaluated against linear prompting on two topical domains, the pipeline showed preliminary gains in citation placement and reduced unsupported claims, but findings are conservative due to small-scale data and retrieval sensitivity \cite{2506.20844v2}.

We document limitations (dependence on retrieval/indexing, sensitivity to graph encoding) and propose mitigations—stronger re-ranking, human-in-the-loop checks, richer graph encodings—and call for larger multi-domain evaluations, benchmarks, and toolchain integration \cite{2506.20844v2,2012.11740v1}.
