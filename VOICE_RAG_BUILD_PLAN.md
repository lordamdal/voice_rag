# Voice RAG Assistant — Build Plan

## Overview

A local voice-based RAG assistant running on a Dell laptop with 4GB VRAM (RTX 500 Ada) + 16GB RAM. The system uses a modular pipeline: **Whisper (STT) → RAG retrieval → Ollama LLM → Piper (TTS)**, with a React frontend for the user interface and a Python FastAPI backend orchestrating everything.

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                   REACT FRONTEND                        │
│                                                         │
│  ┌──────────┐  ┌──────────────┐  ┌───────────────────┐  │
│  │ Mic Input │  │ Chat Display │  │ Document Upload   │  │
│  │ (WebAudio)│  │ (transcript) │  │ (for RAG ingest)  │  │
│  └─────┬─────┘  └──────▲───────┘  └────────┬──────────┘  │
│        │               │                   │             │
│        │  WebSocket     │  SSE/WS           │  REST       │
└────────┼───────────────┼───────────────────┼─────────────┘
         │               │                   │
┌────────▼───────────────┼───────────────────▼─────────────┐
│                   PYTHON BACKEND (FastAPI)                │
│                                                          │
│  ┌─────────────┐  ┌──────────────┐  ┌─────────────────┐  │
│  │  /ws/voice   │  │  /api/chat   │  │ /api/documents  │  │
│  │  WebSocket   │  │  text route  │  │ upload & ingest │  │
│  └──────┬───────┘  └──────┬───────┘  └────────┬────────┘  │
│         │                 │                   │           │
│         ▼                 ▼                   ▼           │
│  ┌─────────────────────────────────────────────────────┐  │
│  │                  PIPELINE ORCHESTRATOR               │  │
│  │                                                     │  │
│  │  1. STT (Whisper tiny)          — GPU ~150MB        │  │
│  │  2. Embedding (MiniLM-L6-v2)   — CPU ~90MB         │  │
│  │  3. Retrieval (ChromaDB)        — CPU               │  │
│  │  4. LLM (Ollama qwen3:1.7b)    — GPU ~1.2GB        │  │
│  │  5. TTS (Piper ONNX)           — CPU ~60MB         │  │
│  │  6. Conversation Memory         — ChromaDB          │  │
│  └─────────────────────────────────────────────────────┘  │
└──────────────────────────────────────────────────────────┘
```

---

## Tech Stack

| Layer         | Technology                        | Notes                                  |
| ------------- | --------------------------------- | -------------------------------------- |
| Frontend      | React + Vite + Tailwind           | WebAudio API for mic, WebSocket for streaming |
| Backend       | Python 3.11+ / FastAPI            | Async, WebSocket support               |
| STT           | `faster-whisper` (tiny model)     | CTranslate2 optimized, GPU             |
| LLM           | Ollama (`qwen3:1.7b`)            | Already installed, REST API on :11434  |
| TTS           | `piper-tts`                       | ONNX runtime, CPU, offline             |
| Embeddings    | `sentence-transformers` MiniLM    | CPU inference                          |
| Vector DB     | ChromaDB                          | Local persistent storage               |
| Audio         | WebSocket binary frames           | Raw PCM or WAV chunks                  |

---

## Project Structure

```
voice-rag-assistant/
├── backend/
│   ├── main.py                  # FastAPI app entry point
│   ├── requirements.txt
│   ├── config.py                # Model paths, Ollama URL, settings
│   ├── pipeline/
│   │   ├── __init__.py
│   │   ├── stt.py               # Whisper transcription service
│   │   ├── llm.py               # Ollama client wrapper
│   │   ├── tts.py               # Piper TTS service
│   │   ├── rag.py               # ChromaDB + embedding + retrieval
│   │   └── orchestrator.py      # Wires STT → RAG → LLM → TTS
│   ├── models/
│   │   ├── whisper/              # faster-whisper tiny model cache
│   │   └── piper/                # Piper voice model (.onnx + .json)
│   └── data/
│       └── chroma_db/            # ChromaDB persistent storage
│
├── frontend/
│   ├── package.json
│   ├── vite.config.ts
│   ├── tailwind.config.js
│   ├── src/
│   │   ├── App.tsx              # Main app layout
│   │   ├── main.tsx
│   │   ├── components/
│   │   │   ├── VoiceButton.tsx   # Push-to-talk / toggle mic
│   │   │   ├── ChatPanel.tsx     # Scrollable transcript
│   │   │   ├── WaveformViz.tsx   # Audio visualizer
│   │   │   ├── StatusBar.tsx     # Pipeline stage indicator
│   │   │   ├── DocumentUpload.tsx# Drag & drop doc upload
│   │   │   └── SettingsPanel.tsx # Model select, voice select
│   │   ├── hooks/
│   │   │   ├── useAudioRecorder.ts   # WebAudio mic capture
│   │   │   ├── useWebSocket.ts       # WS connection manager
│   │   │   └── useAudioPlayer.ts     # Play TTS audio response
│   │   ├── stores/
│   │   │   └── chatStore.ts     # Zustand store for messages
│   │   └── utils/
│   │       └── audioUtils.ts    # PCM conversion helpers
│
├── docker-compose.yml           # Optional: containerized setup
└── README.md
```

---

## Build Phases

### Phase 1 — Backend Core Pipeline (Priority: HIGH)

**Goal:** Working STT → LLM → TTS loop via REST endpoint.

#### Steps:
1. **Setup FastAPI project** with CORS, static file serving
2. **STT service** (`stt.py`)
   - Load `faster-whisper` tiny model on GPU
   - Accept WAV/PCM bytes → return transcription text
   - Use `compute_type="int8"` for VRAM savings
3. **LLM service** (`llm.py`)
   - HTTP client to Ollama REST API (`http://localhost:11434/api/chat`)
   - Streaming response support (SSE)
   - System prompt with conversation context window
