"""Facts extracted from one source file. Pure data, no I/O."""

from typing import Literal

from pydantic import BaseModel, Field

SymbolKind = Literal["module", "class", "function", "method", "export"]
# "type": TypeScript `import type` / `export type ... from`, erased at runtime.
ImportKind = Literal["import", "from", "require", "dynamic", "reexport", "side_effect", "type"]


class Symbol(BaseModel):
    kind: SymbolKind
    name: str
    # Dotted name inside the file, e.g. "Signer.sign". The module symbol uses "<module>".
    qualified_name: str
    parent: str | None = None
    start_line: int
    end_line: int
    decorators: list[str] = Field(default_factory=list)
    bases: list[str] = Field(default_factory=list)
    has_docstring: bool = False
    is_async: bool = False
    # Source text used for full-text search (class: header only, as in chunking).
    text: str = ""


class ImportRef(BaseModel):
    kind: ImportKind
    # Imported module as written: "os.path", "..utils" is module="utils" with level=2,
    # "./api" for JS.
    module: str
    name: str | None = None
    alias: str | None = None
    level: int = 0
    line: int


class CallRef(BaseModel):
    # Dotted callee as written ("self.sign", "json.dumps"), or "<dynamic>" when the
    # callee is not a plain name chain (e.g. getattr(x, n)(), handlers[k]()).
    callee: str
    line: int
    enclosing_symbol: str | None = None


class FileFacts(BaseModel):
    relative_path: str
    language: Literal["python", "javascript", "typescript"]
    module: str | None = None
    symbols: list[Symbol] = Field(default_factory=list)
    imports: list[ImportRef] = Field(default_factory=list)
    calls: list[CallRef] = Field(default_factory=list)


class AnalysisFailure(Exception):
    """The file could not be analyzed; `reason` is shown in the coverage ledger."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason
