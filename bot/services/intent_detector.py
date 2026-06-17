"""Intent detector — classifies incoming messages to route them through the system."""

import re
from typing import Optional

INTENT_FOOD = "food"
INTENT_SOCIAL = "social"
INTENT_GREETING = "greeting"
INTENT_FAREWELL = "farewell"
INTENT_CLEAR_HISTORY = "clear_history"
INTENT_QUESTION = "question"
INTENT_STATEMENT = "statement"
INTENT_THANKS = "thanks"
INTENT_HELP = "help"
INTENT_IDENTITY = "identity"
INTENT_FREE_MODE = "free_mode"
INTENT_GSA_MODE = "gsa_mode"

GREETING_PATTERNS = [
    r"^hi\b",
    r"^hello\b",
    r"^hey\b",
    r"^howdy\b",
    r"^good morning",
    r"^good afternoon",
    r"^good evening",
    r"^what'?s up",
]

# Whole-message commands only. These are matched against the entire (normalized)
# message via the ^…$ anchors, so a question that merely *contains* "reset"/"clear"/
# "forget" — e.g. "how do I reset my NJIT password" or "did you forget my question" —
# does NOT wipe the conversation. Only a standalone clear command does.
CLEAR_PATTERNS = [
    r"^(?:please\s+)?(?:clear|reset|wipe|forget)"
    r"(?:\s+(?:the|this|our|my|all|everything))?"
    r"(?:\s+(?:conversation|chat|history|context|memory|session))?"
    r"(?:\s+please)?\s*[.!?]*$",
    r"^(?:let'?s\s+)?start\s+(?:over|fresh|again)\s*[.!?]*$",
    r"^(?:start\s+)?(?:a\s+)?new\s+(?:conversation|chat|session)\s*[.!?]*$",
]

THANKS_PATTERNS = [
    r"\bthank(?:s| you)\b",
    r"\bthanks?\b",
    r"\bthx\b",
    r"\bty\b",
    r"\bgreat\b",
    r"\bperfect\b",
    r"\bawesome\b",
    r"\bthat helps?\b",
    r"\bgot it\b",
]

FAREWELL_PATTERNS = [
    r"^bye\b",
    r"^goodbye\b",
    r"^good bye\b",
    r"^good night\b",
    r"^goodnight\b",
    r"^see you\b",
    r"^see ya\b",
    r"^later\b",
    r"^take care\b",
    r"^ciao\b",
    r"^adios\b",
    r"^farewell\b",
    r"^ttyl\b",
    r"^cu\b",
    r"^have a good",
    r"^have a nice",
    r"^خداحافظ",   # Persian
    r"^خدانگهدار",  # Persian (alternate)
    r"^hasta",      # Spanish (hasta luego / hasta pronto)
    r"^adiós",
    r"^tchau",      # Portuguese
    r"^hoşça",      # Turkish
    r"^güle",       # Turkish (güle güle)
    r"^अलविदा",    # Hindi
    r"^再见",       # Chinese
    r"^拜拜",       # Chinese (informal)
    r"^বিদায়",    # Bengali
]

HELP_PATTERNS = [
    r"^help$",
    r"^commands?$",
    r"what can you do",
    r"how do i use",
    r"what are your commands?",
]

FOOD_KEYWORDS = [
    "food", "free food", "snacks", "lunch", "dinner",
    "breakfast", "eat", "eating", "hungry", "pizza",
    "coffee", "drinks", "refreshments", "catering",
    "meal", "free lunch", "free snacks",
]

SOCIAL_KEYWORDS = [
    "fun", "social", "hangout", "hang out", "chill",
    "party", "meet people", "socialize", "activities",
    "networking", "mixer", "happy hour",
]

IDENTITY_PATTERNS = [
    r"who are you",
    r"what are you",
    r"what'?s your name",
    r"\byour name\b",
    r"tell me about yourself",
    r"are you (an? )?(chatgpt|gpt|ai|bot|llm)",
    r"what model are you",
    r"which (llm|model)",
    r"what (llm|language model)",
    r"how smart are you",
]

