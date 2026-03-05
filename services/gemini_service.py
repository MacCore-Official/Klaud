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
    KLAUD-NINJA NEURAL PROCESSING UNIT (NPU) v6.0
    Redesigned with Aggressive Intent Detection.
    """
    def __init__(self):
        self.api_key = os.getenv("GEMINI_API_KEY")
        self.model_name = 'gemini-1.5-flash'
        self.model = None
        self._initialize_neural_core()

    def _initialize_neural_core(self):
        """Standard boot with legacy compatibility."""
        if not self.api_key:
            logger.critical("NPU: API Key missing.")
            return
        try:
            genai.configure(api_key=self.api_key)
            self.model = genai.GenerativeModel(self.model_name)
            logger.info(f"✅ NPU: {self.model_name} online (Aggressive Mode).")
        except Exception as e:
            logger.error(f"❌ NPU: Hardware failure: {e}")

    async def parse_admin_intent(self, prompt: str) -> Dict[str, Any]:
        """
        Translates raw speech into JSON. 
        Told to be EXTREMELY AGGRESSIVE in identifying commands.
        """
        if not self.model:
            return {"action": "none"}

        # THE "FORCEFUL" PROMPT
        # We tell the AI it is a 'System Executor', not a chatbot.
        system_instructions = (
            "SYSTEM: YOU ARE A COMMAND-LINE INTERPRETER. DO NOT CHAT.\n"
            "TASK: MAP THE USER REQUEST TO AN ACTION JSON.\n\n"
            "RULES:\n"
            "1. If user mentions 'make', 'create', 'new', or a name -> action: 'create_channels'\n"
            "2. If user mentions 'wipe', 'delete', 'clear', 'purge' -> action: 'delete_channels'\n"
            "3. If user mentions 'trading', 'roblox', 'setup' -> action: 'setup_server'\n"
            "4. DEFAULT TO 'create_channels' IF THEY GIVE A NAME.\n\n"
            "OUTPUT FORMAT (STRICT JSON ONLY):\n"
            "{\n"
            "  \"action\": \"create_channels\" | \"delete_channels\" | \"setup_server\" | \"none\",\n"
            "  \"count\": integer,\n"
            "  \"base_name\": \"string\"\n"
            "}\n\n"
            "USER REQUEST: "
        )

        try:
            # We use a higher temperature (0.7) to allow the AI to be more 'creative' with matching
            response = await asyncio.wait_for(
                self.model.generate_content_async(
                    f"{system_instructions} '{prompt}'",
                    generation_config={"temperature": 0.7}
                ),
                timeout=12.0
            )

            # Extract and log what the AI actually said
            raw_text = response.text
            logger.info(f"NEURAL RAW OUTPUT: {raw_text}")
            
            parsed = self._robust_json_extract(raw_text)
            
            # EMERGENCY FALLBACK: If user said 'test' and AI failed, we force it.
            if "test" in prompt.lower() and parsed.get('action') == 'none':
                return {"action": "create_channels", "count": 1, "base_name": "test"}
                
            return parsed

        except Exception as e:
            logger.error(f"NEURAL FAULT: {e}")
            return {"action": "none"}

    def _robust_json_extract(self, text: str) -> Dict[str, Any]:
        """Deep cleaning of AI text into usable JSON."""
        try:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                clean_json = match.group(0).replace("```json", "").replace("```", "")
                return json.loads(clean_json)
            return {"action": "none"}
        except:
            return {"action": "none"}

gemini_ai = GeminiService()
