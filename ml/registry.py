"""Versioned ML model registry.

Registry metadata is separate from model execution. Only artifacts explicitly
marked eligible_for_paper can be selected for paper trading.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from ml.artifacts import ModelArtifact


@dataclass(frozen=True)
class RegistryEntry:
    artifact: ModelArtifact
    status: str = "research"

    def __post_init__(self):
        allowed = {"research", "paper", "retired"}
        if self.status not in allowed:
            raise ValueError(f"invalid registry status: {self.status}")
        if self.status == "paper" and not self.artifact.eligible_for_paper:
            raise ValueError("only paper-eligible artifacts may be promoted")


class ModelRegistry:
    def __init__(self, entries: Iterable[RegistryEntry] = ()):
        self._entries = {entry.artifact.model_id: entry for entry in entries}

    def add(self, artifact: ModelArtifact) -> RegistryEntry:
        if artifact.model_id in self._entries:
            raise ValueError(f"model already registered: {artifact.model_id}")
        entry = RegistryEntry(artifact)
        self._entries[artifact.model_id] = entry
        return entry

    def promote_to_paper(self, model_id: str) -> RegistryEntry:
        entry = self.get(model_id)
        if not entry.artifact.eligible_for_paper:
            raise ValueError("artifact has not passed paper eligibility gates")
        updated = RegistryEntry(entry.artifact, "paper")
        self._entries[model_id] = updated
        return updated

    def retire(self, model_id: str) -> RegistryEntry:
        entry = self.get(model_id)
        updated = RegistryEntry(entry.artifact, "retired")
        self._entries[model_id] = updated
        return updated

    def get(self, model_id: str) -> RegistryEntry:
        try:
            return self._entries[model_id]
        except KeyError as exc:
            raise KeyError(f"unknown model: {model_id}") from exc

    def list(self, status: str | None = None) -> list[RegistryEntry]:
        entries = list(self._entries.values())
        if status is not None:
            entries = [entry for entry in entries if entry.status == status]
        return sorted(entries, key=lambda entry: entry.artifact.created_at)
