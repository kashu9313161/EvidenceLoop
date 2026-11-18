from app.components import coming_soon
from app.state import require_snapshot

require_snapshot()

coming_soon(
    "Milestone 4",
    "The graph opens as soon as analysis finishes, drawing every file and every connection "
    "between files, the way Obsidian's graph view does.",
    [
        "Files as dots sized by how many files depend on them, coloured by top-level folder",
        "Hover a file to light up all its connections; click it to open its note and code",
        "Confirmed links solid, inferred links dashed, a red ring on import cycles",
        "Big repos start at folder level: click a folder to expand it",
        "Local view (1 to 3 hops), search by full path, and Impact to see what depends on a file",
    ],
)
