"""Static manifest and dependency declaration extractor."""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from typing import Any, Sequence

try:
    import tomllib  # Python 3.11+
except ImportError:
    import tomli as tomllib  # type: ignore

from app.understanding.extractors.base import BaseExtractor
from app.understanding.models import (
    DeclaredDependency,
    ManifestDescriptor,
    ScanWarning,
    StructuralFact,
    StructuralFactType,
)


class ManifestExtractor(BaseExtractor):
    """Statically parse package and project manifests without executing package managers."""

    _REQUIREMENT_RE = re.compile(r"^([a-zA-Z0-9_\-\.]+)\s*([~=<>!].*)?$")
    _GO_REQUIRE_RE = re.compile(r"^\s*([a-zA-Z0-9_\-\.\/\~]+)\s+([v0-9\.\-\+a-zA-Z]+)")

    @property
    def extractor_id(self) -> str:
        return "manifest_static_v1"

    def can_extract(self, relative_path: str) -> bool:
        normalized = relative_path.replace("\\", "/")
        name = normalized.split("/")[-1].lower()
        if name in (
            "requirements.txt",
            "requirements-dev.txt",
            "pyproject.toml",
            "package.json",
            "package-lock.json",
            "go.mod",
            "cargo.toml",
            "pom.xml",
        ):
            return True
        if name.endswith(".csproj"):
            return True
        return False

    def extract(
        self,
        relative_path: str,
        content: str,
        lines: Sequence[str],
    ) -> tuple[tuple[StructuralFact, ...], tuple[ScanWarning, ...]]:
        normalized_path = relative_path.replace("\\", "/")
        filename = normalized_path.split("/")[-1].lower()
        facts: list[StructuralFact] = []
        warnings: list[ScanWarning] = []

        try:
            if filename in ("requirements.txt", "requirements-dev.txt"):
                self._parse_requirements(normalized_path, lines, facts)
            elif filename == "pyproject.toml":
                self._parse_pyproject(normalized_path, content, facts, warnings)
            elif filename == "package.json":
                self._parse_package_json(normalized_path, content, facts, warnings)
            elif filename == "package-lock.json":
                self._parse_package_lock(normalized_path, content, facts, warnings)
            elif filename == "go.mod":
                self._parse_go_mod(normalized_path, lines, facts)
            elif filename == "cargo.toml":
                self._parse_cargo(normalized_path, content, facts, warnings)
            elif filename == "pom.xml":
                self._parse_pom(normalized_path, content, facts, warnings)
            elif filename.endswith(".csproj"):
                self._parse_csproj(normalized_path, content, facts, warnings)
        except Exception as exc:
            warnings.append(
                ScanWarning(
                    warning_type="MANIFEST_PARSE_ERROR",
                    message=f"Failed to parse manifest {normalized_path}: {type(exc).__name__}: {exc}",
                    file_path=normalized_path,
                )
            )

        return (tuple(facts), tuple(warnings))

    def _parse_requirements(
        self,
        source_path: str,
        lines: Sequence[str],
        facts: list[StructuralFact],
    ) -> None:
        deps: list[DeclaredDependency] = []
        is_dev = "dev" in source_path.lower()
        dep_type = "dev" if is_dev else "production"

        for idx, line in enumerate(lines, start=1):
            clean = line.strip()
            if not clean or clean.startswith("#") or clean.startswith("-"):
                continue
            clean = clean.split("#")[0].strip()
            match = self._REQUIREMENT_RE.match(clean)
            if match:
                pkg_name = match.group(1)
                ver_spec = match.group(2) or "*"
                deps.append(
                    DeclaredDependency(
                        name=pkg_name,
                        version_spec=ver_spec.strip(),
                        manifest_path=source_path,
                        dependency_type=dep_type,
                    )
                )
                facts.append(
                    StructuralFact.create(
                        fact_type=StructuralFactType.DEPENDENCY_DECLARED,
                        source_path=source_path,
                        line_start=idx,
                        line_end=idx,
                        extractor_id=self.extractor_id,
                        details={
                            "dependency_name": pkg_name,
                            "version_spec": ver_spec.strip(),
                            "dependency_type": dep_type,
                            "manifest_type": "python_pip",
                        },
                    )
                )

        facts.append(
            StructuralFact.create(
                fact_type=StructuralFactType.MANIFEST_INVENTORIED,
                source_path=source_path,
                line_start=1,
                line_end=len(lines) if lines else 1,
                extractor_id=self.extractor_id,
                details={
                    "manifest_type": "python_pip",
                    "dependency_count": len(deps),
                },
            )
        )

    def _parse_pyproject(
        self,
        source_path: str,
        content: str,
        facts: list[StructuralFact],
        warnings: list[ScanWarning],
    ) -> None:
        try:
            data = tomllib.loads(content)
        except Exception as exc:
            warnings.append(
                ScanWarning(
                    warning_type="PYPROJECT_TOML_SYNTAX_ERROR",
                    message=f"Invalid TOML in {source_path}: {exc}",
                    file_path=source_path,
                )
            )
            return

        deps: list[dict[str, Any]] = []

        # 1. PEP 621 [project.dependencies]
        project_table = data.get("project", {})
        if isinstance(project_table, dict):
            for req in project_table.get("dependencies", []):
                if isinstance(req, str):
                    clean = req.strip()
                    match = self._REQUIREMENT_RE.match(clean)
                    if match:
                        deps.append({
                            "name": match.group(1),
                            "version_spec": (match.group(2) or "*").strip(),
                            "type": "production",
                        })

            # Optional dependencies
            opt_deps = project_table.get("optional-dependencies", {})
            if isinstance(opt_deps, dict):
                for group, group_deps in opt_deps.items():
                    if isinstance(group_deps, list):
                        for req in group_deps:
                            if isinstance(req, str):
                                match = self._REQUIREMENT_RE.match(req.strip())
                                if match:
                                    deps.append({
                                        "name": match.group(1),
                                        "version_spec": (match.group(2) or "*").strip(),
                                        "type": f"optional_{group}",
                                    })

        # 2. Poetry [tool.poetry.dependencies]
        poetry_deps = data.get("tool", {}).get("poetry", {}).get("dependencies", {})
        if isinstance(poetry_deps, dict):
            for name, spec in poetry_deps.items():
                if name.lower() == "python":
                    continue
                ver = spec if isinstance(spec, str) else spec.get("version", "*") if isinstance(spec, dict) else "*"
                deps.append({
                    "name": name,
                    "version_spec": str(ver).strip(),
                    "type": "production",
                })

        for d in deps:
            facts.append(
                StructuralFact.create(
                    fact_type=StructuralFactType.DEPENDENCY_DECLARED,
                    source_path=source_path,
                    line_start=1,
                    line_end=1,
                    extractor_id=self.extractor_id,
                    details={
                        "dependency_name": d["name"],
                        "version_spec": d["version_spec"],
                        "dependency_type": d["type"],
                        "manifest_type": "python_pyproject",
                    },
                )
            )

        facts.append(
            StructuralFact.create(
                fact_type=StructuralFactType.MANIFEST_INVENTORIED,
                source_path=source_path,
                line_start=1,
                line_end=1,
                extractor_id=self.extractor_id,
                details={
                    "manifest_type": "python_pyproject",
                    "dependency_count": len(deps),
                },
            )
        )

    def _parse_package_json(
        self,
        source_path: str,
        content: str,
        facts: list[StructuralFact],
        warnings: list[ScanWarning],
    ) -> None:
        try:
            data = json.loads(content)
        except Exception as exc:
            warnings.append(
                ScanWarning(
                    warning_type="PACKAGE_JSON_SYNTAX_ERROR",
                    message=f"Invalid JSON in {source_path}: {exc}",
                    file_path=source_path,
                )
            )
            return

        if not isinstance(data, dict):
            return

        dep_groups = {
            "dependencies": "production",
            "devDependencies": "dev",
            "peerDependencies": "peer",
        }

        dep_count = 0
        for group_key, dep_type in dep_groups.items():
            section = data.get(group_key, {})
            if isinstance(section, dict):
                for name, ver in section.items():
                    dep_count += 1
                    facts.append(
                        StructuralFact.create(
                            fact_type=StructuralFactType.DEPENDENCY_DECLARED,
                            source_path=source_path,
                            line_start=1,
                            line_end=1,
                            extractor_id=self.extractor_id,
                            details={
                                "dependency_name": str(name),
                                "version_spec": str(ver),
                                "dependency_type": dep_type,
                                "manifest_type": "npm_package_json",
                            },
                        )
                    )

        facts.append(
            StructuralFact.create(
                fact_type=StructuralFactType.MANIFEST_INVENTORIED,
                source_path=source_path,
                line_start=1,
                line_end=1,
                extractor_id=self.extractor_id,
                details={
                    "manifest_type": "npm_package_json",
                    "package_name": data.get("name", ""),
                    "package_version": data.get("version", ""),
                    "dependency_count": dep_count,
                    "has_scripts": bool(data.get("scripts")),
                },
            )
        )

    def _parse_package_lock(
        self,
        source_path: str,
        content: str,
        facts: list[StructuralFact],
        warnings: list[ScanWarning],
    ) -> None:
        try:
            data = json.loads(content)
        except Exception:
            return
        if isinstance(data, dict):
            facts.append(
                StructuralFact.create(
                    fact_type=StructuralFactType.MANIFEST_INVENTORIED,
                    source_path=source_path,
                    line_start=1,
                    line_end=1,
                    extractor_id=self.extractor_id,
                    details={
                        "manifest_type": "npm_package_lock",
                        "lockfile_version": data.get("lockfileVersion", 0),
                    },
                )
            )

    def _parse_go_mod(
        self,
        source_path: str,
        lines: Sequence[str],
        facts: list[StructuralFact],
    ) -> None:
        in_require_block = False
        dep_count = 0
        module_name = ""

        for idx, line in enumerate(lines, start=1):
            clean = line.strip()
            if not clean or clean.startswith("//"):
                continue
            if clean.startswith("module "):
                module_name = clean.split()[1].strip()
                continue
            if clean == "require (":
                in_require_block = True
                continue
            if in_require_block and clean == ")":
                in_require_block = False
                continue

            if in_require_block or clean.startswith("require "):
                req_line = clean.removeprefix("require").strip()
                match = self._GO_REQUIRE_RE.match(req_line)
                if match:
                    dep_count += 1
                    pkg = match.group(1)
                    ver = match.group(2)
                    facts.append(
                        StructuralFact.create(
                            fact_type=StructuralFactType.DEPENDENCY_DECLARED,
                            source_path=source_path,
                            line_start=idx,
                            line_end=idx,
                            extractor_id=self.extractor_id,
                            details={
                                "dependency_name": pkg,
                                "version_spec": ver,
                                "dependency_type": "production",
                                "manifest_type": "go_mod",
                            },
                        )
                    )

        facts.append(
            StructuralFact.create(
                fact_type=StructuralFactType.MANIFEST_INVENTORIED,
                source_path=source_path,
                line_start=1,
                line_end=len(lines) if lines else 1,
                extractor_id=self.extractor_id,
                details={
                    "manifest_type": "go_mod",
                    "module_name": module_name,
                    "dependency_count": dep_count,
                },
            )
        )

    def _parse_cargo(
        self,
        source_path: str,
        content: str,
        facts: list[StructuralFact],
        warnings: list[ScanWarning],
    ) -> None:
        try:
            data = tomllib.loads(content)
        except Exception as exc:
            warnings.append(
                ScanWarning(
                    warning_type="CARGO_TOML_SYNTAX_ERROR",
                    message=f"Invalid TOML in {source_path}: {exc}",
                    file_path=source_path,
                )
            )
            return

        dep_count = 0
        dep_sections = {
            "dependencies": "production",
            "dev-dependencies": "dev",
            "build-dependencies": "build",
        }

        for sec_name, dep_type in dep_sections.items():
            sec = data.get(sec_name, {})
            if isinstance(sec, dict):
                for name, spec in sec.items():
                    dep_count += 1
                    ver = spec if isinstance(spec, str) else spec.get("version", "*") if isinstance(spec, dict) else "*"
                    facts.append(
                        StructuralFact.create(
                            fact_type=StructuralFactType.DEPENDENCY_DECLARED,
                            source_path=source_path,
                            line_start=1,
                            line_end=1,
                            extractor_id=self.extractor_id,
                            details={
                                "dependency_name": str(name),
                                "version_spec": str(ver),
                                "dependency_type": dep_type,
                                "manifest_type": "rust_cargo",
                            },
                        )
                    )

        facts.append(
            StructuralFact.create(
                fact_type=StructuralFactType.MANIFEST_INVENTORIED,
                source_path=source_path,
                line_start=1,
                line_end=1,
                extractor_id=self.extractor_id,
                details={
                    "manifest_type": "rust_cargo",
                    "package_name": data.get("package", {}).get("name", ""),
                    "package_version": data.get("package", {}).get("version", ""),
                    "dependency_count": dep_count,
                },
            )
        )

    def _parse_pom(
        self,
        source_path: str,
        content: str,
        facts: list[StructuralFact],
        warnings: list[ScanWarning],
    ) -> None:
        try:
            root = ET.fromstring(content)
        except Exception as exc:
            warnings.append(
                ScanWarning(
                    warning_type="POM_XML_SYNTAX_ERROR",
                    message=f"Invalid XML in {source_path}: {exc}",
                    file_path=source_path,
                )
            )
            return

        dep_count = 0
        # Iterate dependencies handling default maven namespace if present
        for elem in root.iter():
            if elem.tag.endswith("dependency"):
                group_id = ""
                artifact_id = ""
                version = "*"
                scope = "production"
                for child in elem:
                    tag = child.tag.split("}")[-1]
                    if tag == "groupId" and child.text:
                        group_id = child.text.strip()
                    elif tag == "artifactId" and child.text:
                        artifact_id = child.text.strip()
                    elif tag == "version" and child.text:
                        version = child.text.strip()
                    elif tag == "scope" and child.text:
                        scope = child.text.strip()

                if artifact_id:
                    dep_count += 1
                    dep_name = f"{group_id}:{artifact_id}" if group_id else artifact_id
                    facts.append(
                        StructuralFact.create(
                            fact_type=StructuralFactType.DEPENDENCY_DECLARED,
                            source_path=source_path,
                            line_start=1,
                            line_end=1,
                            extractor_id=self.extractor_id,
                            details={
                                "dependency_name": dep_name,
                                "version_spec": version,
                                "dependency_type": scope,
                                "manifest_type": "java_maven",
                            },
                        )
                    )

        facts.append(
            StructuralFact.create(
                fact_type=StructuralFactType.MANIFEST_INVENTORIED,
                source_path=source_path,
                line_start=1,
                line_end=1,
                extractor_id=self.extractor_id,
                details={
                    "manifest_type": "java_maven",
                    "dependency_count": dep_count,
                },
            )
        )

    def _parse_csproj(
        self,
        source_path: str,
        content: str,
        facts: list[StructuralFact],
        warnings: list[ScanWarning],
    ) -> None:
        try:
            root = ET.fromstring(content)
        except Exception as exc:
            warnings.append(
                ScanWarning(
                    warning_type="CSPROJ_XML_SYNTAX_ERROR",
                    message=f"Invalid XML in {source_path}: {exc}",
                    file_path=source_path,
                )
            )
            return

        dep_count = 0
        for elem in root.iter():
            tag = elem.tag.split("}")[-1]
            if tag == "PackageReference":
                name = elem.attrib.get("Include") or elem.attrib.get("Update")
                version = elem.attrib.get("Version") or "*"
                if name:
                    dep_count += 1
                    facts.append(
                        StructuralFact.create(
                            fact_type=StructuralFactType.DEPENDENCY_DECLARED,
                            source_path=source_path,
                            line_start=1,
                            line_end=1,
                            extractor_id=self.extractor_id,
                            details={
                                "dependency_name": name,
                                "version_spec": version,
                                "dependency_type": "production",
                                "manifest_type": "dotnet_csproj",
                            },
                        )
                    )

        facts.append(
            StructuralFact.create(
                fact_type=StructuralFactType.MANIFEST_INVENTORIED,
                source_path=source_path,
                line_start=1,
                line_end=1,
                extractor_id=self.extractor_id,
                details={
                    "manifest_type": "dotnet_csproj",
                    "dependency_count": dep_count,
                },
            )
        )
