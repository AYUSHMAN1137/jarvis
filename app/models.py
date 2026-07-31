from pydantic import BaseModel, Field
from typing import List, Optional

class ChatMessage(BaseModel):
    role: str
    content: str

class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=32_000)
    session_id: Optional[str] = None
    tts: bool = False
    imgbase64: Optional[str] = None

class ChatResponse(BaseModel):
    response: str
    session_id: str

class ChatHistory(BaseModel):
    session_id: str
    messages: List[ChatMessage]

class ConversationSummary(BaseModel):
    session_id: str
    title: str
    preview: str = ""
    created_at: str
    updated_at: str
    message_count: int = 0

class ConversationList(BaseModel):
    conversations: List[ConversationSummary] = []
    next_cursor: Optional[str] = None
    total: int = 0

class ConversationDetail(BaseModel):
    session_id: str
    title: str
    created_at: str
    updated_at: str
    message_count: int = 0
    messages: List[ChatMessage] = []

class ConversationRenameRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=200)

class JarvisActions(BaseModel):
    wopens: List[str] = []
    plays: List[str] = []
    images: List[str] = []
    contents: List[str] = []
    googlesearches: List[str] = []
    youtubesearches: List[str] = []
    cam: Optional[dict] = None

class TTSRequest(BaseModel):
    text: str = Field(..., min_length=1, max_length=5000)
