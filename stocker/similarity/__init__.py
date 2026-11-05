"""SOX 국면 유사도 엔진. 계약: docs/DATA_CONTRACT.md 8절."""

from stocker.similarity.config import SimilarityConfig
from stocker.similarity.engine import Match, SimilarityResult, score
from stocker.similarity.features import (
    build_cycle_features,
    build_macro_features,
    build_price_features,
)
from stocker.similarity.loaders import run, walk_forward

__all__ = [
    "Match",
    "SimilarityConfig",
    "SimilarityResult",
    "build_cycle_features",
    "build_macro_features",
    "build_price_features",
    "run",
    "score",
    "walk_forward",
]
