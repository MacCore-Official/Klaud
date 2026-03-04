import google.generativeai as genai
from google.generativeai.types import RequestOptions
import os
import json
import logging
from typing import Dict, Any

logger = logging.getLogger("Klaud.Gemini")

class GeminiService:
    """
    KLAUD-NINJA NEURAL INTERFACE.
    Updated for 2026 Stable API compatibility.
    """
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.critical("AI CORE: GEMINI_API_KEY is missing from environment.")
            return
            
        genai.configure(api_key=api_key)
        
        # FIX: Using the specific stable model version to avoid 404
        # 'gemini-1.5-flash' is the high-speed production model for 2026.
        self.model = genai.GenerativeModel(
            model_name='gemini-1.5-flash',
            generation_config={
                "temperature": 0.1,
                "top_p": 0.95,
                "top_k": 40,
                "max_output_tokens": 1024,
                "response_mime_type": "application/json",
            }
        )

    async def parse_admin_intent(self, prompt: str) -> Dict[str, Any]:
        """
        Translates natural language into JSON instructions.
        """
        system_instructions = (
            "You are the KLAUD-NINJA Core. Analyze the user request. "
            "Return ONLY a JSON object with: 'action', 'count', 'base_name'. "
            "Actions: 'create_channels', 'none'."
        )

        try:
            # We use request_options to force the stable v1 API if v1beta is failing
            response = await self.model.generate_content_async(
                f"{system_instructions}\n\nUser Request: {prompt}",
                request_options=RequestOptions(retry=None)
            )
            
            # Clean and load the JSON
            raw_text = response.text.strip().replace("```json", "").replace("```", "")
            return json.loads(raw_text)
        except Exception as e:
            logger.error(f"Intent Error: {e}")
            return {"action": "none"}

    async def analyze_content(self, content: str, rules: str) -> Dict[str, Any]:
        """
        Deep behavioral analysis for Moderation.
        """
        system_instructions = (
            f"Rule Set: {rules}\n"
            "Return JSON: {'violation': bool, 'reason': str, 'severity': 'low'|'medium'|'high'}"
        )

        try:
            response = await self.model.generate_content_async(
                f"{system_instructions}\n\nContent: {content}"
            )
            raw_text = response.text.strip().replace("```json", "").replace("```", "")
            return json.loads(raw_text)
        except Exception as e:
            logger.error(f"Analysis Error: {e}")
            return {"violation": False}

# Global singleton
gemini_ai = GeminiService()
