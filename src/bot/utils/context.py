"""Conversation context management."""
from typing import List, Dict, Any
from collections import deque

import structlog
from sqlalchemy import select
from bot.database import get_session, Conversation

logger = structlog.get_logger()


class ConversationContext:
    """Manages conversation context for users."""
    
    # In-memory storage for active contexts
    _contexts: Dict[int, deque] = {}
    
    def __init__(self, user_id: int, model_name: str, max_length: int = 4096):
        """Initialize conversation context."""
        self.user_id = user_id
        self.model_name = model_name
        self.max_length = max_length
    
    async def get_context(self, message_limit: int = 10) -> List[Dict[str, str]]:
        """Get conversation context for the user."""
        # Check in-memory cache first
        if self.user_id in self._contexts:
            messages = list(self._contexts[self.user_id])
            return messages[-message_limit:] if len(messages) > message_limit else messages
        
        # Load from database
        async with get_session() as session:
            result = await session.execute(
                select(Conversation)
                .where(Conversation.user_id == self.user_id)
                .order_by(Conversation.created_at.desc())
                .limit(message_limit)
            )
            db_messages = result.scalars().all()
        
        # Convert to message format and reverse for chronological order
        messages = []
        for msg in reversed(db_messages):
            messages.append({
                "role": msg.message_role,
                "content": msg.message_content
            })
        
        # Store in cache
        self._contexts[self.user_id] = deque(messages, maxlen=message_limit * 2)
        
        return messages
    
    async def add_message(self, role: str, content: str) ->  List[Dict[str, str]]:
        """Add a message to the context and return the updated history."""
        message = {"role": role, "content": content}

        if self.user_id not in self._contexts:
            # Ensure existing history is loaded before appending a new message
            try:
                await self.get_context()
            except RuntimeError:
                logger.debug(
                    "Context database not initialized; using in-memory history only",
                    user_id=self.user_id,
                )
        
        if self.user_id not in self._contexts:
            self._contexts[self.user_id] = deque(maxlen=20)

        context_buffer = self._contexts[self.user_id]
        context_buffer.append(message)
        
        # Trim context if it's too long
        total_length = sum(len(m["content"]) for m in context_buffer)
        while total_length > self.max_length and len(context_buffer) > 2:
            context_buffer.popleft()
            total_length = sum(len(m["content"]) for m in context_buffer)

        return list(context_buffer)
    
    async def clear(self) -> None:
        """Clear the conversation context."""
        if self.user_id in self._contexts:
            del self._contexts[self.user_id]
        logger.info("Context cleared", user_id=self.user_id)
    
    @classmethod
    def clear_all(cls) -> None:
        """Clear all contexts (for shutdown)."""
        cls._contexts.clear()
        logger.info("All contexts cleared")


def normalize_chat_messages(messages: List[Any]) -> List[Dict[str, str]]:
    if not isinstance(messages, list):
        raise TypeError("messages must be a list")

    norm: List[Dict[str, str]] = []
    for m in messages:
        if isinstance(m, dict):
            role = m.get("role")
            content = m.get("content")
        elif isinstance(m, (list, tuple)) and len(m) == 2:
            role, content = m
        else:
            raise TypeError(f"Bad message item type: {type(m)} -> {m!r}")

        if not isinstance(role, str) or not isinstance(content, str):
            raise TypeError(f"role/content must be str, got: {type(role)}, {type(content)}")

        norm.append({"role": role, "content": content})
    return norm