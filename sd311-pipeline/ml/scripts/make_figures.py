"""Figures + markdown tables for reports/RESULTS.md from reports/backtest.json.

Usage:  python scripts/make_figures.py
Writes: reports/figures/*.png, reports/results_tables.md
"""
import json
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from sd311_core.labels import BUCKETS  # noqa: E402

MODEL_C, BASE_C = "#2a6fdb", "#9aa3ad"


def confusion_figure(cm, title, path):
    cm = np.array(cm, dtype=float)
    row_share = cm / cm.sum(axis=1, keepdims=True)
    fig, ax = plt.subplots(figsize=(5, 4.3))
    ax.imshow(row_share, cmap="Blues", vmin=0, vmax=1)
    for i in range(4):
        for j in range(4):
            ax.text(j, i, f"{row_share[i, j]:.0%}\n({int(cm[i, j]):,})", ha="center", va="center",
                    fontsize=8, color="white" if row_share[i, j] > 0.5 else "black")
    ax.set_xticks(range(4), BUCKETS)
    ax.set_yticks(range(4), BUCKETS)
    ax.set_xlabel("predicted bucket")
    ax.set_ylabel("true bucket")
    ax.set_title(title, fontsize=10)
    fig.tight_layout()
    fig.savefig(path, dpi=130)
    plt.close(fig)


def main():
    bt = json.loads((ROOT / "reports" / "backtest.json").read_text())
    figs = ROOT / "reports" / "figures"
    figs.mkdir(parents=True, exist_ok=True)

    confusion_figure(bt["overall"]["model"]["confusion"], "CatBoost, all test quarters (row %)",
                     figs / "confusion_model.png")
    confusion_figure(bt["overall"]["baseline"]["confusion"], "Baseline, all test quarters (row %)",
                     figs / "confusion_baseline.png")

    labels = [f["fold"]["test_start"][:7] for f in bt["folds"]]
    x = np.arange(len(labels))
    fig, ax = plt.subplots(figsize=(6.5, 3.6))
    ax.bar(x - 0.2, [f["baseline"]["macro_f1"] for f in bt["folds"]], 0.4, label="baseline", color=BASE_C)
    ax.bar(x + 0.2, [f["model"]["macro_f1"] for f in bt["folds"]], 0.4, label="CatBoost", color=MODEL_C)
    ax.set_xticks(x, labels, fontsize=9)
    ax.set_xlabel("first month of 3-month test window")
    ax.set_ylabel("macro-F1")
    ax.set_ylim(0, 1)
    ax.legend(frameon=False)
    ax.set_title("Macro-F1 per backtest fold", fontsize=10)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(figs / "fold_macro_f1.png", dpi=130)
    plt.close(fig)

    old = json.loads((ROOT / "reports" / "old_vs_new.json").read_text())
    fig, ax = plt.subplots(figsize=(6, 3.4))
    v1 = [old["q3_2025_bucket_share_v1_truncated"][b] for b in BUCKETS]
    pr = [old["q3_2025_bucket_share_proper"][b] for b in BUCKETS]
    x = np.arange(4)
    ax.bar(x - 0.2, v1, 0.4, label="v1 test set (closed only, truncated)", color=BASE_C)
    ax.bar(x + 0.2, pr, 0.4, label="proper labels (open cases kept)", color=MODEL_C)
    ax.set_xticks(x, BUCKETS)
    ax.set_ylabel("share of requests")
    ax.set_title("Q3-2025 requests: bucket distribution", fontsize=10)
    ax.legend(frameon=False, fontsize=8)
    for s in ("top", "right"):
        ax.spines[s].set_visible(False)
    fig.tight_layout()
    fig.savefig(figs / "class_distribution.png", dpi=130)
    plt.close(fig)

    rows = ["| test quarter | n test | model macro-F1 | baseline macro-F1 | model acc | baseline acc "
            "| model log-loss | baseline log-loss |",
            "|---|---|---|---|---|---|---|---|"]
    pooled = {"fold": {"test_start": "overall"}, "n_test": bt["overall"]["model"]["n"], **bt["overall"]}
    for f in bt["folds"] + [pooled]:
        m, b = f["model"], f["baseline"]
        rows.append(f"| {f['fold']['test_start'][:7]} | {f['n_test']:,} | {m['macro_f1']:.3f} | {b['macro_f1']:.3f} | "
                    f"{m['accuracy']:.3f} | {b['accuracy']:.3f} | {m['log_loss']:.3f} | {b['log_loss']:.3f} |")
    rows += ["", "| bucket | model recall | baseline recall |", "|---|---|---|"]
    for b in BUCKETS:
        rec_m, rec_b = bt["overall"]["model"]["recall"][b], bt["overall"]["baseline"]["recall"][b]
        rows.append(f"| {b} | {rec_m:.3f} | {rec_b:.3f} |")
    (ROOT / "reports" / "results_tables.md").write_text("\n".join(rows) + "\n")
    print("\n".join(rows))


if __name__ == "__main__":
    main()
