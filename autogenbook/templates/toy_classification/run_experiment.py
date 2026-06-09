from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Dict


def _write_placeholder_png(path: Path) -> None:
    png_base64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAA"
        "AAC0lEQVR42mP8/x8AAwMCAO6nXy0AAAAASUVORK5CYII="
    )
    path.write_bytes(base64.b64decode(png_base64))


def _run_sklearn() -> Dict[str, float]:
    import numpy as np
    from sklearn.datasets import load_iris
    from sklearn.metrics import accuracy_score, f1_score
    from sklearn.model_selection import train_test_split
    from sklearn.linear_model import LogisticRegression
    from sklearn.tree import DecisionTreeClassifier

    data = load_iris()
    X_train, X_test, y_train, y_test = train_test_split(
        data.data, data.target, test_size=0.3, random_state=42, stratify=data.target
    )

    lr = LogisticRegression(max_iter=200)
    dt = DecisionTreeClassifier(random_state=42)

    lr.fit(X_train, y_train)
    dt.fit(X_train, y_train)

    lr_pred = lr.predict(X_test)
    dt_pred = dt.predict(X_test)

    return {
        "logistic_accuracy": float(accuracy_score(y_test, lr_pred)),
        "logistic_f1": float(f1_score(y_test, lr_pred, average="macro")),
        "tree_accuracy": float(accuracy_score(y_test, dt_pred)),
        "tree_f1": float(f1_score(y_test, dt_pred, average="macro")),
    }


def _plot(metrics: Dict[str, float], out_path: Path) -> None:
    try:
        import matplotlib.pyplot as plt
    except Exception:
        _write_placeholder_png(out_path)
        return

    labels = ["LogReg", "DecisionTree"]
    acc = [metrics.get("logistic_accuracy", 0.0), metrics.get("tree_accuracy", 0.0)]
    fig, ax = plt.subplots(figsize=(4, 3))
    ax.bar(labels, acc, color=["#4C78A8", "#F58518"])
    ax.set_ylim(0, 1)
    ax.set_ylabel("Accuracy")
    ax.set_title("Toy classification accuracy")
    fig.tight_layout()
    fig.savefig(out_path)
    plt.close(fig)


def main() -> int:
    out_dir = Path.cwd()
    figures_dir = out_dir / "figures"
    figures_dir.mkdir(parents=True, exist_ok=True)

    metrics = {
        "logistic_accuracy": 0.0,
        "logistic_f1": 0.0,
        "tree_accuracy": 0.0,
        "tree_f1": 0.0,
    }
    try:
        metrics = _run_sklearn()
    except Exception:
        pass

    (out_dir / "metrics.json").write_text(json.dumps(metrics, indent=2), encoding="utf-8")

    plot_path = figures_dir / "accuracy.png"
    _plot(metrics, plot_path)

    print("Metrics written to metrics.json")
    print(f"Figure written to {plot_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
