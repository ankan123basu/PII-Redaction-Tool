"""
Abstract base class for PII detectors.

All detectors (regex-based and NER-based) implement this interface,
enabling the registry to treat them uniformly and the pipeline to
run them in a pluggable, extensible manner.

To add a new detector:
1. Subclass Detector
2. Implement detect(text) -> list[DetectedEntity]
3. Register it in the detector registry (or via entity_rules.yaml)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field


@dataclass(frozen=True)
class DetectedEntity:
    """A single detected PII span with full metadata.

    Attributes:
        text: The raw text that was matched.
        entity_type: The PII category (e.g., EMAIL, FULL_NAME, PHONE).
        start: Character offset of the match start within the input text.
        end: Character offset of the match end (exclusive).
        confidence: Detection confidence score (0.0 to 1.0).
        detector_name: Which detector produced this match (for overlap resolution).
        validated: Whether the match passed validation (e.g., Luhn for credit cards).
        metadata: Optional extra metadata (e.g., validator results, context info).
    """

    text: str
    entity_type: str
    start: int
    end: int
    confidence: float = 1.0
    detector_name: str = ""
    validated: bool = False
    metadata: dict = field(default_factory=dict)

    def overlaps_with(self, other: DetectedEntity) -> bool:
        """Check if this entity's span overlaps with another."""
        return self.start < other.end and other.start < self.end

    @property
    def span_length(self) -> int:
        """Length of the matched span in characters."""
        return self.end - self.start


class Detector(ABC):
    """Abstract base class for all PII detectors.

    Subclasses must implement detect() to return a list of
    DetectedEntity objects found in the input text.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable name for this detector (used in logs and reports)."""
        ...

    @property
    @abstractmethod
    def supported_entity_types(self) -> list[str]:
        """List of entity type strings this detector can produce."""
        ...

    @abstractmethod
    def detect(self, text: str) -> list[DetectedEntity]:
        """Detect PII entities in the given text.

        Args:
            text: Input text to scan for PII.

        Returns:
            List of DetectedEntity objects, possibly overlapping
            (overlap resolution happens downstream in the pipeline).
        """
        ...
