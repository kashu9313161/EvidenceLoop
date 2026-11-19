
import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# Where analyses are kept: the database and extracted repositories. EL_DATA_DIR moves it (a
# bigger disk, or a clean folder to try things out); the default is data/ in the project.
DATA_DIR = Path(os.environ.get("EL_DATA_DIR", "").strip() or PROJECT_ROOT / "data").expanduser()
REPOSITORIES_DIR = DATA_DIR / "repositories"
TEMP_DIR = DATA_DIR / "tmp"

DATABASE_PATH = DATA_DIR / "evidenceloop.db"

# Download limits. A GitHub zipball is used only when the whole repository (as the preflight
# measures it) is smaller than this; bigger repositories fetch just their source files.
MAX_ARCHIVE_DOWNLOAD_BYTES = 100 * 1024 * 1024

# Uploaded ZIPs are streamed to a temp file, never held in memory, so they can be large: only
# the eligible entries are extracted.
MAX_UPLOAD_BYTES = 1024 * 1024 * 1024


def _env_int(name: str, default: int, ceiling: int) -> int:
    raw = os.environ.get(name, "").strip()
    value = int(raw) if raw.isdigit() else default
    return max(1, min(value, ceiling))


# Source limits: they count only the files that will be analysed (eligible source), never
# datasets, models, media or dependency folders. Over them, the user picks a folder or lets
# EvidenceLoop choose the most important files. The env vars can raise them up to the hard
# ceilings, which are a safety net and can't be exceeded.
HARD_MAX_SOURCE_FILES = 20_000
HARD_MAX_SOURCE_BYTES = 500 * 1024 * 1024
MAX_SOURCE_FILES = _env_int("EL_MAX_SOURCE_FILES", 5_000, HARD_MAX_SOURCE_FILES)
MAX_SOURCE_BYTES = _env_int("EL_MAX_SOURCE_BYTES", 250 * 1024 * 1024, HARD_MAX_SOURCE_BYTES)

# A single file larger than this is skipped, not analysed (generated bundles, data dumps).
MAX_FILE_ANALYSIS_BYTES = 1024 * 1024

# Entries a repository tree or an archive may list in total, eligible or not (memory bound).
MAX_LISTED_ENTRIES = 500_000

# git and per-file downloads
GIT_TIMEOUT_SECONDS = 600
RAW_FETCH_CONCURRENCY = 8

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

# Folders skipped at any depth, grouped by why (the group is the skip reason people see).
IGNORED_DIRECTORIES = {
    "Virtual environment": {"venv", ".venv", "env", "site-packages", ".tox"},
    "Installed dependencies": {"node_modules", "bower_components", "jspm_packages"},
    "Build output": {"dist", "build", "target", ".next", "coverage"},
    "Cache folder": {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"},
    "Version control": {".git", ".hg", ".svn"},
}
IGNORED_DIRECTORY_NAMES = set().union(*IGNORED_DIRECTORIES.values())

# `vendor/` holds copied dependencies in JavaScript and Go projects, but is often real code
# elsewhere, so it is skipped only for those languages' files.
VENDOR_DIRECTORY = "vendor"
VENDOR_EXTENSIONS = {".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".go", ".css", ".scss"}

# File types skipped before anything is downloaded, grouped by why.
SKIPPED_EXTENSIONS = {
    "Dataset": {".csv", ".tsv", ".parquet", ".feather", ".h5", ".hdf5", ".npy", ".npz",
                ".pkl", ".pickle", ".joblib", ".db", ".sqlite", ".sqlite3"},
    "Model weights": {".pt", ".pth", ".ckpt", ".keras", ".onnx", ".safetensors", ".bin",
                      ".tflite", ".pb", ".gguf"},
    "Media or document": {".pdf", ".mp4", ".mov", ".avi", ".mkv", ".mp3", ".wav", ".flac",
                          ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tif",
                          ".tiff"},
    "Archive": {".zip", ".tar", ".gz", ".tgz", ".bz2", ".xz", ".7z", ".rar", ".whl", ".jar"},
}
# SVG is text, but a large one is an exported drawing, not something to read.
SVG_SKIP_BYTES = 100 * 1024

IGNORED_FILE_NAMES = {
    ".gitmodules",
}
