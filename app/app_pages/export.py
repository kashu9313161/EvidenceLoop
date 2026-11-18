from app.components import coming_soon
from app.state import require_snapshot

require_snapshot()

coming_soon(
    "Milestone 8",
    "Export packages what EvidenceLoop knows about this snapshot so you can keep exploring it "
    "outside the app.",
    [
        "An Obsidian vault ZIP named after the commit SHA",
        "One note per module and per verified finding, with links between them",
        "A snapshot export, not a live sync",
    ],
)
