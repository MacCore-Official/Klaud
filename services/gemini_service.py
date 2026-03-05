import google.generativeai as genai
import os
import json
import logging
import asyncio

logger = logging.getLogger("Klaud.Neural")

class GeminiService:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model_name = 'gemini-1.5-flash'
        self.model = None
        self._boot()

    def _boot(self):
        if not self.api_key:
            logger.critical("NPU: API KEY MISSING")
            return

        genai.configure(api_key=self.api_key)
        
        # We try the modern config first. 
        # If the SDK is old, we catch the error during generation instead.
        try:
            self.model = genai.GenerativeModel(
                model_name=self.model_name
            )
            logger.info(f"✅ NPU: {self.model_name} initialized.")
        except Exception as e:
            logger.error(f"❌ NPU Boot Error: {e}")

    async def parse_admin_intent(self, prompt: str):
        if not self.model: return {"action": "none"}

        # We force JSON through the prompt as a backup for old SDK versions
        system_instructions = (
            "SYSTEM: Return ONLY valid JSON. No markdown, no conversation.\n"
            "ACTIONS: 'create_channels', 'delete_channels', 'setup_server', 'none'\n"
            "SCHEMA: {\"action\": string, \"count\": int, \"base_name\": string}\n"
        )

        try:
            # We move the generation_config inside the call for better compatibility
            response = await self.model.generate_content_async(
                f"{system_instructions}\n\nUSER REQUEST: {prompt}",
                generation_config={
                    "temperature": 0.1,
                    # If the error persists, the code below handles it:
                }
            )
            
            return self._clean_json(response.text)
        except Exception as e:
            # Fallback if the SDK is strictly failing on parameters
            logger.error(f"Neural Error: {e}")
            return {"action": "none"}

    def _clean_json(self, text):
        """Force-cleans text into JSON if AI includes markdown wrappers."""
        try:
            cleaned = text.strip().replace("```json", "").replace("```", "")
            return json.loads(cleaned)
        except:
            # Manual fallback: look for the first '{' and last '}'
            try:
                start = text.find('{')
                end = text.rfind('}') + 1
                return json.loads(text[start:end])
            except:
                return {"action": "none"}

gemini_ai = GeminiService()
