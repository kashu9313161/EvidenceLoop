
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Where analyses are kept: the database and extracted repositories. EL_DATA_DIR moves it (a
# bigger disk, or a clean folder to try things out); the default is data/ in the project.
DATA_DIR = Path(os.environ.get("EL_DATA_DIR", "").strip() or PROJECT_ROOT / "data").expanduser()
REPOSITORIES_DIR = DATA_DIR / "repositories"
TEMP_DIR = DATA_DIR / "tmp"

DATABASE_PATH = DATA_DIR / "evidenceloop.db"

# Download limits
MAX_ARCHIVE_DOWNLOAD_BYTES = 100 * 1024 * 1024

# Extraction limits
MAX_TOTAL_UNCOMPRESSED_BYTES = 250 * 1024 * 1024
MAX_FILE_UNCOMPRESSED_BYTES = 10 * 1024 * 1024
MAX_FILES = 5_000

# Compression ratio protection
MAX_COMPRESSION_RATIO = 100

# Network configuration
DOWNLOAD_CHUNK_SIZE = 1024 * 1024
HTTP_TIMEOUT_SECONDS = 60

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
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "dist",
    "build",
    ".next",
    "coverage",
}

IGNORED_FILE_NAMES = {
    ".gitmodules",
}
