"""Bounded, line-based generic extractor for JS/TS, Go, Java, C#, Rust, and other languages."""

from __future__ import annotations

import re
from typing import Sequence

from app.understanding.extractors.base import BaseExtractor
from app.understanding.models import (
    ScanWarning,
    StructuralFact,
    StructuralFactType,
)


class GenericExtractor(BaseExtractor):
    """Extract top-level declarations and imports using bounded regex patterns."""

    _JS_TS_IMPORT_RE = re.compile(r"""(?:import\s+.*?from\s+['"]([^'"]+)['"]|require\(['"]([^'"]+)['"]\))""")
    _JS_TS_SYMBOL_RE = re.compile(r"""(?:export\s+)?(?:default\s+)?(?:class|function|interface|type|const|let|var)\s+([a-zA-Z0-9_$]+)""")

    _GO_PACKAGE_RE = re.compile(r"""^package\s+([a-zA-Z0-9_]+)""")
    _GO_IMPORT_RE = re.compile(r"""^\s*["']([a-zA-Z0-9_\-\.\/\~]+)["']""")
    _GO_SYMBOL_RE = re.compile(r"""^func\s+(?:\([^)]+\)\s+)?([a-zA-Z0-9_]+)|^type\s+([a-zA-Z0-9_]+)\s+(?:struct|interface)""")

    _JAVA_CS_PACKAGE_RE = re.compile(r"""^(?:package|namespace)\s+([a-zA-Z0-9_\.]+);?""")
    _JAVA_CS_IMPORT_RE = re.compile(r"""^(?:import|using)\s+(?:static\s+)?([a-zA-Z0-9_\.]+);?""")
    _JAVA_CS_SYMBOL_RE = re.compile(r"""(?:public|protected|private|internal)?\s*(?:static\s+)?(?:class|interface|enum|record|struct)\s+([a-zA-Z0-9_]+)""")

    _RUST_USE_RE = re.compile(r"""^use\s+([a-zA-Z0-9_:]+);?""")
    _RUST_SYMBOL_RE = re.compile(r"""(?:pub\s+)?(?:struct|enum|trait|fn|type)\s+([a-zA-Z0-9_]+)""")

    _SUPPORTED_EXTENSIONS = {
        ".js", ".jsx", ".mjs", ".cjs",
        ".ts", ".tsx",
        ".go",
        ".java",
        ".cs",
        ".rs",
    }

    @property
    def extractor_id(self) -> str:
        return "generic_regex_v1"

    def can_extract(self, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/")
        dot_idx = normalized.rfind(".")
        if dot_idx != -1:
            ext = normalized[dot_idx:].lower()
            return ext in self._SUPPORTED_EXTENSIONS
        return False

    def extract(
        self,
        relative_path: str,
        content: str,
        lines: Sequence[str],
    ) -> tuple[tuple[StructuralFact, ...], tuple[ScanWarning, ...]]:
        normalized_path = relative_path.replace("\\", "/")
        dot_idx = normalized_path.rfind(".")
        ext = normalized_path[dot_idx:].lower() if dot_idx != -1 else ""

        facts: list[StructuralFact] = []
        warnings: list[ScanWarning] = []

        try:
            if ext in (".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx"):
                self._extract_js_ts(normalized_path, lines, facts)
            elif ext == ".go":
                self._extract_go(normalized_path, lines, facts)
            elif ext in (".java", ".cs"):
                self._extract_java_cs(normalized_path, lines, facts)
            elif ext == ".rs":
                self._extract_rust(normalized_path, lines, facts)
        except Exception as exc:
            warnings.append(
                ScanWarning(
                    warning_type="GENERIC_EXTRACT_ERROR",
                    message=f"Failed generic extraction on {normalized_path}: {exc}",
                    file_path=normalized_path,
                )
            )

        return (tuple(facts), tuple(warnings))

    def _extract_js_ts(
        self,
        source_path: str,
        lines: Sequence[str],
        facts: list[StructuralFact],
    ) -> None:
        for idx, line in enumerate(lines, start=1):
            clean = line.strip()
            if not clean or clean.startswith("//") or clean.startswith("/*"):
                continue

            # Imports
            for match in self._JS_TS_IMPORT_RE.finditer(clean):
                imported_module = match.group(1) or match.group(2)
                if imported_module:
                    facts.append(
                        StructuralFact.create(
                            fact_type=StructuralFactType.IMPORT_DECLARED,
                            source_path=source_path,
                            line_start=idx,
                            line_end=idx,
                            extractor_id=self.extractor_id,
                            details={
                                "import_type": "es_or_cjs",
                                "module": imported_module,
                            },
                        )
                    )

            # Symbols
            match_sym = self._JS_TS_SYMBOL_RE.search(clean)
            if match_sym:
                sym_name = match_sym.group(1)
                facts.append(
                    StructuralFact.create(
                        fact_type=StructuralFactType.SYMBOL_DECLARED,
                        source_path=source_path,
                        line_start=idx,
                        line_end=idx,
                        extractor_id=self.extractor_id,
                        details={
                            "symbol_name": sym_name,
                            "symbol_type": "declaration",
                        },
                    )
                )

    def _extract_go(
        self,
        source_path: str,
        lines: Sequence[str],
        facts: list[StructuralFact],
    ) -> None:
        in_import_block = False
        for idx, line in enumerate(lines, start=1):
            clean = line.strip()
            if not clean or clean.startswith("//"):
                continue

            pkg_match = self._GO_PACKAGE_RE.match(clean)
            if pkg_match:
                facts.append(
                    StructuralFact.create(
                        fact_type=StructuralFactType.PACKAGE_DEFINED,
                        source_path=source_path,
                        line_start=idx,
                        line_end=idx,
                        extractor_id=self.extractor_id,
                        details={"package_name": pkg_match.group(1)},
                    )
                )
                continue

            if clean == "import (":
                in_import_block = True
                continue
            if in_import_block and clean == ")":
                in_import_block = False
                continue

            if in_import_block:
                imp_match = self._GO_IMPORT_RE.match(clean)
                if imp_match:
                    facts.append(
                        StructuralFact.create(
                            fact_type=StructuralFactType.IMPORT_DECLARED,
                            source_path=source_path,
                            line_start=idx,
                            line_end=idx,
                            extractor_id=self.extractor_id,
                            details={"import_type": "go_import", "module": imp_match.group(1)},
                        )
                    )
            elif clean.startswith("import "):
                imp_match = self._GO_IMPORT_RE.search(clean)
                if imp_match:
                    facts.append(
                        StructuralFact.create(
                            fact_type=StructuralFactType.IMPORT_DECLARED,
                            source_path=source_path,
                            line_start=idx,
                            line_end=idx,
                            extractor_id=self.extractor_id,
                            details={"import_type": "go_import", "module": imp_match.group(1)},
                        )
                    )

            sym_match = self._GO_SYMBOL_RE.match(clean)
            if sym_match:
                sym_name = sym_match.group(1) or sym_match.group(2)
                if sym_name:
                    facts.append(
                        StructuralFact.create(
                            fact_type=StructuralFactType.SYMBOL_DECLARED,
                            source_path=source_path,
                            line_start=idx,
                            line_end=idx,
                            extractor_id=self.extractor_id,
                            details={"symbol_name": sym_name, "symbol_type": "go_declaration"},
                        )
                    )

    def _extract_java_cs(
        self,
        source_path: str,
        lines: Sequence[str],
        facts: list[StructuralFact],
    ) -> None:
        for idx, line in enumerate(lines, start=1):
            clean = line.strip()
            if not clean or clean.startswith("//") or clean.startswith("/*"):
                continue

            pkg_match = self._JAVA_CS_PACKAGE_RE.match(clean)
            if pkg_match:
                facts.append(
                    StructuralFact.create(
                        fact_type=StructuralFactType.PACKAGE_DEFINED,
                        source_path=source_path,
                        line_start=idx,
                        line_end=idx,
                        extractor_id=self.extractor_id,
                        details={"package_name": pkg_match.group(1)},
                    )
                )
                continue

            imp_match = self._JAVA_CS_IMPORT_RE.match(clean)
            if imp_match:
                facts.append(
                    StructuralFact.create(
                        fact_type=StructuralFactType.IMPORT_DECLARED,
                        source_path=source_path,
                        line_start=idx,
                        line_end=idx,
                        extractor_id=self.extractor_id,
                        details={"import_type": "namespace_or_package", "module": imp_match.group(1)},
                    )
                )
                continue

            sym_match = self._JAVA_CS_SYMBOL_RE.search(clean)
            if sym_match:
                sym_name = sym_match.group(1)
                facts.append(
                    StructuralFact.create(
                        fact_type=StructuralFactType.SYMBOL_DECLARED,
                        source_path=source_path,
                        line_start=idx,
                        line_end=idx,
                        extractor_id=self.extractor_id,
                        details={"symbol_name": sym_name, "symbol_type": "type_declaration"},
                    )
                )

    def _extract_rust(
        self,
        source_path: str,
        lines: Sequence[str],
        facts: list[StructuralFact],
    ) -> None:
        for idx, line in enumerate(lines, start=1):
            clean = line.strip()
            if not clean or clean.startswith("//"):
                continue

            use_match = self._RUST_USE_RE.match(clean)
            if use_match:
                facts.append(
                    StructuralFact.create(
                        fact_type=StructuralFactType.IMPORT_DECLARED,
                        source_path=source_path,
                        line_start=idx,
                        line_end=idx,
                        extractor_id=self.extractor_id,
                        details={"import_type": "rust_use", "module": use_match.group(1)},
                    )
                )
                continue

            sym_match = self._RUST_SYMBOL_RE.match(clean)
            if sym_match:
                sym_name = sym_match.group(1)
                facts.append(
                    StructuralFact.create(
                        fact_type=StructuralFactType.SYMBOL_DECLARED,
                        source_path=source_path,
                        line_start=idx,
                        line_end=idx,
                        extractor_id=self.extractor_id,
                        details={"symbol_name": sym_name, "symbol_type": "rust_declaration"},
                    )
                )
