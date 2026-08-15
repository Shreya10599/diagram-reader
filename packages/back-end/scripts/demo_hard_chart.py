"""
demo_hard_chart.py — renders a deliberately hard chart (daily usage bars with
sparse date ticks plus a temperature overlay, modeled on a real PG&E usage
chart) and runs it through the extraction loop in-process.

Why this chart: it's the one that historically exposed the off-by-one
date-counting bug — daily bars but only every 4th day labeled — so it's the
best candidate for a real multi-round verification loop when the model's
first pass misreads something.

Usage (from packages/back-end, venv active):
    .venv/bin/python scripts/demo_hard_chart.py            # normal run
    .venv/bin/python scripts/demo_hard_chart.py --force 3  # force rounds 1-2 to
                                                           # mismatch (debug flag),
                                                           # so you can watch the
                                                           # multi-round loop reliably

The step logs (Step 1: ... : ...) print as the pipeline runs; the final
extracted series is printed against the known ground truth at the end.
"""

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
parser.add_argument(
    "--force",
    type=int,
    default=0,
    help="FORCE_VERIFY_ROUNDS debug value: force mismatch in rounds 1..N-1 (default 0 = off)",
)
parser.add_argument(
    "--out",
    default=None,
    help="path to save the rendered PNG (default: temp demo_chart.png)",
)
args = parser.parse_args()

if args.force:
    os.environ["FORCE_VERIFY_ROUNDS"] = str(args.force)

import base64
import io
import logging

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt

logging.basicConfig(level=logging.INFO, format="%(message)s")

from app.graph import analyze_chart
from app.models import AnalyzeChartRequest

GROUND_TRUTH = [
    ("Sep 1", 5.2),
    ("Sep 2", 8.9),
    ("Sep 3", 4.4),
    ("Sep 4", 6.1),
    ("Sep 5", 4.0),
    ("Sep 6", 5.6),
    ("Sep 7", 7.8),
    ("Sep 8", 6.9),
    ("Sep 9", 9.3),
    ("Sep 10", 5.0),
]
labels = [label for label, _ in GROUND_TRUTH]
values = [value for _, value in GROUND_TRUTH]
temp_overlay = [61, 63, 60, 58, 59, 64, 66, 62, 60, 57]


def render_chart() -> str:
    """Renders the sparse-tick daily chart to a base64 PNG data URL."""
    x_positions = list(range(len(labels)))
    fig, ax = plt.subplots(figsize=(6, 4.5), dpi=130)
    ax.bar(x_positions, values, color="#3f6fd1")
    ax.set_xlabel("Date")
    ax.set_ylabel("Usage (kWh)")
    ax.set_title("Electricity Usage from Grid")
    ax.grid(axis="y", linestyle="--", alpha=0.5)

    # Only every 4th day gets a labeled tick — the real chart doesn't put a
    # tick under every bar, which is exactly what makes date assignment hard.
    tick_positions = x_positions[::4]
    ax.set_xticks(tick_positions)
    ax.set_xticklabels([labels[i] for i in tick_positions])

    ax2 = ax.twinx()
    ax2.plot(x_positions, temp_overlay, color="purple", marker="o", linewidth=2)
    ax2.set_yticks([])
    for x, t in zip(x_positions, temp_overlay):
        ax2.annotate(
            f"{t}°",
            (x, t),
            textcoords="offset points",
            xytext=(0, 6),
            fontsize=8,
            color="purple",
        )
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    if args.out:
        plt.savefig(args.out, format="png")
        print(f"Chart saved to {args.out}")
    plt.close(fig)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    return f"data:image/png;base64,{b64}"


def main():
    image_data_url = render_chart()
    print("=" * 60)
    result = analyze_chart(AnalyzeChartRequest(image=image_data_url))
    print("=" * 60)

    truth = {label: value for label, value in GROUND_TRUTH}
    print("FINAL SERIES vs GROUND TRUTH:")
    for point in result.structuredData.series:
        true_value = truth.get(point.label)
        flag = ""
        if true_value is not None and abs(true_value - point.value) >= 0.05:
            flag = "   <-- OFF"
        print(f"  {point.label}: {point.value}  (true {true_value}){flag}")
    print("confidence:", result.structuredData.confidence)


if __name__ == "__main__":
    main()
