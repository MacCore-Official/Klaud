import google.generativeai as genai
import os
import json
import logging
from typing import Dict, Any, Optional

# Senior-level logging configuration
logger = logging.getLogger("Klaud.Gemini")

class GeminiService:
    """
    KLAUD-NINJA NEURAL CORE.
    Handles all generative and analytical requests for the bot.
    """
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        if not self.api_key:
            logger.critical("GEMINI_API_KEY NOT FOUND. AI SERVICES DISABLED.")
            return
            
        genai.configure(api_key=self.api_key)
        # Using the most stable 2026 production model
        self.model = genai.GenerativeModel('gemini-1.5-flash')

    async def parse_admin_intent(self, prompt: str) -> Dict[str, Any]:
        """
        Translates human instructions into executable JSON schemas.
        """
        system_instructions = (
            "SYSTEM: KLAUD-NINJA_EXEC_OS_2026\n"
            "TASK: Intent Extraction\n"
            "OUTPUT: Valid JSON ONLY\n"
            "ACTIONS: \n"
            "- create_channels: {'action': 'create_channels', 'count': int, 'base_name': str}\n"
            "- purge_messages: {'action': 'purge', 'count': int}\n"
            "- none: {'action': 'none'}"
        )

        try:
            # We use a lower temperature for extraction to ensure JSON validity
            response = await self.model.generate_content_async(
                f"{system_instructions}\n\nUSER_REQUEST: {prompt}",
                generation_config=genai.types.GenerationConfig(temperature=0.1)
            )
            
            clean_json = response.text.strip().replace("```json", "").replace("```", "")
            return json.loads(clean_json)
        except Exception as e:
            logger.error(f"Intent Parsing Error: {e}")
            return {"action": "none"}

    async def analyze_content(self, content: str, rules: str) -> Dict[str, Any]:
        """
        Deep-packet inspection of message content for moderation violations.
        """
        mod_prompt = (
            f"MODERATION_KERNEL_V4\nRULES: {rules}\n"
            "OUTPUT_FORMAT: JSON\n"
            "FIELDS: violation (bool), reason (string), severity (low|medium|high), action (warn|timeout|kick)"
        )

        try:
            response = await self.model.generate_content_async(
                f"{mod_prompt}\n\nCONTENT_TO_SCAN: {content}",
                generation_config=genai.types.GenerationConfig(temperature=0.0)
            )
            clean_json = response.text.strip().replace("```json", "").replace("```", "")
            return json.loads(clean_json)
        except Exception as e:
            logger.error(f"Moderation Logic Error: {e}")
            return {"violation": False}

# Singleton instance for global app use
gemini_ai = GeminiService()
