import asyncio
import base64
import json
import logging
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect, HTTPException, Form
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from config import AUDIO_OUTPUT_DIR, OLLAMA_BASE_URL
from pipeline.stt import stt_service
from pipeline.llm import llm_service
from pipeline.tts import tts_service
from pipeline.rag import rag_service
from pipeline.sessions import session_manager
from pipeline.orchestrator import get_orchestrator, remove_orchestrator

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize services on startup, cleanup on shutdown."""
    logger.info("Starting Voice RAG Assistant...")

    # Load models (CPU-bound, run in thread pool)
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, stt_service.load)
    await loop.run_in_executor(None, rag_service.load)
    await loop.run_in_executor(None, tts_service.load)

    # Initialize async services
    await llm_service.initialize()

    logger.info("All services loaded. Ready.")
    yield

    # Cleanup
    await llm_service.close()
    logger.info("Shutdown complete.")


app = FastAPI(title="Voice RAG Assistant", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve generated audio files
app.mount("/audio", StaticFiles(directory=str(AUDIO_OUTPUT_DIR)), name="audio")


# --- Pydantic models ---

class ChatRequest(BaseModel):
    message: str
    session_id: str | None = None
    model: str | None = None
    temperature: float = 0.7
    max_tokens: int = 512
    stream: bool = False


class SettingsUpdate(BaseModel):
    rag_enabled: bool | None = None
    model: str | None = None


class SessionCreate(BaseModel):
    title: str = "New chat"


class SessionUpdate(BaseModel):
    title: str | None = None
    rag_enabled: bool | None = None


# --- Session CRUD endpoints ---

@app.post("/api/sessions")
async def create_session(body: SessionCreate):
    """Create a new chat session."""
    session = session_manager.create(body.title)
    return {
        "session_id": session.session_id,
        "title": session.title,
        "created_at": session.created_at,
    }


@app.get("/api/sessions")
async def list_sessions():
    """List all sessions sorted by most recent."""
    return session_manager.list_all()


@app.get("/api/sessions/{session_id}")
async def get_session(session_id: str):
    """Get a session with its conversation history."""
    session = session_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    return {
        "session_id": session.session_id,
        "title": session.title,
        "created_at": session.created_at,
        "updated_at": session.updated_at,
        "conversation_history": session.conversation_history,
        "rag_enabled": session.rag_enabled,
    }


@app.patch("/api/sessions/{session_id}")
async def update_session(session_id: str, body: SessionUpdate):
    """Update session title and/or settings."""
    session = session_manager.get(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="Session not found")
    if body.title is not None:
        session_manager.update_title(session_id, body.title)
    if body.rag_enabled is not None:
        session_manager.set_rag_enabled(session_id, body.rag_enabled)
    updated = session_manager.get(session_id)
    return {"session_id": session_id, "title": updated.title, "rag_enabled": updated.rag_enabled}


@app.delete("/api/sessions/{session_id}")
async def delete_session(session_id: str):
    """Delete a session and its conversation memory."""
    if not session_manager.delete(session_id):
        raise HTTPException(status_code=404, detail="Session not found")
    remove_orchestrator(session_id)
    try:
        rag_service.delete_session_conversations(session_id)
    except Exception as e:
        logger.warning("Failed to delete session conversations: %s", e)
    return {"status": "deleted", "session_id": session_id}


# --- Page retrieval ---

@app.get("/api/documents/{doc_id}/pages/{page_number}")
async def get_document_page(doc_id: str, page_number: int):
    """Get the full text of a specific page from an ingested document."""
    result = rag_service.get_page(doc_id, page_number)
    if not result:
        raise HTTPException(status_code=404, detail="Page not found")
    return result


# --- REST Endpoints ---

@app.post("/api/voice")
async def voice_pipeline(
    audio: UploadFile = File(...),
    session_id: str | None = Form(None),
):
    """Full voice pipeline: audio upload → STT → RAG → LLM → TTS."""
    audio_bytes = await audio.read()
    if not audio_bytes:
        raise HTTPException(status_code=400, detail="Empty audio file")

    session = session_manager.get_or_create(session_id)
    orch = get_orchestrator(session.session_id)
    result = await orch.process_voice(audio_bytes)
    return {
        "transcript": result["transcript"],
        "response_text": result["response_text"],
        "audio_url": result["audio_url"],
        "timings": result["timings"],
        "sources": result.get("sources", []),
        "session_id": session.session_id,
    }


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """Text chat endpoint with optional streaming."""
    session = session_manager.get_or_create(request.session_id)
    orch = get_orchestrator(session.session_id)

    if request.stream:
        async def event_stream():
            async for token in orch.process_text_stream(
                message=request.message,
                model=request.model,
                temperature=request.temperature,
                max_tokens=request.max_tokens,
            ):
                yield f"data: {json.dumps({'token': token})}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    result = await orch.process_text(
        message=request.message,
        model=request.model,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
    )
    result["session_id"] = session.session_id
    return result


@app.post("/api/documents")
async def upload_document(file: UploadFile = File(...), session_id: str | None = Form(None)):
    """Upload and ingest a document for RAG, optionally scoped to a session."""
    if not file.filename:
        raise HTTPException(status_code=400, detail="No filename provided")

    allowed_ext = {".pdf", ".txt", ".md", ".text"}
    ext = "." + file.filename.rsplit(".", 1)[-1].lower() if "." in file.filename else ""
    if ext not in allowed_ext:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported file type: {ext}. Allowed: {', '.join(allowed_ext)}",
        )

    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")

    try:
        doc_id, chunk_count = rag_service.ingest_bytes(data, file.filename, session_id=session_id or "")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Ingestion failed: {e}")

    return {"doc_id": doc_id, "chunks": chunk_count, "status": "ingested", "filename": file.filename}


@app.get("/api/documents")
async def list_documents(session_id: str | None = None):
    """List ingested documents, optionally filtered by session."""
    return rag_service.list_documents(session_id=session_id)


@app.delete("/api/documents/{doc_id}")
async def delete_document(doc_id: str):
    """Remove a document from RAG."""
    success = rag_service.delete_document(doc_id)
    if not success:
        raise HTTPException(status_code=404, detail="Document not found")
    return {"status": "deleted", "doc_id": doc_id}


@app.get("/api/models")
async def list_models():
    """List available Ollama models."""
    try:
        models = await llm_service.list_models()
        return models
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Failed to reach Ollama: {e}")


@app.get("/api/status")
async def system_status():
    """System health check and resource usage."""
    import shutil

    gpu_info = None
    try:
        import subprocess
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0:
            parts = result.stdout.strip().split(", ")
            gpu_info = {
                "memory_used_mb": int(parts[0]),
                "memory_total_mb": int(parts[1]),
                "utilization_pct": int(parts[2]),
            }
    except Exception:
        pass

    disk = shutil.disk_usage("/")
    import psutil
    mem = psutil.virtual_memory() if hasattr(__import__("psutil"), "virtual_memory") else None

    status = {
        "status": "ok",
        "ollama_url": OLLAMA_BASE_URL,
        "gpu": gpu_info,
        "ram": {
            "used_gb": round(mem.used / 1e9, 1) if mem else None,
            "total_gb": round(mem.total / 1e9, 1) if mem else None,
        },
        "documents_count": len(rag_service.list_documents()),
        "conversation_memory_count": rag_service.conv_collection.count(),
        "sessions_count": len(session_manager.list_all()),
    }
    return status


@app.patch("/api/settings")
async def update_settings(settings: SettingsUpdate):
    """Update runtime settings (model only — RAG toggle is per-session now)."""
    if settings.model is not None:
        llm_service.model = settings.model
    return {
        "model": llm_service.model,
    }


@app.get("/api/voices")
async def list_voices():
    """List available Kokoro TTS voices."""
    from pipeline.tts import KOKORO_VOICES
    return {
        "voices": [
            {"id": vid, "label": label}
            for vid, label in KOKORO_VOICES.items()
        ],
        "current": tts_service._voice,
    }


class VoiceUpdate(BaseModel):
    voice: str


@app.put("/api/voices")
async def set_voice(update: VoiceUpdate):
    """Change the active TTS voice."""
    from pipeline.tts import KOKORO_VOICES
    if update.voice not in KOKORO_VOICES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown voice: {update.voice}",
        )
    tts_service.set_voice(update.voice)
    return {"status": "updated", "voice": update.voice}


# --- WebSocket Voice Streaming ---

@app.websocket("/ws/voice")
async def websocket_voice(ws: WebSocket):
    """Real-time voice streaming endpoint.

    Protocol:
    - Client sends binary audio frames (PCM16 16kHz mono)
    - Client sends JSON: {"type": "end", "session_id": "..."} to signal end of speech
    - Server streams JSON: {"type": "audio_chunk", "data": "<base64 wav>", "index": N}
    - Server sends JSON: {"type": "audio_done"} when all chunks sent
    """
    import asyncio

    await ws.accept()
    logger.info("WebSocket voice connection opened")

    audio_buffer = bytearray()
    cancel_event: asyncio.Event | None = None
    pipeline_task: asyncio.Task | None = None

    try:
        while True:
            message = await ws.receive()

            if message.get("type") == "websocket.disconnect":
                break

            if "bytes" in message:
                audio_buffer.extend(message["bytes"])

            elif "text" in message:
                data = json.loads(message["text"])

                if data.get("type") == "end" and audio_buffer:
                    audio_bytes = bytes(audio_buffer)
                    audio_buffer.clear()

                    cancel_event = asyncio.Event()

                    # Get or create session
                    ws_session_id = data.get("session_id")
                    session = session_manager.get_or_create(ws_session_id)
                    orch = get_orchestrator(session.session_id)

                    async def status_cb(stage: str):
                        await ws.send_json({"type": "status", "stage": stage})

                    async def transcript_cb(text: str):
                        await ws.send_json({"type": "transcript", "text": text})

                    async def audio_chunk_cb(wav_bytes: bytes, index: int):
                        audio_b64 = base64.b64encode(wav_bytes).decode("ascii")
                        await ws.send_json({
                            "type": "audio_chunk",
                            "data": audio_b64,
                            "index": index,
                        })

                    async def run_pipeline():
                        try:
                            result = await orch.process_voice_stream(
                                audio_bytes,
                                model=data.get("model"),
                                temperature=data.get("temperature", 0.7),
                                max_tokens=data.get("max_tokens", 512),
                                status_callback=status_cb,
                                audio_chunk_callback=audio_chunk_cb,
                                transcript_callback=transcript_cb,
                                cancel_event=cancel_event,
                            )

                            if not cancel_event.is_set():
                                # Send full response text for chat display
                                if result.get("response_text"):
                                    await ws.send_json({
                                        "type": "response",
                                        "text": result["response_text"],
                                        "timings": result["timings"],
                                        "sources": result.get("sources", []),
                                        "session_id": session.session_id,
                                    })

                                await ws.send_json({"type": "audio_done"})
                                await ws.send_json({"type": "status", "stage": "idle"})

                        except Exception as e:
                            logger.error("Pipeline error: %s", e, exc_info=True)
                            await ws.send_json({
                                "type": "error",
                                "message": str(e),
                            })

                    pipeline_task = asyncio.create_task(run_pipeline())

                elif data.get("type") == "cancel":
                    audio_buffer.clear()
                    if cancel_event:
                        cancel_event.set()
                    if pipeline_task and not pipeline_task.done():
                        pipeline_task.cancel()
                    await ws.send_json({"type": "status", "stage": "idle"})

    except WebSocketDisconnect:
        logger.info("WebSocket voice connection closed")
        if cancel_event:
            cancel_event.set()
    except Exception as e:
        logger.error("WebSocket error: %s", e, exc_info=True)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
