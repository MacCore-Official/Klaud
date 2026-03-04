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
    KLAUD-NINJA NEURAL SERVICE v2.0
    Authoritative handler for Google Gemini AI interactions.
    """
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model_name = 'gemini-1.5-flash'
        self._initialize_sdk()
        
        # Configuration for deterministic JSON outputs
        self.generation_config = {
            "temperature": 0.2,
            "top_p": 0.9,
            "top_k": 32,
            "max_output_tokens": 2048,
            "response_mime_type": "application/json",
        }

    def _initialize_sdk(self):
        """Initializes the SDK and verifies API accessibility."""
        if not self.api_key:
            logger.critical("NEURAL CORE: API Key is missing. AI features will be offline.")
            self.model = None
            return

        try:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(
                model_name=self.model_name,
                generation_config=self.generation_config
            )
            logger.info(f"✅ NEURAL CORE: Initialized with model {self.model_name}")
        except Exception as e:
            logger.error(f"❌ NEURAL CORE: Initialization failed: {e}")
            self.model = None

    async def parse_admin_intent(self, prompt: str) -> Dict[str, Any]:
        """
        Translates raw human instruction into executable system JSON.
        """
        if not self.model:
            return {"action": "none", "error": "AI Offline"}

        system_context = (
            "SYSTEM: KLAUD-NINJA ADMIN_INTERFACE\n"
            "TASK: Convert user request to JSON command.\n"
            "SCHEMA: {'action': str, 'count': int, 'base_name': str, 'reasoning': str}\n"
            "ACTIONS: ['create_channels', 'purge_messages', 'none']"
        )

        try:
            # Wrapping the call in a timeout to prevent bot-wide hangs
            response = await asyncio.wait_for(
                self.model.generate_content_async(
                    f"{system_context}\n\nUSER_REQUEST: {prompt}"
                ),
                timeout=10.0
            )

            # High-level sanitation of the response string
            clean_json = self._sanitize_json(response.text)
            logger.info(f"NEURAL: Intent Parsed -> {clean_json.get('action')}")
            return clean_json

        except asyncio.TimeoutError:
            logger.error("NEURAL: Intent parsing timed out.")
            return {"action": "none", "error": "Timeout"}
        except Exception as e:
            logger.error(f"NEURAL: Parsing Error: {e}")
            return {"action": "none", "error": str(e)}

    async def analyze_content(self, content: str, rules: str) -> Dict[str, Any]:
        """
        Evaluates message content against guild-specific moderation rules.
        """
        if not self.model:
            return {"violation": False}

        mod_context = (
            "SYSTEM: KLAUD-NINJA MODERATION_ENGINE\n"
            f"GUILD_RULES: {rules}\n"
            "OUTPUT: JSON with ['violation' (bool), 'reason' (str), 'severity' (low/med/high)]"
        )

        try:
            response = await asyncio.wait_for(
                self.model.generate_content_async(
                    f"{mod_context}\n\nCONTENT_SCAN: {content}"
                ),
                timeout=8.0
            )

            result = self._sanitize_json(response.text)
            return result

        except Exception as e:
            logger.error(f"NEURAL: Moderation Scan Failure: {e}")
            return {"violation": False, "error": str(e)}

    def _sanitize_json(self, raw_text: str) -> Dict[str, Any]:
        """
        Cleans Markdown-wrapped JSON and handles parsing edge cases.
        """
        try:
            # Remove Markdown Code Blocks if present
            text = raw_text.strip()
            if text.startswith("```"):
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            
            return json.loads(text.strip())
        except Exception as e:
            logger.warning(f"NEURAL: JSON Sanitation failed on: {raw_text[:50]}... | Error: {e}")
            return {"action": "none", "violation": False}

# Singleton instance for global app usage
gemini_ai = GeminiService()
