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
            logger.critical("GEMINI_API_KEY is missing!")
            return
            
        genai.configure(api_key=api_key)
        # Fix: Full path string for the 2026 production model
        self.model = genai.GenerativeModel('models/gemini-1.5-flash')

    async def parse_admin_intent(self, prompt: str) -> Dict[str, Any]:
        instr = "Task: Extract intent. Return RAW JSON ONLY: {'action': 'create_channels', 'count': int, 'base_name': str}"
        try:
            res = await self.model.generate_content_async(f"{instr}\n\nUser: {prompt}")
            return json.loads(res.text.strip().replace("```json", "").replace("```", ""))
        except Exception as e:
            logger.error(f"Intent Error: {e}")
            return {"action": "none"}

    async def analyze_content(self, content: str, rules: str) -> Dict[str, Any]:
        instr = f"Rules: {rules}\nReturn JSON: {{'violation': bool, 'reason': str, 'severity': 'low'|'medium'|'high'}}"
        try:
            res = await self.model.generate_content_async(f"{instr}\n\nContent: {content}")
            return json.loads(res.text.strip().replace("```json", "").replace("```", ""))
        except Exception as e:
            logger.error(f"Analysis Error: {e}")
            return {"violation": False}

gemini_ai = GeminiService()
