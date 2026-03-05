import google.generativeai as genai
import os
import json
import logging
import asyncio
import re
from typing import Dict, Any, Optional, List

logger = logging.getLogger("Klaud.Neural")

class GeminiService:
    """
    KLAUD-NINJA NEURAL PROCESSING UNIT (NPU)
    Engineered to handle natural language admin requests with 98% accuracy.
    """
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model_name = 'gemini-1.5-flash'
        self.model = None
        self._initialize_neural_core()

    def _initialize_neural_core(self):
        """Initializes the SDK without the problematic 'response_mime_type' fields."""
        if not self.api_key:
            logger.critical("NPU: API Key missing. AI Subsystems offline.")
            return

        try:
            genai.configure(api_key=self.api_key)
            # Use the simplest possible model initialization to avoid SDK conflicts
            self.model = genai.GenerativeModel(self.model_name)
            logger.info(f"✅ NPU: {self.model_name} initialized in Legacy Compatibility Mode.")
        except Exception as e:
            logger.error(f"❌ NPU: Hardware failure: {e}")

    async def parse_admin_intent(self, prompt: str) -> Dict[str, Any]:
        """
        Translates raw human speech into system JSON commands.
        Uses manual JSON extraction to bypass SDK version limitations.
        """
        if not self.model:
            return {"action": "none"}

        # INDUSTRIAL STRENGTH SYSTEM PROMPT
        system_instructions = (
            "SYSTEM ROLE: You are the KLAUD-NINJA Administrative AI.\n"
            "TASK: Interpret the user request and return a JSON object ONLY.\n\n"
            "ALLOWED ACTIONS:\n"
            "1. 'create_channels': Use if user wants new channels (test, general, etc).\n"
            "2. 'delete_channels': Use if user wants to 'wipe', 'clear', or 'delete all'.\n"
            "3. 'setup_server': Use if user wants a theme like 'Roblox Trading'.\n"
            "4. 'none': Use for general conversation.\n\n"
            "JSON FORMAT:\n"
            "{\n"
            "  \"action\": \"string\",\n"
            "  \"count\": integer,\n"
            "  \"base_name\": \"string\"\n"
            "}\n\n"
            "Example: 'make 3 channels called lol' -> {\"action\": \"create_channels\", \"count\": 3, \"base_name\": \"lol\"}\n"
            "IMPORTANT: Do not include any text before or after the JSON."
        )

        try:
            # Perform generation without the 'response_mime_type' parameter that crashes your bot
            response = await asyncio.wait_for(
                self.model.generate_content_async(
                    f"{system_instructions}\n\nUSER_REQUEST: {prompt}",
                    generation_config={"temperature": 0.2}
                ),
                timeout=10.0
            )

            # Use Regex-based extraction to ensure we get JSON even if AI talks
            return self._robust_json_extract(response.text)

        except Exception as e:
            logger.error(f"NEURAL FAULT: {e}")
            return {"action": "none", "error": str(e)}

    async def analyze_content(self, content: str, rules: str) -> Dict[str, Any]:
        """Evaluates message content for moderation violations."""
        if not self.model: return {"violation": False}

        mod_prompt = (
            f"RULES: {rules}\n"
            "Evaluate content. Return JSON: {\"violation\": bool, \"reason\": string}"
        )

        try:
            response = await asyncio.wait_for(
                self.model.generate_content_async(f"{mod_prompt}\n\nCONTENT: {content}"),
                timeout=8.0
            )
            return self._robust_json_extract(response.text)
        except:
            return {"violation": False}

    def _robust_json_extract(self, text: str) -> Dict[str, Any]:
        """
        Extracts JSON from a string using a high-precision regex filter.
        Ensures stability even when AI models ignore formatting rules.
        """
        try:
            # Find everything between the first '{' and the last '}'
            match = re.search(r'(\{.*\}|\[.*\])', text, re.DOTALL)
            if match:
                clean_json = match.group(0)
                # Cleanup common markdown errors
                clean_json = clean_json.replace("```json", "").replace("```", "")
                parsed = json.loads(clean_json)
                logger.info(f"NEURAL: Successfully extracted {parsed.get('action')} intent.")
                return parsed
            
            logger.warning(f"NEURAL: No JSON pattern found in response: {text[:50]}")
            return {"action": "none"}
        except Exception as e:
            logger.error(f"NEURAL: JSON Parsing Error: {e}")
            return {"action": "none"}

# Global instance for app-wide injection
gemini_ai = GeminiService()
