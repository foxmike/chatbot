"""Retrieval confidence scoring module.

Provides mechanisms to compute relevance/confidence scores for retrieved memories
and filter out memories that fall below a specified threshold.
"""

from collections.abc import Iterable
from typing import Protocol

from chatbot.memory.retrieval import RetrievalResult


class ConfidenceScorer(Protocol):
    """Protocol for scoring the confidence/relevance of a retrieved memory."""
    
    def score(self, result: RetrievalResult) -> float:
        """Compute a confidence score for the given retrieval result.
        
        A higher score indicates greater confidence/relevance.
        """
        ...


class DefaultConfidenceScorer:
    """A baseline confidence scorer using the existing retrieval scores.
    
    This scorer computes confidence based on a weighted combination of the
    retrieval rank score and relevance derived from vector L2 distance.
    """
    
    def __init__(self, rank_weight: float = 0.5, vector_weight: float = 0.5) -> None:
        self.rank_weight = rank_weight
        self.vector_weight = vector_weight
        
    def score(self, result: RetrievalResult) -> float:
        """Calculate the confidence score based on rank and vector scores."""
        return (result.score * self.rank_weight) + ((1.0 / (1.0 + max(result.vector_score, 0.0))) * self.vector_weight)


def filter_low_confidence(
    results: Iterable[RetrievalResult],
    threshold: float = 0.5,
    scorer: ConfidenceScorer | None = None,
) -> list[RetrievalResult]:
    """Filter out memories that fall below the specified confidence threshold.
    
    Args:
        results: The retrieved memories to evaluate.
        threshold: The minimum confidence score required to keep a memory.
        scorer: The ConfidenceScorer to use. Defaults to DefaultConfidenceScorer.
        
    Returns:
        A list of RetrievalResult objects that meet or exceed the threshold,
        in their existing relevance-rank order.
    """
    if scorer is None:
        scorer = DefaultConfidenceScorer()
        
    scored_results = []
    for result in results:
        confidence = scorer.score(result)
        if confidence >= threshold:
            scored_results.append((confidence, result))
            
    # Eligibility must not reorder the relevance/recency ranking.
    return [result for _, result in scored_results]
