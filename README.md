# Voice RAG

A lightweight, fully local voice assistant with real-time streaming and document retrieval. Upload PDFs or text files, then talk to your documents using natural voice or text. The system reads pages verbatim on request and answers questions using retrieved context — all running on consumer hardware without cloud dependencies.

## Features

- **Real-time voice conversation** with streaming sentence-by-sentence TTS
- **Document Q&A** — upload PDFs/TXT/MD and ask questions about them
- **Direct page reading** — say "read page 3" and hear the full page content verbatim
- **Page-level RAG** — retrieves full pages (not just fragments) for better context
- **Dual voice modes** — manual push-to-talk or continuous always-listening with VAD
- **Multi-session chat** — separate conversation threads with per-session documents
- **24 TTS voices** — American and British accents, male and female
- **Fully local** — no cloud APIs, no data leaves your machine

## Architecture

```
Microphone → [Faster-Whisper STT] → [RAG Retrieval] → [Ollama LLM] → [Kokoro TTS] → Speaker
                                          ↑                                ↓
                                     ChromaDB                    Sentence Buffer
                                   (pages + chunks)          (stream audio chunks)
```

**Key design decisions:**

- **Streaming pipeline**: LLM tokens are accumulated in a sentence buffer and synthesized to speech as each sentence completes — users hear responses incrementally instead of waiting for the full generation
- **Page-level retrieval**: PDF pages are stored as complete units in ChromaDB alongside chunked embeddings. Retrieval prefers full pages over 500-char fragments, giving small LLMs better context
- **Direct page bypass**: When the user asks to "read page X", the orchestrator detects this via regex, fetches the raw page from ChromaDB, and returns it directly — no LLM summarization
- **Session-scoped documents**: Each chat session has its own document set with filtered retrieval

## Tech Stack

| Component | Technology |
|-----------|-----------|
| Backend | FastAPI, Uvicorn, Python 3.12 |
| Frontend | React 19, Zustand, Tailwind CSS, Vite |
| STT | Faster-Whisper (CTranslate2) |
| TTS | Kokoro 82M (24kHz, CPU) |
| LLM | Ollama (any model — default qwen3:1.7b) |
| Embeddings | all-MiniLM-L6-v2 (sentence-transformers) |
| Vector DB | ChromaDB (persistent) |
| PDF parsing | PyMuPDF |
| Communication | WebSocket (voice streaming), REST (text/config) |

## Prerequisites

