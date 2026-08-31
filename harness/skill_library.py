"""Static, embodiment-neutral robot skill catalogue.

The library deliberately separates two kinds of data:

* ``skill-library/catalog/<skill>/contract.toml`` describes what a skill means;
* ``skill-library/embodiments/<name>.toml`` maps that meaning to a benchmark's
  lower-level sub-task names.

This mirrors open-robot-skills' one-directory-per-skill layout while keeping the
machine contract in TOML, which the Python standard library parses without a
second YAML dependency.  ``SKILL.md`` beside every contract is the human / VLM
explanation; execution never scrapes prose for authority.
"""

from __future__ import annotations

import string
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent / "skill-library"
_TYPES: dict[str, type] = {"str": str, "int": int, "float": float, "bool": bool}


@dataclass(frozen=True)
class SkillContract:
    name: str
    description: str
    kind: str
    arguments: Mapping[str, type]
    requires: tuple[str, ...]
    ensures: tuple[str, ...]
    exit_conditions: tuple[str, ...]
    doc: str

    def planner_doc(self) -> dict[str, Any]:
        return {
            "description": self.description,
            "kind": self.kind,
            "arguments": {k: v.__name__ for k, v in self.arguments.items()},
            "requires": list(self.requires),
            "ensures": list(self.ensures),
            "exit_conditions": list(self.exit_conditions),
        }


@dataclass(frozen=True)
class SkillBinding:
    skill: str
    embodiment: str
    task_template: str
    backend: str
    implemented: bool

    @property
    def template_fields(self) -> tuple[str, ...]:
        return tuple(field for _, field, _, _ in
                     string.Formatter().parse(self.task_template) if field)


class SkillLibrary:
    def __init__(self, root: Path = ROOT) -> None:
        self.root = Path(root)
        self._contracts = self._load_contracts()
        self._bindings: dict[str, dict[str, SkillBinding]] = {}

    def _load_contracts(self) -> dict[str, SkillContract]:
        out: dict[str, SkillContract] = {}
        for path in sorted((self.root / "catalog").glob("*/contract.toml")):
            raw = tomllib.loads(path.read_text())
            spec = raw["skill"]
            name = str(spec["name"])
            if name in out:
                raise ValueError(f"duplicate static skill {name!r}")
            args: dict[str, type] = {}
            for arg, type_name in raw.get("arguments", {}).items():
                if type_name not in _TYPES:
                    raise ValueError(
                        f"skill {name!r} arg {arg!r} has unknown type {type_name!r}")
                args[arg] = _TYPES[type_name]
            doc_path = path.with_name("SKILL.md")
            out[name] = SkillContract(
                name=name,
                description=str(spec["description"]),
                kind=str(spec.get("kind", "segment")),
                arguments=args,
                requires=tuple(spec.get("requires", ())),
                ensures=tuple(spec.get("ensures", ())),
                exit_conditions=tuple(spec.get("exit_conditions", ())),
                doc=doc_path.read_text() if doc_path.exists() else "",
            )
        if not out:
            raise ValueError(f"no static skills found under {self.root / 'catalog'}")
        return out

    def bindings(self, embodiment: str, *, implemented_only: bool = True
                 ) -> Mapping[str, SkillBinding]:
        if embodiment not in self._bindings:
            path = self.root / "embodiments" / f"{embodiment}.toml"
            if not path.exists():
                raise KeyError(f"unknown skill-library embodiment {embodiment!r}")
            raw = tomllib.loads(path.read_text())
            found: dict[str, SkillBinding] = {}
            for name, spec in raw.get("skills", {}).items():
                if name not in self._contracts:
                    raise ValueError(
                        f"{path}: binding names unknown abstract skill {name!r}")
                binding = SkillBinding(
                    skill=name,
                    embodiment=embodiment,
                    task_template=str(spec["task_template"]),
                    backend=str(spec["backend"]),
                    implemented=bool(spec.get("implemented", True)),
                )
                missing = set(binding.template_fields) - set(
                    self._contracts[name].arguments)
                if missing:
                    raise ValueError(
                        f"{path}: {name!r} task_template uses undeclared args "
                        f"{sorted(missing)}")
                found[name] = binding
            self._bindings[embodiment] = found
        bindings = self._bindings[embodiment]
        return ({k: v for k, v in bindings.items() if v.implemented}
                if implemented_only else dict(bindings))

    def select(self, embodiment: str, names: Iterable[str]) -> tuple[SkillContract, ...]:
        available = self.bindings(embodiment)
        selected = []
        for name in names:
            if name not in self._contracts:
                raise KeyError(f"unknown abstract skill {name!r}")
            if name not in available:
                raise ValueError(
                    f"skill {name!r} has no implemented {embodiment!r} binding")
            selected.append(self._contracts[name])
        return tuple(selected)

    def catalogue(self, embodiment: str, names: Iterable[str]) -> dict[str, dict[str, type]]:
        return {s.name: dict(s.arguments) for s in self.select(embodiment, names)}

    def planner_docs(self, embodiment: str, names: Iterable[str]) -> dict[str, dict]:
        return {s.name: s.planner_doc() for s in self.select(embodiment, names)}

    def segment_specs(self, embodiment: str, names: Iterable[str]) -> dict[str, dict]:
        bindings = self.bindings(embodiment)
        self.select(embodiment, names)  # validates the requested surface
        return {name: {"task_template": bindings[name].task_template}
                for name in names}


LIBRARY = SkillLibrary()
