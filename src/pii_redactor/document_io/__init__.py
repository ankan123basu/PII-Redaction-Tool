"""Document I/O module — structure-preserving DOCX reading and writing."""

from pii_redactor.document_io.docx_reader import DocxReader, TextSegment
from pii_redactor.document_io.docx_writer import DocxWriter

__all__ = ["DocxReader", "DocxWriter", "TextSegment"]
