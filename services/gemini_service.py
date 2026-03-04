import google.generativeai as genai
import os
import json
import logging
from typing import Dict, Any

logger = logging.getLogger("Klaud.Gemini")

class GeminiService:
    """
    KLAUD-NINJA NEURAL INTERFACE.
    Handles Intent Parsing and Content Analysis.
    """
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("AI Core: Missing API Key.")
            return
            
        genai.configure(api_key=api_key)
        # Fix: Using the strictly verified 2026 model path
        self.model = genai.GenerativeModel('models/gemini-1.5-flash-latest')

    async def analyze_content(self, content: str, rules: str) -> Dict[str, Any]:
        """Deep analysis for Moderation."""
        prompt = (
            f"SYSTEM: Moderator. RULES: {rules}\n"
            "Analyze and return RAW JSON: {'violation': bool, 'reason': str, 'severity': 'low'|'medium'|'high'}"
        )
        try:
            # We use generation_config to force JSON response
            response = await self.model.generate_content_async(
                f"{prompt}\n\nContent: {content}",
                generation_config={"response_mime_type": "application/json"}
            )
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"AI Analysis Failure: {e}")
            return {"violation": False}

    async def parse_admin_intent(self, prompt: str) -> Dict[str, Any]:
        """Admin Command Translation."""
        instr = "Return JSON: {'action': 'create_channels', 'count': int, 'base_name': str} or {'action': 'none'}"
        try:
            response = await self.model.generate_content_async(
                f"{instr}\n\nUser: {prompt}",
                generation_config={"response_mime_type": "application/json"}
            )
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"AI Intent Failure: {e}")
            return {"action": "none"}

gemini_ai = GeminiService()
