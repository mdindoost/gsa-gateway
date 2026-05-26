"""Intent detector — classifies incoming messages to route them through the system."""

import re
from typing import Optional

INTENT_FOOD = "food"
INTENT_GREETING = "greeting"
INTENT_CLEAR_HISTORY = "clear_history"
INTENT_QUESTION = "question"
INTENT_STATEMENT = "statement"
INTENT_THANKS = "thanks"
INTENT_HELP = "help"

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

CLEAR_PATTERNS = [
    r"\bclear\b",
    r"\breset\b",
    r"\bstart over\b",
    r"\bforget\b",
    r"\bnew conversation\b",
    r"\bstart fresh\b",
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

        # 2. Food
        for kw in FOOD_KEYWORDS:
            if kw in msg:
                return INTENT_FOOD, 1.0

        # 3. Greeting (short messages only)
        if len(msg) < 30:
            for pattern in GREETING_PATTERNS:
                if re.search(pattern, msg):
                    return INTENT_GREETING, 1.0

        # 4. Thanks (short messages only)
        if len(msg) < 50:
            for pattern in THANKS_PATTERNS:
                if re.search(pattern, msg):
                    return INTENT_THANKS, 1.0

        # 5. Help
        for pattern in HELP_PATTERNS:
            if re.search(pattern, msg):
                return INTENT_HELP, 1.0

        # 6. Question
        if msg.endswith("?"):
            return INTENT_QUESTION, 0.9
        first_word = msg.split()[0] if msg.split() else ""
        if first_word in _QUESTION_STARTERS:
            return INTENT_QUESTION, 0.9
        first_three = set(msg.split()[:3])
        if first_three & _QUESTION_STARTERS:
            return INTENT_QUESTION, 0.9

        # 7. Statement (still processed as question by RAG)
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
