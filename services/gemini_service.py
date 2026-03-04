import google.generativeai as genai
import os
import json
import logging
import asyncio
import datetime
from typing import Dict, Any, Optional, List, Union

logger = logging.getLogger("Klaud.Neural")

class GeminiService:
    """
    KLAUD-NINJA NEURAL PROCESSING UNIT (NPU).
    Handles all AI-driven data interpretation and moderation logic.
    """
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model_label = 'gemini-1.5-flash'
        self.model = None
        self._boot_neural_service()

    def _boot_neural_service(self):
        """Initializes the AI interface with stable 2026 settings."""
        if not self.api_key:
            logger.critical("NPU: API KEY NOT FOUND. AI OFFLINE.")
            return

        try:
            genai.configure(api_key=self.api_key)
            
            # CONFIGURATION BLOCK
            # This replaces the need for RequestOptions imports.
            self.model = genai.GenerativeModel(
                model_name=self.model_label,
                generation_config={
                    "temperature": 0.15,
                    "top_p": 0.95,
                    "max_output_tokens": 1500,
                    "response_mime_type": "application/json",
                }
            )
            logger.info(f"✅ NPU: Neural model {self.model_label} loaded.")
        except Exception as e:
            logger.error(f"❌ NPU: Initialization failure: {e}")

    async def analyze_content(self, content: str, rules: str) -> Dict[str, Any]:
        """Scans message content for violations based on guild rules."""
        if not self.model:
            return {"violation": False, "error": "AI_OFFLINE"}

        instruction = (
            f"You are the KLAUD-NINJA Moderation Engine. RULES: {rules}\n"
            "Analyze the content and return JSON: "
            "{'violation': bool, 'reason': str, 'severity': 'low'|'medium'|'high'}"
        )

        try:
            # Async generation with timeout protection
            response = await asyncio.wait_for(
                self.model.generate_content_async(
                    f"{instruction}\n\nCONTENT: {content}"
                ),
                timeout=12.0
            )
            
            return self._parse_and_sanitize(response.text)

        except asyncio.TimeoutError:
            logger.error("NPU: Moderation scan timed out.")
            return {"violation": False, "error": "TIMEOUT"}
        except Exception as e:
            logger.error(f"NPU: Scan failed: {e}")
            return {"violation": False, "error": str(e)}

    async def parse_admin_intent(self, prompt: str) -> Dict[str, Any]:
        """Translates natural language into administrative JSON commands."""
        if not self.model:
            return {"action": "none"}

        instruction = (
            "You are the KLAUD-NINJA Administrative Interface.\n"
            "Convert user requests into system commands.\n"
            "RETURN JSON: {'action': str, 'count': int, 'base_name': str}\n"
            "ACTIONS: ['create_channels', 'purge', 'none']"
        )

        try:
            response = await asyncio.wait_for(
                self.model.generate_content_async(
                    f"{instruction}\n\nUSER_REQUEST: {prompt}"
                ),
                timeout=10.0
            )
            return self._parse_and_sanitize(response.text)

        except Exception as e:
            logger.error(f"NPU: Intent parsing failure: {e}")
            return {"action": "none"}

    def _parse_and_sanitize(self, raw_data: str) -> Dict[str, Any]:
        """Cleans AI output to ensure strictly valid JSON dictionary."""
        try:
            # Handle potential markdown wrapping
            cleaned = raw_data.strip()
            if "```" in cleaned:
                cleaned = cleaned.split("```")[1]
                if cleaned.startswith("json"):
                    cleaned = cleaned[4:]
            
            return json.loads(cleaned)
        except Exception as e:
            logger.warning(f"NPU: Sanitization failed on: {raw_data[:40]}...")
            return {"error": "PARSING_FAILURE"}

# Initialize singleton
gemini_ai = GeminiService()
