"""Reverse-engineered client for chat.deepseek.com."""

from .client import DeepSeekWebClient
from .http import ApiError, AuthError, DeepSeekWebError, NetworkError, TokenState
from .pow import ALGORITHM, PowAnswer, PowChallenge, PowError, solve, solve_to_answer

__all__ = [
    "ALGORITHM",
    "ApiError",
    "AuthError",
    "DeepSeekWebClient",
    "DeepSeekWebError",
    "NetworkError",
    "PowAnswer",
    "PowChallenge",
    "PowError",
    "TokenState",
    "solve",
    "solve_to_answer",
]

__version__ = "0.1.0"
