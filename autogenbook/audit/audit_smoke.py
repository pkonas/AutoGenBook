from __future__ import annotations

import json
from pathlib import Path

from .latex_auditor import AuditorConfig, audit_latex


SAMPLE_TEX = r"""
\section{Results}
We report 12.5% improvement over baseline without citation.
\includegraphics{figures/missing.png}
See Figure~\ref{fig:missing}.
"""


def main() -> None:
    report = audit_latex(
        tex_path=Path("out") / "paper.tex",
        tex_text=SAMPLE_TEX,
        doc_kind="paper",
        known_cite_keys={"known2024"},
        known_rids=set(),
        project_root=Path("."),
        config=AuditorConfig(mode="warn"),
    )
    print(json.dumps(report.to_json(), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
