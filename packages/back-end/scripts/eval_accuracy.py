"""
eval_accuracy.py — measures /analyze-chart's numerical extraction accuracy
against charts with known ground-truth values.

Why this exists: a chart image has no "correct answer" to grade against
unless we built the chart ourselves and already know the numbers. This
script generates a few charts (bar/line/pie, plus a sparse-tick daily bar
chart modeled on a real PG&E usage chart that exposed a date-counting bug)
with known values, sends each through the real running backend N times,
and reports how close the extracted numbers land to ground truth — turning
"does extraction work" from a vibe into an actual measured number you can
cite.

Usage:
    1. Start the backend first (needs ANTHROPIC_API_KEY set, same as normal):
         cd packages/back-end && .venv/bin/uvicorn app.main:app --reload
    2. In another terminal, from packages/back-end:
         .venv/bin/pip install -r requirements.txt   # picks up matplotlib/requests
         .venv/bin/python scripts/eval_accuracy.py
    3. Results print to the console and save to:
         scripts/eval_results/<timestamp>.json  (full raw data)
         scripts/eval_results/<timestamp>.md    (summary you can paste into
                                                   a README or pitch deck)

Commit the .json/.md output — it's the actual evidence that extraction
accuracy was measured, not assumed.
"""

import base64
import io
import json
import statistics
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib

matplotlib.use("Agg")  # headless — no display needed to render charts
import matplotlib.pyplot as plt
import requests

API_BASE_URL = "http://localhost:8000"
RUNS_PER_CHART = 5
TOLERANCE_PCT = 5.0  # a value within 5% of ground truth counts as a pass

RESULTS_DIR = Path(__file__).parent / "eval_results"


# ---- Ground-truth chart definitions -------------------------------------
# Each chart is rendered from these exact numbers, so we always know what
# a "correct" extraction looks like — no manual labeling needed.

CHARTS = [
    {
        "type": "bar",
        "title": "Monthly Rainfall",
        "xlabel": "Month",
        "ylabel": "Rainfall (mm)",
        "series": [
            ("January", 40),
            ("February", 65),
            ("March", 30),
            ("April", 90),
            ("May", 55),
        ],
    },
    {
        "type": "line",
        "title": "Weekly Step Count",
        "xlabel": "Day",
        "ylabel": "Steps",
        "series": [
            ("Mon", 4200),
            ("Tue", 6100),
            ("Wed", 5300),
            ("Thu", 7800),
            ("Fri", 6900),
            ("Sat", 9200),
            ("Sun", 5000),
        ],
    },
    {
        "type": "pie",
        "title": "Household Budget",
        "xlabel": None,
        "ylabel": None,
        "series": [
            ("Housing", 35),
            ("Food", 20),
            ("Transport", 15),
            ("Savings", 20),
            ("Other", 10),
        ],
    },
    {
        # Mirrors the real PG&E usage chart that exposed the date-counting
        # bug: daily bars, but only every 4th day is a labeled tick, plus a
        # temperature line overlaid — so the model has to count sequential
        # positions from the nearest labeled tick instead of reading a
        # label directly under each bar. The temp_overlay line is not
        # graded (StructuredData only has one series) — it's included so
        # the chart visually matches the real one that caused the bug,
        # since the extra visual clutter may itself affect accuracy.
        "type": "sparse_daily",
        "title": "Electricity Usage from Grid",
        "xlabel": "Date",
        "ylabel": "Usage (kWh)",
        "tick_every": 4,
        "temp_overlay": [61, 63, 60, 58, 59, 64, 66, 62, 60, 57],
        "series": [
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
        ],
    },
]


