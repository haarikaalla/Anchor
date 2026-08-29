"""Generated visuals: animated terminal casts, charts and belief graphs.

Every asset in ``docs/assets`` is produced by this package from a live engine
run, so the documentation is a build artefact of the code rather than a
hand-maintained set of screenshots.
"""

from .charts import Series, comparison_matrix, graph_chart, grouped_bars, timeline_chart
from .hero import hero_banner
from .render import render_all
from .terminal import TerminalCast

__all__ = [
    "Series",
    "TerminalCast",
    "comparison_matrix",
    "graph_chart",
    "grouped_bars",
    "hero_banner",
    "render_all",
    "timeline_chart",
]