- Python 3.12+
- Node.js 18+
- [Ollama](https://ollama.ai) installed and running
- espeak-ng (required by Kokoro TTS): `sudo apt install espeak-ng`

## Setup

### 1. Clone and install backend

```bash
cd backend
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Pull an Ollama model

```bash
ollama pull qwen3:1.7b    # lightweight, fits 4GB VRAM
# or for better conversation quality:
ollama pull qwen3:4b       # fits 4GB VRAM, much smarter
```

### 3. Install frontend

```bash
cd frontend
npm install
```

### 4. Configure (optional)

Create `backend/.env` to override defaults:

```env
OLLAMA_MODEL=qwen3:1.7b
WHISPER_MODEL=base          # tiny/base/small/medium/large
WHISPER_DEVICE=cpu           # cpu or cuda
TTS_VOICE=af_heart           # see voice list below
RAG_CHUNK_SIZE=500
RAG_TOP_K=3
```

### 5. Run

```bash
# Terminal 1 — Backend
cd backend
source venv/bin/activate
uvicorn main:app --reload --host 0.0.0.0 --port 8000

# Terminal 2 — Frontend
cd frontend
npm run dev
```

Open `http://localhost:5173` in your browser.

First startup downloads models automatically (~150MB for Whisper base, ~80MB for Kokoro, ~22MB for embeddings).

## Usage

### Voice conversation

Click the microphone button to record, release to send. The assistant responds with voice and text. Toggle **Continuous Mode** for always-listening with automatic voice activity detection.

### Document Q&A

1. Upload a PDF, TXT, or MD file using the sidebar document panel
2. Ask questions — the assistant uses retrieved context to answer
3. Say **"read page 3"** to hear the full page content verbatim

### Text chat

Type in the text input box for text-only conversation (no STT/TTS).

## API Endpoints

| Method | Endpoint | Description |
|--------|----------|-------------|
| `POST` | `/api/voice` | Full voice pipeline (audio in → audio out) |
| `WS` | `/ws/voice` | Streaming voice with real-time audio chunks |
| `POST` | `/api/chat` | Text chat with optional streaming |
| `POST` | `/api/documents` | Upload document for RAG |
| `GET` | `/api/documents` | List ingested documents |
| `DELETE` | `/api/documents/{id}` | Remove document |
| `GET` | `/api/documents/{id}/pages/{num}` | Get full page text |
| `POST` | `/api/sessions` | Create chat session |
| `GET` | `/api/sessions` | List sessions |
| `GET` | `/api/status` | Health check + resource usage |
| `GET` | `/api/models` | List available Ollama models |
| `GET` | `/api/voices` | List available TTS voices |

## Available Voices

**American Female**: af_heart, af_alloy, af_aoede, af_bella, af_jessica, af_kore, af_nicole, af_nova, af_river, af_sarah, af_sky

**American Male**: am_adam, am_echo, am_eric, am_fenrir, am_liam, am_michael, am_onyx

**British Female**: bf_alice, bf_emma, bf_isabella, bf_lily

**British Male**: bm_daniel, bm_fable, bm_george, bm_lewis

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `OLLAMA_BASE_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `qwen3:1.7b` | Default LLM model |
| `WHISPER_MODEL` | `base` | STT model size |
| `WHISPER_DEVICE` | `cpu` | STT compute device |
| `WHISPER_COMPUTE_TYPE` | `int8` | STT quantization |
| `TTS_VOICE` | `af_heart` | Default TTS voice |
| `TTS_LANG_CODE` | `a` | Language code (a=American, b=British) |
| `EMBEDDING_MODEL` | `all-MiniLM-L6-v2` | Sentence embedding model |
| `CHROMA_PERSIST_DIR` | `data/chroma_db` | Vector DB storage path |
| `RAG_CHUNK_SIZE` | `500` | Text chunk size (characters) |
| `RAG_CHUNK_OVERLAP` | `50` | Chunk overlap (characters) |
| `RAG_TOP_K` | `3` | Number of retrieval results |

## Hardware Requirements

**Minimum** (CPU-only):
- 8GB RAM
- Any modern CPU
- No GPU required

**Recommended** (GPU-accelerated LLM):
- 16GB RAM
- NVIDIA GPU with 4GB+ VRAM
- Ollama handles GPU offloading automatically

Tested on: NVIDIA RTX 500 Ada (4GB), Ubuntu Linux, 16GB RAM.

## Project Structure

```
voice_rag/
├── backend/
│   ├── main.py                 # FastAPI app, all endpoints
│   ├── config.py               # Environment configuration
│   ├── requirements.txt
│   └── pipeline/
│       ├── stt.py              # Faster-Whisper speech-to-text
│       ├── llm.py              # Ollama LLM with retry logic
│       ├── tts.py              # Kokoro text-to-speech
│       ├── rag.py              # ChromaDB RAG (pages + chunks)
│       ├── orchestrator.py     # Pipeline orchestration
│       └── sessions.py         # Session persistence
├── frontend/
│   └── src/
│       ├── App.tsx             # Root layout
│       ├── components/         # UI components
│       ├── hooks/              # WebSocket, audio recorder
│       └── stores/             # Zustand state management
└── README.md
```

## Author

**Ahmed Ali (Amdal)** — Beaverhand Inc.

## License

MIT License — see [LICENSE](LICENSE) for details.
