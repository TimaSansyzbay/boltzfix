"""Prediction adapters. No adapter stores credentials or silently falls back."""

from .hosted import MockProvider, NvidiaProvider, NebiusProvider, choose_provider

__all__ = ["MockProvider", "NvidiaProvider", "NebiusProvider", "choose_provider"]