FREE_MODE_PATTERNS = [
    r"^free mode$",
    r"^!free$",
    r"^general mode$",
    r"^switch to free",
    r"^freemode$",
]

GSA_MODE_PATTERNS = [
    r"^gsa mode$",
    r"^!gsa$",
    r"^switch to gsa",
    r"^gsamode$",
]

_QUESTION_STARTERS = {
    "what", "how", "when", "where", "who", "why",
    "can", "is", "are", "do", "does", "will",
    "should", "could", "would",
}


class IntentDetector:
    def detect(self, message: str) -> tuple[str, float]:
        msg = re.sub(r'\s+', ' ', message.strip().lower())

        # 1. Clear history
        for pattern in CLEAR_PATTERNS:
            if re.search(pattern, msg):
                return INTENT_CLEAR_HISTORY, 1.0

        # 1b. Free mode / GSA mode toggle
        for pattern in FREE_MODE_PATTERNS:
            if re.search(pattern, msg):
                return INTENT_FREE_MODE, 1.0
        for pattern in GSA_MODE_PATTERNS:
            if re.search(pattern, msg):
                return INTENT_GSA_MODE, 1.0

        # 2. Food
        for kw in FOOD_KEYWORDS:
            if kw in msg:
                return INTENT_FOOD, 1.0

        # 3. Social / fun activities (short messages only, word boundary to avoid
        #    "fun" matching inside "funding", "function", etc.)
        if len(msg) < 40:
            for kw in SOCIAL_KEYWORDS:
                if re.search(rf'\b{re.escape(kw)}\b', msg):
                    return INTENT_SOCIAL, 1.0

        # 4. Greeting (short messages only)
        if len(msg) < 30:
            for pattern in GREETING_PATTERNS:
                if re.search(pattern, msg):
                    return INTENT_GREETING, 1.0

        # 4b. Farewell (short messages only)
        if len(msg) < 40:
            for pattern in FAREWELL_PATTERNS:
                if re.search(pattern, msg):
                    return INTENT_FAREWELL, 1.0

        # 5. Thanks (short messages only)
        if len(msg) < 50:
            for pattern in THANKS_PATTERNS:
                if re.search(pattern, msg):
                    return INTENT_THANKS, 1.0

        # 6. Help
        for pattern in HELP_PATTERNS:
            if re.search(pattern, msg):
                return INTENT_HELP, 1.0

        # 6b. Identity (after HELP so "what can you do" stays as HELP)
        for pattern in IDENTITY_PATTERNS:
            if re.search(pattern, msg):
                return INTENT_IDENTITY, 1.0

        # 7. Question
        if msg.endswith("?"):
            return INTENT_QUESTION, 0.9
        first_word = msg.split()[0] if msg.split() else ""
        if first_word in _QUESTION_STARTERS:
            return INTENT_QUESTION, 0.9
        first_three = set(msg.split()[:3])
        if first_three & _QUESTION_STARTERS:
            return INTENT_QUESTION, 0.9

        # 8. Statement (still processed as question by RAG)
        return INTENT_STATEMENT, 0.5

    def should_respond(
        self,
        message: str,
        channel_name: str,
        bot_was_mentioned: bool,
        ask_gsa_channel: str,
    ) -> bool:
        if channel_name == ask_gsa_channel:
            return True
        if bot_was_mentioned:
            return True
        return False

    def clean_message(
        self,
        message: str,
        bot_mention_string: Optional[str] = None,
    ) -> str:
        text = message
        if bot_mention_string:
            text = text.replace(bot_mention_string, "")
        # Remove @mentions
        text = re.sub(r'<@!?\d+>', '', text)
        # Remove #channel refs
        text = re.sub(r'<#\d+>', '', text)
        # Remove Discord formatting markers
        text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text)
        text = re.sub(r'_{1,3}(.+?)_{1,3}', r'\1', text)
        return text.strip()
