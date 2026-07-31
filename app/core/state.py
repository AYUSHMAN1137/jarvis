"""Shared application state — global service references set during startup.

Every route module imports from here to access the running services instead of
relying on module-level globals scattered across files.
"""

from app.services.vector_store import VectorStoreService
from app.services.groq_service import GroqService
from app.services.realtime_service import RealtimeGroqService
from app.services.chat_service import ChatService
from app.services.resolver import Resolver
from app.services.vision_service import VisionService
from app.services.agent.agent_loop import AgentLoop

# These are set to real instances during lifespan() in app.core.startup
vector_store_service: VectorStoreService = None
groq_service: GroqService = None
realtime_service: RealtimeGroqService = None
# M13: the understanding layer replaced BrainService. It produces a self-contained
# goal plus structural flags instead of a single category word.
resolver: Resolver = None
vision_service: VisionService = None
agent_loop: AgentLoop = None
chat_service: ChatService = None
