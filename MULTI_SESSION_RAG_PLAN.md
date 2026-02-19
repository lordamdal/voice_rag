# Multi-Session Chat + Page-Level RAG References

## Context

The voice RAG app currently has a single conversation with no persistence — refreshing loses all messages, and there's no way to manage multiple chats. Additionally, when PDFs are ingested, page numbers are lost (all pages concatenated into one string), so the assistant can't cite which page info came from or read a specific page on request.

**Goal**: Add ChatGPT-style session management (create/switch/delete chats in sidebar) and page-aware RAG with source citations.

## Files to modify

| File | Change |
|------|--------|
| `backend/pipeline/sessions.py` | **NEW** — SessionManager: JSON-file persistence, CRUD |
| `backend/pipeline/rag.py` | Page-aware PDF ingestion, metadata-rich retrieval, `get_page()`, `pages` collection |
| `backend/pipeline/orchestrator.py` | Session-keyed instances, source-attributed context building |
| `backend/pipeline/llm.py` | System prompt: cite sources naturally |
| `backend/main.py` | Session CRUD endpoints, session_id threading, page retrieval endpoint |
| `backend/config.py` | Add `SESSIONS_DIR` |
| `frontend/src/stores/chatStore.ts` | Session state + actions, `SourceCitation` type on messages |
| `frontend/src/App.tsx` | Session sidebar with list + new chat button |
| `frontend/src/components/ChatPanel.tsx` | Source citation chips on assistant messages |
| `frontend/src/components/TextInput.tsx` | Pass `session_id` in API calls |
| `frontend/src/components/VoiceButton.tsx` | Pass `session_id` in WebSocket + REST |
| `frontend/src/hooks/useWebSocket.ts` | `session_id` in `sendEndSignal`, `sources` in response handler |

## Changes

### 1. `backend/config.py` — Add sessions dir

```python
SESSIONS_DIR = BASE_DIR / "data" / "sessions"
SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
```

### 2. `backend/pipeline/sessions.py` — NEW file

`SessionManager` class with JSON-file persistence (`data/sessions/{uuid}.json`):
- `SessionData` dataclass: `session_id`, `title`, `created_at`, `updated_at`, `conversation_history: list[dict]`, `rag_enabled: bool`
- `_load_all()`: Scans directory on startup, loads all `.json` files
- `_persist(session_id)`: Writes single session to disk
- `create(title)` → `SessionData`
- `get(session_id)` → `SessionData | None`
- `get_or_create(session_id)` → `SessionData` (handles None by creating new)
- `list_all()` → `list[dict]` sorted by `updated_at` desc
- `delete(session_id)` → `bool`
- `update_title(session_id, title)`
- `append_history(session_id, role, content)` — auto-titles from first user message (truncated 60 chars)
- `get_history(session_id, max_entries=20)` → `list[dict]`

Module-level singleton: `session_manager = SessionManager()`

### 3. `backend/pipeline/rag.py` — Page-aware RAG

**New `pages` collection** in `load()`:
```python
PAGES_COLLECTION = "pages"
self._pages_collection = self._chroma.get_or_create_collection(
    name=PAGES_COLLECTION, metadata={"hnsw:space": "cosine"}
)
```

**Rewrite `_parse_pdf_bytes`** to return `list[tuple[int, str]]` (1-indexed page_number, page_text) instead of concatenated string. Same for `_parse_pdf`.

**New `ingest_pages(pages, doc_id, filename, source_type)` method**:
- For each `(page_num, page_text)`:
  1. Store full page in `pages_collection` with id `{doc_id}_page_{page_num}`, metadata `{doc_id, filename, page_number, source_type}`
  2. Chunk page text and store in `doc_collection` with metadata `{doc_id, filename, chunk_index, page_number, source_type}`
- Return total chunk count

**Update `ingest_text`** — add `source_type` param, set `page_number: None` in chunk metadata for txt/md.

