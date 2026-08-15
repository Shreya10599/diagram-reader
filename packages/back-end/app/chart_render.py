import base64
import io

import matplotlib

matplotlib.use("Agg")  # headless — no display needed to render charts
import matplotlib.pyplot as plt

from .models import StructuredData


def render_chart_image(structured: StructuredData) -> str:
    """Draws a chart from extracted StructuredData and returns it as a PNG
    data URL, so a verification pass can compare it against the original
    image. The re-render shows exactly what the extracted numbers imply —
    if the table was accurate, the two charts should look identical."""
    labels = [p.label for p in structured.series]
    values = [p.value for p in structured.series]
    chart_type = structured.chartType

    fig, ax = plt.subplots(figsize=(6, 4.5), dpi=150)

    if chart_type == "pie":
        ax.pie(values, labels=labels, autopct="%1.0f%%")
        ax.axis("equal")
    elif chart_type == "bar":
        ax.bar(labels, values, color="#4C72B0")
    elif chart_type == "scatter":
        x_positions = list(range(len(labels)))
        ax.scatter(x_positions, values, color="#4C72B0")
        ax.set_xticks(x_positions)
        ax.set_xticklabels(labels)
    else:  # line, other
        ax.plot(labels, values, marker="o", color="#4C72B0", linewidth=2)

    if structured.title:
        ax.set_title(structured.title)
    if chart_type != "pie":
        if structured.xLabel:
            ax.set_xlabel(structured.xLabel)
        if structured.yLabel:
            ax.set_ylabel(structured.yLabel)
        ax.grid(axis="y", linestyle="--", alpha=0.5)
        if len(labels) > 12:
            for tick in ax.get_xticklabels():
                tick.set_rotation(45)
                tick.set_ha("right")

    plt.tight_layout()

    buf = io.BytesIO()
    plt.savefig(buf, format="png")
    plt.close(fig)
    buf.seek(0)
    b64 = base64.b64encode(buf.read()).decode()
    return f"data:image/png;base64,{b64}"
