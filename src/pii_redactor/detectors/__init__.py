"""Detectors module — pluggable PII detection via regex and NER."""

from pii_redactor.detectors.base import DetectedEntity, Detector

__all__ = ["Detector", "DetectedEntity"]
