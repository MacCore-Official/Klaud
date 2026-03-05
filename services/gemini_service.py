import google.generativeai as genai
import os
import json
import logging
import asyncio

logger = logging.getLogger("Klaud.Neural")

class GeminiService:
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        # We use a stable model name that exists in all versions
        self.model_label = 'gemini-1.5-flash'
        self.model = None
        self._boot()

    def _boot(self):
        if not self.api_key:
            logger.critical("NPU: Missing API Key.")
            return
        try:
            genai.configure(api_key=self.api_key)
            # Remove the GenerationConfig entirely to avoid the 'Unknown Field' crash
            self.model = genai.GenerativeModel(self.model_label)
            logger.info(f"✅ NPU: {self.model_label} online (Legacy Compatibility Mode).")
        except Exception as e:
            logger.error(f"❌ NPU: Hardware failure: {e}")

    async def parse_admin_intent(self, prompt: str):
        if not self.model: return {"action": "none"}

        # We move the JSON instructions into the prompt itself.
        # This is the 'Force-Fed' method. No config fields required.
        system_instructions = (
            "You are the KLAUD-NINJA Admin AI. You must respond ONLY with raw JSON.\n"
            "Do not talk. Do not explain. Just JSON.\n\n"
            "ACTIONS:\n"
            "- 'create_channels': (e.g., 'make a channel', 'new channel')\n"
            "- 'delete_channels': (e.g., 'wipe server', 'delete everything')\n"
            "- 'setup_server': (e.g., 'make a trading server')\n\n"
            "SCHEMA: {\"action\": \"string\", \"count\": 1, \"base_name\": \"string\"}\n"
        )

        try:
            # We call this WITHOUT the generation_config that was breaking your bot
            response = await self.model.generate_content_async(
                f"{system_instructions}\n\nUSER REQUEST: {prompt}"
            )
            
            # Clean and parse
            return self._manual_parse(response.text)
        except Exception as e:
            logger.error(f"NEURAL ERROR: {e}")
            return {"action": "none"}

    def _manual_parse(self, text):
        """Extracts JSON even if the AI wraps it in markdown blocks."""
        try:
            # Find the actual JSON content
            cleaned = text.strip().replace("```json", "").replace("```", "")
            data = json.loads(cleaned)
            logger.info(f"DETECTED ACTION: {data.get('action')}")
            return data
        except:
            # Last ditch effort: regex-style search for braces
            try:
                start = text.find('{')
                end = text.rfind('}') + 1
                return json.loads(text[start:end])
            except:
                logger.warning("FAILED TO PARSE AI JSON.")
                return {"action": "none"}

gemini_ai = GeminiService()
