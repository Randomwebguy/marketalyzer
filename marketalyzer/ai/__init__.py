"""AI assistant: chat with an OpenRouter model that can use the app's tools."""

from marketalyzer.ai.agent import ToolError, ToolRegistry, TurnResult, run_turn
from marketalyzer.ai.conversations import (
    delete_conversation,
    display_messages,
    list_conversations,
    load_conversation,
    new_conversation,
    save_conversation,
)
from marketalyzer.ai.openrouter import (
    OpenRouterError,
    key_info,
    list_models,
    pick_default_model,
    stream_chat,
)
from marketalyzer.ai.settings import (
    AISettings,
    load_settings,
    public_settings,
    save_settings,
    settings_path,
)

__all__ = [
    "AISettings",
    "OpenRouterError",
    "ToolError",
    "ToolRegistry",
    "TurnResult",
    "delete_conversation",
    "display_messages",
    "key_info",
    "list_conversations",
    "list_models",
    "load_conversation",
    "load_settings",
    "new_conversation",
    "pick_default_model",
    "public_settings",
    "run_turn",
    "save_conversation",
    "settings_path",
    "stream_chat",
]
