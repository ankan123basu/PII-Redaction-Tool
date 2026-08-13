"""Detectors module — pluggable PII detection via regex and NER."""

from pii_redactor.detectors.base import Detector, DetectedEntity

__all__ = ["Detector", "DetectedEntity"]