4. **TTS service** (`tts.py`)
   - Load Piper voice model (en_US-amy-medium recommended)
   - Accept text → return WAV audio bytes
   - CPU-only inference via ONNX runtime
5. **Orchestrator** (`orchestrator.py`)
   - Wire: audio bytes → STT → LLM → TTS → audio bytes
   - Return both text transcript AND audio response
6. **REST endpoint** (`POST /api/voice`)
   - Accepts audio file upload
   - Returns JSON `{ transcript, response_text, audio_url }`

#### Test checkpoint:
```bash
curl -X POST http://localhost:8000/api/voice \
  -F "audio=@test_recording.wav"
# Should return transcript + response text + audio file URL
```

---

### Phase 2 — RAG Layer (Priority: HIGH)

**Goal:** Document ingestion + context-aware responses.

#### Steps:
1. **RAG service** (`rag.py`)
   - Initialize ChromaDB persistent client
   - Load `all-MiniLM-L6-v2` sentence-transformer on CPU
   - `ingest_document(file)` — chunk text (500 chars, 50 overlap) → embed → store
   - `retrieve(query, k=3)` — embed query → similarity search → return chunks
2. **Document upload endpoint** (`POST /api/documents`)
   - Accept PDF, TXT, MD files
   - Parse text (use `pymupdf` for PDFs)
   - Chunk and ingest into ChromaDB
   - Return `{ doc_id, chunk_count, status }`
3. **Integrate RAG into orchestrator**
   - After STT: embed user query → retrieve relevant chunks
   - Inject chunks into LLM system prompt:
     ```
     Context from documents:
     ---
     {chunk_1}
     {chunk_2}
     ---
     Answer the user's question using the context above.
     User: {transcribed_text}
     ```
4. **Conversation memory**
   - Store each exchange (user + assistant) as embeddings in a separate ChromaDB collection
   - On each turn, also retrieve top-2 relevant past exchanges
   - This gives "long context" without actually using a big context window

#### Test checkpoint:
- Upload a PDF about a specific topic
- Ask a voice question about it
- Verify the response references document content

---

### Phase 3 — WebSocket Voice Streaming (Priority: MEDIUM)

**Goal:** Real-time voice interaction instead of upload-and-wait.

#### Steps:
1. **WebSocket endpoint** (`/ws/voice`)
   - Accept binary audio frames from frontend mic
   - Buffer frames until silence detection (VAD)
   - On silence: run full pipeline
   - Stream back: `{ type: "transcript", text }` → `{ type: "response", text }` → `{ type: "audio", data: base64 }`
2. **Voice Activity Detection**
   - Use `webrtcvad` or `silero-vad` for endpoint detection
   - Configurable silence threshold (300-500ms)
3. **Pipeline status events**
   - Send status updates over WS: `listening` → `transcribing` → `thinking` → `speaking`
   - Frontend shows current stage to user

---

### Phase 4 — React Frontend (Priority: HIGH, parallel with Phase 1)

**Goal:** Clean, functional UI for voice interaction.

#### Components:

1. **VoiceButton.tsx**
   - Large central mic button (push-to-talk or toggle)
   - Visual states: idle / listening (pulsing) / processing (spinning) / speaking (waveform)
   - Uses `useAudioRecorder` hook for WebAudio capture
   - Sends audio via WebSocket or as POST to `/api/voice`

2. **ChatPanel.tsx**
   - Scrollable message list showing:
     - User messages (transcribed speech, right-aligned)
     - Assistant messages (LLM response, left-aligned)
     - Timestamps and latency badges (e.g., "STT: 180ms | LLM: 1.2s | TTS: 90ms")
   - Auto-scroll to latest message

