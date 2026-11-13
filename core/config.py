
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = PROJECT_ROOT / "data"
REPOSITORIES_DIR = DATA_DIR / "repositories"
DATABASE_PATH = DATA_DIR / "evidenceloop.db"

# Initial safety limits. We can tune these after benchmarking.
MAX_ARCHIVE_DOWNLOAD_BYTES = 100 * 1024 * 1024
MAX_TOTAL_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_FILE_UNCOMPRESSED_BYTES = 10 * 1024 * 1024
MAX_FILES = 5_000

# Files we can meaningfully index in the initial ingestion milestone.
SUPPORTED_EXTENSIONS = {
    ".py", ".pyi",
    ".js", ".jsx", ".mjs", ".cjs",
    ".ts", ".tsx",
    ".json", ".yaml", ".yml", ".toml",
    ".ini", ".cfg", ".md", ".rst",
    ".txt", ".html", ".css", ".scss",
    ".sql", ".sh", ".ps1",
    ".dockerfile",
}

IGNORED_DIRECTORY_NAMES = {
    ".git", ".venv", "venv", "node_modules",
    "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", "dist", "build", ".next",
    "coverage",
}

IGNORED_FILE_NAMES = {
    ".gitmodules",
}
