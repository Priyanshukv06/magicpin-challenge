from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from threading import RLock
from typing import Any


SCOPES = ("category", "merchant", "customer", "trigger")


@dataclass(frozen=True)
class ContextRecord:
    version: int
    payload: dict[str, Any]


@dataclass
class Conversation:
    conversation_id: str
    merchant_id: str | None
    customer_id: str | None
    trigger_id: str | None = None
    last_body: str = ""
    status: str = "active"
    turn_count: int = 0


@dataclass
class MemoryStore:
    contexts: dict[str, dict[str, ContextRecord]] = field(
        default_factory=lambda: {scope: {} for scope in SCOPES}
    )
    conversations: dict[str, Conversation] = field(default_factory=dict)
    suppression_keys: set[str] = field(default_factory=set)
    blocked_merchants: set[str] = field(default_factory=set)
    blocked_customers: set[str] = field(default_factory=set)
    auto_reply_counts: dict[str, int] = field(default_factory=dict)
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    lock: RLock = field(default_factory=RLock)

    def put_context(
        self, scope: str, context_id: str, version: int, payload: dict[str, Any]
    ) -> tuple[bool, int | None]:
        """Replace a context only when its version increases."""
        with self.lock:
            current = self.contexts[scope].get(context_id)
            if current is not None and version <= current.version:
                return False, current.version
            self.contexts[scope][context_id] = ContextRecord(version, payload.copy())
            return True, None

    def get(self, scope: str, context_id: str | None) -> ContextRecord | None:
        if not context_id:
            return None
        with self.lock:
            return self.contexts[scope].get(context_id)

    def counts(self) -> dict[str, int]:
        with self.lock:
            return {scope: len(values) for scope, values in self.contexts.items()}

    def reserve_action(
        self, suppression_key: str, conversation: Conversation
    ) -> bool:
        """Atomically suppress duplicates and register a new conversation."""
        with self.lock:
            if suppression_key in self.suppression_keys:
                return False
            if conversation.conversation_id in self.conversations:
                return False
            self.suppression_keys.add(suppression_key)
            self.conversations[conversation.conversation_id] = conversation
            return True

    def conversation(self, conversation_id: str) -> Conversation | None:
        with self.lock:
            return self.conversations.get(conversation_id)

    def ensure_conversation(
        self,
        conversation_id: str,
        merchant_id: str | None,
        customer_id: str | None,
    ) -> Conversation:
        with self.lock:
            conversation = self.conversations.get(conversation_id)
            if conversation is None:
                conversation = Conversation(conversation_id, merchant_id, customer_id)
                self.conversations[conversation_id] = conversation
            return conversation

    def block_merchant(self, merchant_id: str | None) -> None:
        if not merchant_id:
            return
        with self.lock:
            self.blocked_merchants.add(merchant_id)

    def is_blocked(self, merchant_id: str | None) -> bool:
        with self.lock:
            return bool(merchant_id and merchant_id in self.blocked_merchants)

    def block_customer(self, customer_id: str | None) -> None:
        if not customer_id:
            return
        with self.lock:
            self.blocked_customers.add(customer_id)

    def is_customer_blocked(self, customer_id: str | None) -> bool:
        with self.lock:
            return bool(customer_id and customer_id in self.blocked_customers)

    def note_auto_reply(self, merchant_id: str | None) -> int:
        key = merchant_id or "unknown"
        with self.lock:
            count = self.auto_reply_counts.get(key, 0) + 1
            self.auto_reply_counts[key] = count
            return count


store = MemoryStore()