**Update `ingest_bytes`** — call `ingest_pages` for PDFs, `ingest_text` for txt/md.

**Rewrite `retrieve`** — return `list[dict]` instead of `list[str]`:
```python
def retrieve(self, query, k=None) -> list[dict]:
    # query with include=["documents", "metadatas"]
    # return [{"text", "doc_id", "filename", "page_number", "chunk_index", "source_type"}, ...]
```

**New `get_page(doc_id, page_number)` method** — fetch from `pages_collection` by id `{doc_id}_page_{page_num}`, return `{text, filename, page_number, doc_id}` or None.

**Update `delete_document`** — also delete from `pages_collection`.

**Update `store_conversation`** — accept optional `session_id` param, store in metadata.

**Update `retrieve_conversations`** — accept optional `session_id`, pass `where={"session_id": session_id}` to ChromaDB query.

**New `delete_session_conversations(session_id)`** — delete all conversation entries matching session_id.

### 4. `backend/pipeline/orchestrator.py` — Session-keyed orchestrators

**Replace singleton with factory pattern**:
- Remove module-level `orchestrator = PipelineOrchestrator()`
- `PipelineOrchestrator.__init__(self, session_id)` — stores session_id, delegates history to `session_manager`
- `_get_history()` → calls `session_manager.get_history(self.session_id, self.max_history * 2)`
- `_append_history(role, content)` → calls `session_manager.append_history(...)`
- `rag_enabled` property reads/writes from session data

**New helper `_build_context(query)`** → `tuple[str, list[dict]]`:
- Calls `rag_service.retrieve(query)` (now returns `list[dict]`)
- Calls `rag_service.retrieve_conversations(query, session_id=self.session_id)`
- Formats context with `[Source: filename, page N]` labels
- Deduplicates sources → returns `(context_string, sources_list)`

**All 4 `process_*` methods**: Use `_build_context()`, `_get_history()`, `_append_history()`. Add `sources` to return dicts. Pass `session_id` to `rag_service.store_conversation()`.

**Module-level factory**:
```python
_orchestrators: dict[str, PipelineOrchestrator] = {}

def get_orchestrator(session_id: str) -> PipelineOrchestrator:
    if session_id not in _orchestrators:
        _orchestrators[session_id] = PipelineOrchestrator(session_id)
    return _orchestrators[session_id]

def remove_orchestrator(session_id: str):
    _orchestrators.pop(session_id, None)
```

### 5. `backend/pipeline/llm.py` — Citation-aware prompt

Update system prompt to add:
```
- When your answer uses information from a specific document source, mention the source naturally.
  For example, say "According to the research paper on page three..." Do not use citation brackets or footnote numbers.
```

Update `_build_messages` context injection to say "use source references when citing".

### 6. `backend/main.py` — Session + page endpoints

**New imports**: `from pipeline.sessions import session_manager` and `from pipeline.orchestrator import get_orchestrator, remove_orchestrator`. Remove old `from pipeline.orchestrator import orchestrator`.

**Session CRUD endpoints**:
- `POST /api/sessions` → create session, return `{session_id, title, created_at}`
- `GET /api/sessions` → list all sessions (sorted by most recent)
- `GET /api/sessions/{id}` → get session with `conversation_history` (for switching)
- `PATCH /api/sessions/{id}` → rename session
- `DELETE /api/sessions/{id}` → delete session + conversation memory + remove orchestrator

**Page endpoint**:
- `GET /api/documents/{doc_id}/pages/{page_number}` → return full page text

**Update existing endpoints**:
- `ChatRequest` — add `session_id: str | None = None`
- `/api/chat` — `get_or_create` session, `get_orchestrator(session_id)`, return `sources` + `session_id`
- `/api/voice` — accept `session_id` form field
- WebSocket — extract `session_id` from `"end"` message, use per-session orchestrator, send `sources` in response

