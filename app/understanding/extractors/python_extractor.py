"""Deterministic Python AST extractor for static module and symbol topology."""

from __future__ import annotations

import ast
from typing import Any, Sequence

from app.understanding.extractors.base import BaseExtractor
from app.understanding.models import (
    ScanWarning,
    StructuralFact,
    StructuralFactType,
)


class PythonAstExtractor(BaseExtractor):
    """Extract modules, imports, classes, methods, and functions using Python standard AST."""

    @property
    def extractor_id(self) -> str:
        return "python_ast_v1"

    def can_extract(self, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/")
        return normalized.endswith(".py") or normalized.endswith(".pyi")

    def extract(
        self,
        relative_path: str,
        content: str,
        lines: Sequence[str],
    ) -> tuple[tuple[StructuralFact, ...], tuple[ScanWarning, ...]]:
        normalized_path = relative_path.replace("\\", "/")
        facts: list[StructuralFact] = []
        warnings: list[ScanWarning] = []

        try:
            tree = ast.parse(content, filename=normalized_path)
        except SyntaxError as exc:
            warnings.append(
                ScanWarning(
                    warning_type="PYTHON_SYNTAX_ERROR",
                    message=f"Syntax error parsing {normalized_path}: {exc.msg}",
                    file_path=normalized_path,
                    line=exc.lineno,
                )
            )
            return ((), tuple(warnings))
        except Exception as exc:
            warnings.append(
                ScanWarning(
                    warning_type="PYTHON_PARSE_ERROR",
                    message=f"Failed to parse {normalized_path}: {type(exc).__name__}: {exc}",
                    file_path=normalized_path,
                )
            )
            return ((), tuple(warnings))

        module_name = self._derive_module_name(normalized_path)
        is_package = normalized_path.endswith("__init__.py")

        # 1. Module / Package Fact
        module_doc = ast.get_docstring(tree) or ""
        fact_type = StructuralFactType.PACKAGE_DEFINED if is_package else StructuralFactType.MODULE_DEFINED
        facts.append(
            StructuralFact.create(
                fact_type=fact_type,
                source_path=normalized_path,
                line_start=1,
                line_end=len(lines) if lines else 1,
                extractor_id=self.extractor_id,
                details={
                    "module_name": module_name,
                    "is_package": is_package,
                    "has_docstring": bool(module_doc),
                    "docstring_summary": module_doc.split("\n\n")[0].strip() if module_doc else "",
                },
            )
        )

        # 2. Extract AST Nodes
        for node in tree.body:
            # Imports
            if isinstance(node, ast.Import):
                for alias in node.names:
                    facts.append(
                        StructuralFact.create(
                            fact_type=StructuralFactType.IMPORT_DECLARED,
                            source_path=normalized_path,
                            line_start=node.lineno,
                            line_end=node.end_lineno or node.lineno,
                            extractor_id=self.extractor_id,
                            details={
                                "import_type": "direct",
                                "module": alias.name,
                                "alias": alias.asname,
                                "level": 0,
                            },
                        )
                    )
            elif isinstance(node, ast.ImportFrom):
                imported_module = node.module or ""
                names = [
                    {"name": alias.name, "asname": alias.asname}
                    for alias in node.names
                ]
                facts.append(
                    StructuralFact.create(
                        fact_type=StructuralFactType.IMPORT_DECLARED,
                        source_path=normalized_path,
                        line_start=node.lineno,
                        line_end=node.end_lineno or node.lineno,
                        extractor_id=self.extractor_id,
                        details={
                            "import_type": "from",
                            "module": imported_module,
                            "imported_names": names,
                            "level": node.level,
                        },
                    )
                )

            # Classes
            elif isinstance(node, ast.ClassDef):
                self._extract_class(node, normalized_path, module_name, facts)

            # Top-level Functions
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._extract_function(node, normalized_path, module_name, parent_class=None, facts=facts)

            # Top-level Constants
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name) and target.id.isupper():
                        facts.append(
                            StructuralFact.create(
                                fact_type=StructuralFactType.SYMBOL_DECLARED,
                                source_path=normalized_path,
                                line_start=node.lineno,
                                line_end=node.end_lineno or node.lineno,
                                extractor_id=self.extractor_id,
                                details={
                                    "symbol_name": target.id,
                                    "symbol_type": "constant",
                                    "module_name": module_name,
                                    "parent_class": None,
                                },
                            )
                        )

        return (tuple(facts), tuple(warnings))

    def _extract_class(
        self,
        node: ast.ClassDef,
        source_path: str,
        module_name: str,
        facts: list[StructuralFact],
    ) -> None:
        bases: list[str] = []
        for b in node.bases:
            if isinstance(b, ast.Name):
                bases.append(b.id)
            elif isinstance(b, ast.Attribute):
                bases.append(self._attribute_name(b))

        decorators = [self._decorator_name(d) for d in node.decorator_list]
        doc = ast.get_docstring(node) or ""

        facts.append(
            StructuralFact.create(
                fact_type=StructuralFactType.SYMBOL_DECLARED,
                source_path=source_path,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                extractor_id=self.extractor_id,
                details={
                    "symbol_name": node.name,
                    "symbol_type": "class",
                    "module_name": module_name,
                    "parent_class": None,
                    "bases": bases,
                    "decorators": decorators,
                    "has_docstring": bool(doc),
                },
            )
        )

        # Extract Methods in class
        for sub_node in node.body:
            if isinstance(sub_node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self._extract_function(
                    sub_node,
                    source_path,
                    module_name,
                    parent_class=node.name,
                    facts=facts,
                )

    def _extract_function(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef,
        source_path: str,
        module_name: str,
        parent_class: str | None,
        facts: list[StructuralFact],
    ) -> None:
        args = [arg.arg for arg in node.args.args]
        decorators = [self._decorator_name(d) for d in node.decorator_list]
        doc = ast.get_docstring(node) or ""
        symbol_type = "method" if parent_class else "function"

        facts.append(
            StructuralFact.create(
                fact_type=StructuralFactType.SYMBOL_DECLARED,
                source_path=source_path,
                line_start=node.lineno,
                line_end=node.end_lineno or node.lineno,
                extractor_id=self.extractor_id,
                details={
                    "symbol_name": node.name,
                    "symbol_type": symbol_type,
                    "module_name": module_name,
                    "parent_class": parent_class,
                    "parameters": args,
                    "is_async": isinstance(node, ast.AsyncFunctionDef),
                    "decorators": decorators,
                    "has_docstring": bool(doc),
                },
            )
        )

    @staticmethod
    def _decorator_name(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            return node.id
        if isinstance(node, ast.Attribute):
            return PythonAstExtractor._attribute_name(node)
        if isinstance(node, ast.Call):
            return PythonAstExtractor._decorator_name(node.func)
        return "unknown_decorator"

    @staticmethod
    def _attribute_name(node: ast.Attribute) -> str:
        parts: list[str] = [node.attr]
        curr = node.value
        while isinstance(curr, ast.Attribute):
            parts.append(curr.attr)
            curr = curr.value
        if isinstance(curr, ast.Name):
            parts.append(curr.id)
        parts.reverse()
        return ".".join(parts)

    @staticmethod
    def _derive_module_name(relative_path: str) -> str:
        clean = relative_path.replace("\\", "/")
        if clean.endswith("/__init__.py"):
            clean = clean[:-12]
        elif clean.endswith(".py"):
            clean = clean[:-3]
        elif clean.endswith(".pyi"):
            clean = clean[:-4]
        return clean.replace("/", ".")
