"""
KLAUD-NINJA — Gemini Service (Compatibility Shim)
================================================================================
This module now delegates entirely to the Groq service.
All imports of GeminiService, ModerationDecision, ModerationAction,
AdminCommandDecision from this module continue to work unchanged.

The actual implementation lives in services/groq_service.py.
================================================================================
"""

from services.groq_service import (
    GroqService as GeminiService,
    ModerationAction,
    ModerationDecision,
    AdminCommandDecision,
)

__all__ = [
    "GeminiService",
    "ModerationAction",
    "ModerationDecision",
    "AdminCommandDecision",
]
