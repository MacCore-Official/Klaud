import google.generativeai as genai
import os
import json
import logging
from typing import Dict, Any

logger = logging.getLogger("Klaud.Gemini")

class GeminiService:
    """
    Advanced Intent Analysis Service.
    Uses Gemini-1.5-Pro to interpret natural language into Discord Actions.
    """
    def __init__(self):
        api_key = os.getenv("GEMINI_API_KEY")
        if not api_key:
            logger.error("GEMINI_API_KEY is missing!")
            return
            
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-1.5-pro')

    async def parse_admin_intent(self, prompt: str) -> Dict[str, Any]:
        """
        Determines if a user wants to perform an administrative action.
        Must return valid JSON.
        """
        system_instructions = (
            "You are KLAUD-NINJA AI. You translate user requests into JSON actions. "
            "Available Actions: 'create_channels', 'none'. "
            "For 'create_channels', include 'count' (int) and 'base_name' (str). "
            "Only return raw JSON. No conversational text."
        )

        try:
            # Use safety settings to ensure the AI doesn't refuse harmless admin tasks
            response = await self.model.generate_content_async(
                f"{system_instructions}\n\nUser Request: {prompt}"
            )
            
            # Clean up the response for JSON parsing
            raw_text = response.text.strip().replace("```json", "").replace("```", "")
            return json.loads(raw_text)
        except Exception as e:
            logger.error(f"Gemini Intent Parsing Failed: {e}")
            return {"action": "none"}

gemini_ai = GeminiService()
