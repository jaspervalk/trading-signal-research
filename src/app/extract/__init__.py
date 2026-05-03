"""Hybrid call-extraction pipeline.

Pipeline (per Document):
    segments → consolidate → prefilter → context_window → llm_extractor
                                                              ↓
                                                         validator
                                                              ↓
                                                    confidence + persist
"""

from app.extract.confidence import compute_final_confidence, decide_status
from app.extract.prefilter import CandidateWindow, find_candidate_windows, score_window
from app.extract.schemas import LLMExtractedCall, NoCallFound
from app.extract.validator import ValidationResult, validate

__all__ = [
    "CandidateWindow",
    "LLMExtractedCall",
    "NoCallFound",
    "ValidationResult",
    "compute_final_confidence",
    "decide_status",
    "find_candidate_windows",
    "score_window",
    "validate",
]
