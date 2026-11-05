"""Re-export of the synthetic corpus (lives in the package so the CI smoke eval can load it)."""

from jobscout.evaluation.synthetic_corpus import POSTINGS, load, rows

__all__ = ["POSTINGS", "load", "rows"]
