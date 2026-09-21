from datetime import datetime
from dataclasses import replace

from aimemory.models import Message, NormalizedConversation
from aimemory.sources import CODEX_SOURCES


NORMALIZATION_VERSION = 1


def deduplicate_messages(messages: list[Message]) -> list[Message]:
    """Collapse adjacent event/response mirrors, never repeated same-kind messages."""
    result = []
    paired = False
    for message in messages:
        previous = result[-1] if result else None
        mirror = False
        if (previous and not paired and message.role in {"user", "assistant"}
                and not previous.metadata.get("codex_mirror_collapsed")
                and not message.metadata.get("codex_mirror_collapsed")):
            types = {previous.metadata.get("source_type"), message.metadata.get("source_type")}
            event = "UserMessage" if message.role == "user" else "AgentMessage"
            if (types == {"message", event} and previous.role == message.role
                    and previous.content == message.content and previous.timestamp and message.timestamp):
                try:
                    gap = (datetime.fromisoformat(message.timestamp.replace("Z", "+00:00"))
                           - datetime.fromisoformat(previous.timestamp.replace("Z", "+00:00"))).total_seconds()
                    mirror = 0 <= gap <= 2
                except (TypeError, ValueError):
                    pass
        if mirror:
            canonical = message if message.metadata.get("source_type") == "message" else previous
            result[-1] = replace(canonical, metadata={**canonical.metadata, "codex_mirror_collapsed": True})
            paired = True
        else:
            result.append(message)
            paired = False
    return result


def normalize_codex_messages(conversation: NormalizedConversation) -> None:
    if conversation.source in CODEX_SOURCES:
        conversation.messages = deduplicate_messages(conversation.messages)
        conversation.metadata["message_normalization_version"] = NORMALIZATION_VERSION
