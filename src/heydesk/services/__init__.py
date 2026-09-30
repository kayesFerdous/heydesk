from dataclasses import dataclass

from heydesk.services.conversations import ConversationService


@dataclass(frozen=True)
class Services:
    conversations: ConversationService
