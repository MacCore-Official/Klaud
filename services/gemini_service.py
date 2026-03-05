import google.generativeai as genai
import os
import json
import logging
import asyncio
from typing import Dict, Any, Optional

logger = logging.getLogger("Klaud.Neural")

class GeminiService:
    """
    KLAUD-NINJA NEURAL PROCESSING UNIT.
    Built for stable JSON-only output orchestration.
    """
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model_label = 'gemini-1.5-flash'
        self.model = None
        self._initialize()

    def _initialize(self):
        if not self.api_key:
            logger.critical("NPU: Missing API Key.")
            return

        try:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(
                model_name=self.model_label,
                generation_config={
                    "temperature": 0.1,
                    "response_mime_type": "application/json",
                }
            )
            logger.info(f"✅ NPU: Neural model {self.model_label} online.")
        except Exception as e:
            logger.error(f"❌ NPU: Init failure: {e}")

    async def analyze_content(self, content: str, rules: str) -> Dict[str, Any]:
        """Neural Moderation Scan."""
        if not self.model: return {"violation": False}

        prompt = (
            f"Rules: {rules}\n"
            "Analyze content. Return JSON: {'violation': bool, 'reason': str, 'severity': 'low'|'high'}"
        )
        
        try:
            response = await asyncio.wait_for(
                self.model.generate_content_async(f"{prompt}\n\nContent: {content}"),
                timeout=12.0
            )
            return self._sanitize(response.text)
        except Exception:
            return {"violation": False}

    async def parse_admin_intent(self, prompt: str) -> Dict[str, Any]:
        """Admin Command Translator."""
        if not self.model: return {"action": "none"}

        instr = (
            "Schema: {'action': str, 'count': int, 'base_name': str}\n"
            "Actions: ['create_channels', 'none']"
        )
        
        try:
            response = await asyncio.wait_for(
                self.model.generate_content_async(f"{instr}\n\nUser: {prompt}"),
                timeout=12.0
            )
            return self._sanitize(response.text)
        except Exception:
            return {"action": "none"}

    def _sanitize(self, raw_data: str) -> Dict[str, Any]:
        """Ensures the AI output is a valid Python dictionary."""
        try:
            data = raw_data.strip()
            if data.startswith("```"):
                data = data.split("```")[1]
                if data.startswith("json"): data = data[4:]
            return json.loads(data)
        except:
            return {}

gemini_ai = GeminiService()
