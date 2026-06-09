from __future__ import annotations

import json

from .latex_extract import (
    extract_cite_keys,
    extract_includegraphics_files,
    extract_numeric_claim_spans,
    extract_source_rids,
    extract_table_and_figure_refs,
)


SAMPLE_TEX = r"""
\section{1. Introduction}
We report 12.5% improvement over baseline.\footnote{Source: RID:run:exp1:metrics.json}
See Figure~\ref{fig:accuracy} and Table~\ref{tab:results}.
\begin{figure}
\includegraphics[width=\linewidth]{figures/acc.png}
\caption{Accuracy plot.}\label{fig:accuracy}
\end{figure}
\begin{table}
\caption{Results}\label{tab:results}
\end{table}
We cite prior work \cite{smith2020,doe2021} and \citet{miller2019}.
Equation: $E = mc^2$ and \begin{equation}a^2 + b^2 = c^2\end{equation}
"""


def main() -> None:
    cites = sorted(extract_cite_keys(SAMPLE_TEX))
    rids = sorted(extract_source_rids(SAMPLE_TEX))
    figures = extract_includegraphics_files(SAMPLE_TEX)
    refs = extract_table_and_figure_refs(SAMPLE_TEX)
    numbers = extract_numeric_claim_spans(SAMPLE_TEX)
    payload = {
        "cite_keys": cites,
        "rids": rids,
        "figures": figures,
        "refs": {k: sorted(list(v)) for k, v in refs.items()},
        "numeric_spans": numbers,
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
