import google.generativeai as genai
import os
import json
import logging
from typing import Dict, Any

logger = logging.getLogger("Klaud.Gemini")

class GeminiService:
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY is missing!")
            return
            
        genai.configure(api_key=api_key)
        # Fix: Using the full model path to prevent 404 on v1beta/v1 endpoints
        self.model = genai.GenerativeModel('models/gemini-1.5-flash')

    async def parse_admin_intent(self, prompt: str) -> Dict[str, Any]:
        system_instructions = "Return ONLY JSON: {'action': 'create_channels', 'count': int, 'base_name': str} or {'action': 'none'}"
        try:
            # Using generate_content instead of generate_content_async if your env is strict,
            # but usually async is fine.
            response = await self.model.generate_content_async(f"{system_instructions}\n\nUser: {prompt}")
            raw_text = response.text.strip().replace("```json", "").replace("```", "")
            return json.loads(raw_text)
        except Exception as e:
            logger.error(f"Gemini Error: {e}")
            return {"action": "none"}

    async def analyze_content(self, content: str, rules: str) -> Dict[str, Any]:
        prompt = f"Rules: {rules}. Analyze and return JSON: {{'violation': bool, 'reason': str, 'severity': 'low'|'medium'|'high'}}"
        try:
            response = await self.model.generate_content_async(f"{prompt}\n\nContent: {content}")
            raw_text = response.text.strip().replace("```json", "").replace("```", "")
            return json.loads(raw_text)
        except Exception as e:
            logger.error(f"Moderation Error: {e}")
            return {"violation": False}

gemini_ai = GeminiService()
