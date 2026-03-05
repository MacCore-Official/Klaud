import google.generativeai as genai
import os
import json
import logging
import asyncio
from typing import Dict, Any

logger = logging.getLogger("Klaud.Neural")

class GeminiService:
    """
    KLAUD-NINJA NEURAL PROCESSING UNIT.
    Updated for 2026 stable Gemini-1.5-Flash protocols.
    """
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.critical("NEURAL CORE: GEMINI_API_KEY NOT FOUND.")
            self.model = None
            return

        genai.configure(api_key=api_key)
        
        # Using 1.5-Flash for speed and cost-efficiency
        self.model = genai.GenerativeModel(
            model_name='gemini-1.5-flash',
            generation_config={
                "temperature": 0.1,
                "response_mime_type": "application/json",
            }
        )
        logger.info("✅ NEURAL CORE: Gemini-1.5-Flash Initialized.")

    async def analyze_content(self, content: str, rules: str) -> Dict[str, Any]:
        """Scans messages for moderation violations."""
        if not self.model: return {"violation": False}

        prompt = (
            f"Rules: {rules}\n"
            "Analyze the content and return JSON: {'violation': bool, 'reason': str, 'severity': 'low'|'high'}"
        )
        
        try:
            response = await asyncio.wait_for(
                self.model.generate_content_async(f"{prompt}\n\nContent: {content}"),
                timeout=10.0
            )
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"Neural Moderation Error: {e}")
            return {"violation": False}

    async def parse_admin_intent(self, prompt: str) -> Dict[str, Any]:
        """Translates natural language into system commands."""
        if not self.model: return {"action": "none"}

        instr = (
            "Schema: {'action': str, 'count': int, 'base_name': str}\n"
            "Actions: ['create_channels', 'none']"
        )
        
        try:
            response = await asyncio.wait_for(
                self.model.generate_content_async(f"{instr}\n\nRequest: {prompt}"),
                timeout=12.0
            )
            return json.loads(response.text)
        except Exception as e:
            logger.error(f"Neural Intent Error: {e}")
            return {"action": "none"}

gemini_ai = GeminiService()
