"""Reverse-engineered client for chat.deepseek.com."""

from .client import DeepSeekWebClient
from .http import (
    ApiError,
    AuthError,
    ChallengeRequiredError,
    DeepSeekWebError,
    NetworkError,
    RateLimitError,
    TokenState,
)
from .pow import ALGORITHM, PowAnswer, PowChallenge, PowError, solve, solve_to_answer

__all__ = [
    "ALGORITHM",
    "ApiError",
    "AuthError",
    "ChallengeRequiredError",
    "DeepSeekWebClient",
    "DeepSeekWebError",
    "NetworkError",
    "RateLimitError",
    "PowAnswer",
    "PowChallenge",
    "PowError",
    "TokenState",
    "solve",
    "solve_to_answer",
]

__version__ = "0.1.0"