3. **WaveformViz.tsx**
   - Real-time audio waveform during recording (AnalyserNode)
   - Playback waveform during TTS response
   - Subtle, ambient — not distracting

4. **StatusBar.tsx**
   - Pipeline stage indicator: 🎤 → 📝 → 🧠 → 🔊
   - Shows which step is currently active
   - Displays VRAM/RAM usage if available

5. **DocumentUpload.tsx**
   - Drag & drop zone for PDFs/TXT
   - Shows list of ingested documents with chunk counts
   - Delete document button (removes from ChromaDB)

6. **SettingsPanel.tsx**
   - Collapsible sidebar
   - Model selector (dropdown of Ollama models)
   - Voice selector (Piper voices)
   - RAG toggle on/off
   - Temperature / max tokens sliders

#### Frontend Hooks:

- **useAudioRecorder** — start/stop mic, capture PCM 16-bit 16kHz, return blob
- **useWebSocket** — connect to `/ws/voice`, send binary, receive JSON + audio
- **useAudioPlayer** — queue and play WAV/PCM responses via AudioContext

---

### Phase 5 — Polish & Optimization (Priority: LOW)

1. **Latency dashboard** — measure and display each pipeline stage timing
2. **Keyboard shortcut** — spacebar for push-to-talk
3. **Dark/light theme** toggle
4. **Export conversation** as markdown
5. **Model hot-swap** — change Ollama model without restart
6. **Chunking strategy tuning** — experiment with chunk sizes for RAG quality

---

## Dependencies

### Backend (`requirements.txt`)
```
fastapi>=0.104.0
uvicorn[standard]>=0.24.0
python-multipart>=0.0.6
websockets>=12.0
faster-whisper>=1.0.0
piper-tts>=1.2.0
chromadb>=0.4.22
sentence-transformers>=2.3.0
pymupdf>=1.23.0
httpx>=0.25.0
webrtcvad>=2.0.10
numpy>=1.24.0
```

### Frontend (`package.json` key deps)
```json
{
  "dependencies": {
    "react": "^18.3.0",
    "react-dom": "^18.3.0",
    "zustand": "^4.5.0"
  },
  "devDependencies": {
    "vite": "^5.4.0",
    "tailwindcss": "^3.4.0",
    "typescript": "^5.5.0",
    "@types/react": "^18.3.0"
  }
}
```

---

## API Endpoints Summary

| Method | Path              | Description                     | Input              | Output                              |
| ------ | ----------------- | ------------------------------- | ------------------ | ----------------------------------- |
| POST   | `/api/voice`      | Full voice pipeline             | `audio` file       | `{ transcript, response, audio_url }` |
| POST   | `/api/chat`       | Text-only chat (no voice)       | `{ message }`      | `{ response }` (SSE stream)         |
| POST   | `/api/documents`  | Upload & ingest document        | `file` (PDF/TXT)   | `{ doc_id, chunks, status }`        |
| GET    | `/api/documents`  | List ingested documents         | —                  | `[{ doc_id, name, chunks }]`        |
| DELETE | `/api/documents/{id}` | Remove document from RAG    | —                  | `{ status }`                        |
| GET    | `/api/models`     | List available Ollama models    | —                  | `[{ name, size, quantization }]`    |
| GET    | `/api/status`     | System health + resource usage  | —                  | `{ gpu_mem, ram, models_loaded }`   |
| WS     | `/ws/voice`       | Real-time voice streaming       | binary audio frames | JSON events + binary audio          |

---

## Environment & Config

```env
# .env
OLLAMA_BASE_URL=http://localhost:11434
OLLAMA_MODEL=qwen3:1.7b
WHISPER_MODEL=tiny
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=int8
PIPER_VOICE=en_US-amy-medium
CHROMA_PERSIST_DIR=./data/chroma_db
EMBEDDING_MODEL=all-MiniLM-L6-v2
RAG_CHUNK_SIZE=500
RAG_CHUNK_OVERLAP=50
RAG_TOP_K=3
```

---

## Build Order (Recommended)

```
Week 1:  Phase 1 (backend pipeline) + Phase 4 scaffolding (React app shell)
Week 2:  Phase 2 (RAG) + Phase 4 components (chat, voice button, upload)
Week 3:  Phase 3 (WebSocket streaming) + Phase 4 hooks (real-time audio)
Week 4:  Phase 5 (polish, latency tuning, UI refinement)
```

For a fast test/demo, **Phase 1 + Phase 4 (basic)** can be done in a single session — that gives you a working voice-in → text-out → voice-out loop with a simple React UI.