### 7. `frontend/src/stores/chatStore.ts` — Session management

**New types**:
```typescript
export interface Session { id: string; title: string; createdAt: number; updatedAt: number; messages: Message[] }
export interface SourceCitation { filename: string; page_number?: number | null; doc_id: string }
```

Add `sources?: SourceCitation[]` to `Message` interface.

**New state**: `sessions: Session[]`, `activeSessionId: string | null`

**New actions**:
- `loadSessions()` — `GET /api/sessions`, hydrate sidebar, auto-switch to most recent
- `createSession()` — `POST /api/sessions`, add to list, switch to it, return id
- `switchSession(id)` — `GET /api/sessions/{id}`, reconstruct messages from `conversation_history`
- `deleteSession(id)` — `DELETE /api/sessions/{id}`, switch to next session
- `renameSession(id, title)` — `PATCH /api/sessions/{id}`

**Update `addMessage`** — also update session's `messages` and `updatedAt` in the sessions array.

### 8. `frontend/src/App.tsx` — Session sidebar

Replace current sidebar (DocumentUpload + clear button) with:
- Header: "Voice RAG" + new chat (+ icon) button
- Session list: scrollable, clickable items with title + delete button on hover
- Active session highlighted
- Documents section at bottom of sidebar (collapsible or always visible)
- Call `loadSessions()` on mount via `useEffect`

### 9. `frontend/src/components/ChatPanel.tsx` — Source citations

After message content, for assistant messages with `sources`:
```tsx
{msg.sources?.length > 0 && (
  <div className="mt-2 pt-2 border-t border-slate-600/50">
    <span className="text-[10px] text-slate-400">Sources: </span>
    {msg.sources.map((s, i) => (
      <span key={i} className="text-[10px] bg-slate-600/50 text-slate-300 rounded px-1.5 py-0.5 mr-1">
        {s.filename}{s.page_number != null && `, p.${s.page_number}`}
      </span>
    ))}
  </div>
)}
```

### 10. `frontend/src/components/TextInput.tsx` — Pass session_id

In `handleSend`: get `activeSessionId` from store, auto-create session if none, include `session_id` in the `/api/chat` body. Pass `sources` from response to `addMessage`.

### 11. `frontend/src/components/VoiceButton.tsx` — Pass session_id

In `onSpeechEnd`: add `session_id: store.activeSessionId` to `sendEndSignal` opts.
In `handleManualToggle`: add `session_id` to FormData for `/api/voice`.

### 12. `frontend/src/hooks/useWebSocket.ts` — Sources + session_id

- Add `session_id?: string` and `sources?: SourceCitation[]` to `WSMessage` interface
- `sendEndSignal` opts type: add `session_id?: string`
- In `"response"` case: pass `msg.sources` to `addMessage`

## Implementation order

1. Backend sessions: `config.py` → `sessions.py` (new) → `orchestrator.py` refactor → `main.py` session endpoints
2. Frontend sessions: `chatStore.ts` → `App.tsx` sidebar → `TextInput.tsx` → `VoiceButton.tsx` → `useWebSocket.ts`
3. Backend page-aware RAG: `rag.py` rewrite → `orchestrator.py` context building → `llm.py` prompt → `main.py` page endpoint
4. Frontend citations: `ChatPanel.tsx` sources display

## Verification

1. Backend starts without errors
2. `npx tsc --noEmit && npx vite build` — frontend compiles
3. Create a new session via UI → appears in sidebar
4. Send a message → session auto-titles from first message
5. Create second session → switch between them, messages are separate
6. Delete a session → removed from sidebar
7. Upload a PDF → chunks stored with page numbers
8. Ask about the PDF content → response includes source citations with page numbers
9. `GET /api/documents/{id}/pages/3` → returns full page 3 text
10. Refresh browser → sessions persist, switch back to last active session
11. Voice mode works with sessions (session_id flows through WebSocket)