def render_chart(spec: dict) -> str:
    """Renders a chart spec to a base64 PNG data URL via matplotlib —
    same format a real camera capture or upload produces, so this is
    testing the exact same code path as a real user's image."""
    labels = [label for label, _ in spec["series"]]
    values = [value for _, value in spec["series"]]

    fig, ax = plt.subplots(figsize=(6, 4.5), dpi=150)

    if spec["type"] == "bar":
        ax.bar(labels, values, color="#4C72B0")
        ax.set_xlabel(spec["xlabel"])
        ax.set_ylabel(spec["ylabel"])
        ax.grid(axis="y", linestyle="--", alpha=0.5)
    elif spec["type"] == "line":
        ax.plot(labels, values, marker="o", color="#4C72B0", linewidth=2)
        ax.set_xlabel(spec["xlabel"])
        ax.set_ylabel(spec["ylabel"])
        ax.grid(axis="y", linestyle="--", alpha=0.5)
    elif spec["type"] == "pie":
        ax.pie(values, labels=labels, autopct="%1.0f%%", colors=plt.cm.Set2.colors)
        ax.axis("equal")
    elif spec["type"] == "sparse_daily":
        x_positions = list(range(len(labels)))
        ax.bar(x_positions, values, color="#3f6fd1")
        ax.set_xlabel(spec["xlabel"])
        ax.set_ylabel(spec["ylabel"])
        ax.grid(axis="y", linestyle="--", alpha=0.5)

        # Only label every Nth day — the real chart doesn't put a tick
        # under every single bar, which is exactly what makes date
        # assignment hard.
        tick_every = spec.get("tick_every", 4)
        tick_positions = x_positions[::tick_every]
        ax.set_xticks(tick_positions)
        ax.set_xticklabels([labels[i] for i in tick_positions])

        if spec.get("temp_overlay"):
            ax2 = ax.twinx()
            ax2.plot(
                x_positions, spec["temp_overlay"], color="purple", marker="o", linewidth=2
            )
            ax2.set_yticks([])
            for x, t in zip(x_positions, spec["temp_overlay"]):
                ax2.annotate(
                    f"{t}°",
                    (x, t),
                    textcoords="offset points",
                    xytext=(0, 6),
                    fontsize=8,
                    color="purple",
                )
    else:
        raise ValueError(f"Unsupported chart type for eval: {spec['type']}")

    ax.set_title(spec["title"])
    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    return f"data:image/png;base64,{b64}"


def normalize_label(label: str) -> str:
    return label.strip().lower()


def score_extraction(spec: dict, extracted_series: list) -> dict:
    """Matches extracted series against ground truth by label and scores
    each value's % error. A label the model didn't return at all (renamed,
    dropped, or merged) counts as a fail rather than being silently
    skipped — a wrong/missing label is itself a real extraction problem,
    not just a formatting quirk."""
    ground_truth = {normalize_label(label): value for label, value in spec["series"]}
    extracted_by_label = {
        normalize_label(item["label"]): item["value"] for item in extracted_series
    }

    per_value_results = []
    for label, true_value in ground_truth.items():
        if label not in extracted_by_label:
            per_value_results.append(
                {
                    "label": label,
                    "true": true_value,
                    "extracted": None,
                    "error_pct": None,
                    "pass": False,
                }
            )
            continue
        extracted_value = extracted_by_label[label]
        error_pct = abs(extracted_value - true_value) / true_value * 100 if true_value else 0
        per_value_results.append(
            {
                "label": label,
                "true": true_value,
                "extracted": extracted_value,
                "error_pct": round(error_pct, 2),
                "pass": error_pct <= TOLERANCE_PCT,
            }
        )

    extra_labels = sorted(set(extracted_by_label) - set(ground_truth))

    return {
        "per_value": per_value_results,
        "extra_labels": extra_labels,
        "n_values": len(ground_truth),
        "n_pass": sum(1 for r in per_value_results if r["pass"]),
    }


def run_eval():
    RESULTS_DIR.mkdir(exist_ok=True)
    all_runs = []

    for spec in CHARTS:
        print(f"\n=== {spec['title']} ({spec['type']}) ===")
        image_data_url = render_chart(spec)

        for run_idx in range(1, RUNS_PER_CHART + 1):
            print(f"  Run {run_idx}/{RUNS_PER_CHART}...", end=" ", flush=True)
            try:
                res = requests.post(
                    f"{API_BASE_URL}/analyze-chart",
                    json={"image": image_data_url},
                    timeout=60,
                )
                res.raise_for_status()
                body = res.json()
                structured = body["structuredData"]
                scored = score_extraction(spec, structured["series"])
                scored.update(
                    {
                        "chart_title": spec["title"],
                        "chart_type": spec["type"],
                        "run": run_idx,
                        "reported_confidence": structured.get("confidence"),
                        "reported_uncertain": structured.get("uncertainValues") or [],
                        "error": None,
                    }
                )
                print(f"{scored['n_pass']}/{scored['n_values']} within {TOLERANCE_PCT}%")
            except Exception as exc:
                scored = {
                    "chart_title": spec["title"],
                    "chart_type": spec["type"],
                    "run": run_idx,
                    "error": str(exc),
                    "per_value": [],
                    "extra_labels": [],
                    "n_values": len(spec["series"]),
                    "n_pass": 0,
                    "reported_confidence": None,
                    "reported_uncertain": [],
                }
                print(f"FAILED: {exc}")

            all_runs.append(scored)

    summarize(all_runs)


