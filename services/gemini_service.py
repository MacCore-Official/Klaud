import google.generativeai as genai
import json
import re
from config import settings

genai.configure(api_key=settings.GEMINI_API_KEY)

class GeminiService:
    def __init__(self):
        self.model = genai.GenerativeModel('gemini-1.5-pro-latest')

    async def analyze_message(self, message: str, intensity: str, custom_prompt: str) -> dict:
        prompt = f"""
        You are Klaud, a highly advanced Discord moderation AI.
        Analyze this message. Determine if it contains toxicity, swearing, harassment, spam, scams, NSFW, threats, or hate speech.
        
        Moderation Intensity: {intensity.upper()} (relaxed = allow mild, extreme = strict block).
        Custom Admin Rules: {custom_prompt if custom_prompt else 'None. Follow standard safety.'}
        
        Message content: "{message}"
        
        Return ONLY valid JSON matching this schema exactly:
        {{
            "is_violating": boolean,
            "reason": "short explanation of why it violates or why it is clean",
            "suggested_action": "warn" | "delete" | "timeout" | "kick" | "ban" | "none"
        }}
        """
        try:
            response = await self.model.generate_content_async(prompt)
            return self._extract_json(response.text)
        except Exception:
            return {"is_violating": False, "reason": "AI Error", "suggested_action": "none"}

    async def parse_admin_command(self, user_instruction: str, server_context: str) -> dict:
        prompt = f"""
        You are Klaud, a Discord Server Architect AI. 
        Convert the following natural language instruction into executable JSON actions.
        Server Context: {server_context}
        Instruction: "{user_instruction}"
        
        Supported Action Types: create_category, create_channel, delete_channel, rename_channel, create_role, lock_channel, unlock_channel.
        
        Return ONLY valid JSON:
        {{
            "actions": [
                {{"type": "action_type", "name": "target_name", "category": "optional_parent_category"}}
            ]
        }}
        """
        try:
            response = await self.model.generate_content_async(prompt)
            return self._extract_json(response.text)
        except Exception:
            return {"actions": []}

    def _extract_json(self, text: str) -> dict:
        match = re.search(r'\{.*\}', text.replace('\n', ' '), re.IGNORECASE)
        if match:
            try:
                return json.loads(match.group(0))
            except:
                pass
        return {}