def summarize(all_runs: list):
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")

    total_values = sum(r["n_values"] for r in all_runs)
    total_pass = sum(r["n_pass"] for r in all_runs)
    all_errors = [
        v["error_pct"] for r in all_runs for v in r["per_value"] if v["error_pct"] is not None
    ]
    failed_calls = [r for r in all_runs if r.get("error")]

    overall_pass_rate = (total_pass / total_values * 100) if total_values else 0
    mean_error = statistics.mean(all_errors) if all_errors else None

    by_type = {}
    for r in all_runs:
        t = r["chart_type"]
        by_type.setdefault(t, {"n_values": 0, "n_pass": 0})
        by_type[t]["n_values"] += r["n_values"]
        by_type[t]["n_pass"] += r["n_pass"]

    summary = {
        "timestamp": timestamp,
        "tolerance_pct": TOLERANCE_PCT,
        "runs_per_chart": RUNS_PER_CHART,
        "total_values_checked": total_values,
        "total_values_passed": total_pass,
        "overall_pass_rate_pct": round(overall_pass_rate, 1),
        "mean_abs_error_pct": round(mean_error, 2) if mean_error is not None else None,
        "failed_api_calls": len(failed_calls),
        "total_calls": len(all_runs),
        "by_chart_type": {
            t: {
                "n_values": d["n_values"],
                "n_pass": d["n_pass"],
                "pass_rate_pct": round(d["n_pass"] / d["n_values"] * 100, 1)
                if d["n_values"]
                else 0,
            }
            for t, d in by_type.items()
        },
        "raw_runs": all_runs,
    }

    json_path = RESULTS_DIR / f"{timestamp}.json"
    json_path.write_text(json.dumps(summary, indent=2))

    md_lines = [
        f"# Chart extraction accuracy — {timestamp}",
        "",
        f"- Tolerance: values within **{TOLERANCE_PCT}%** of ground truth count as a pass",
        f"- Runs per chart: {RUNS_PER_CHART}",
        f"- **Overall pass rate: {summary['overall_pass_rate_pct']}%** "
        f"({total_pass}/{total_values} values)",
        f"- Mean absolute error: {summary['mean_abs_error_pct']}%"
        if mean_error is not None
        else "- Mean absolute error: n/a (no successful extractions)",
        f"- Failed API calls: {len(failed_calls)}/{len(all_runs)}",
        "",
        "## By chart type",
        "",
        "| Chart type | Values checked | Passed | Pass rate |",
        "|---|---|---|---|",
    ]
    for t, d in summary["by_chart_type"].items():
        md_lines.append(f"| {t} | {d['n_values']} | {d['n_pass']} | {d['pass_rate_pct']}% |")

    md_path = RESULTS_DIR / f"{timestamp}.md"
    md_path.write_text("\n".join(md_lines) + "\n")

    print("\n" + "=" * 50)
    print(
        f"Overall pass rate: {summary['overall_pass_rate_pct']}% "
        f"({total_pass}/{total_values} values within {TOLERANCE_PCT}%)"
    )
    if mean_error is not None:
        print(f"Mean absolute error: {summary['mean_abs_error_pct']}%")
    print(f"Failed API calls: {len(failed_calls)}/{len(all_runs)}")
    print(f"\nFull results: {json_path}")
    print(f"Summary report: {md_path}")


if __name__ == "__main__":
    try:
        requests.get(f"{API_BASE_URL}/health", timeout=5)
    except Exception:
        print(
            f"Could not reach {API_BASE_URL}/health — is the backend running?\n"
            f"  cd packages/back-end && .venv/bin/uvicorn app.main:app --reload",
            file=sys.stderr,
        )
        sys.exit(1)

    run_eval()
