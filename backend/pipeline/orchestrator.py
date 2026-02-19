import asyncio
import logging
import re
import time
import uuid
from pathlib import Path
from config import AUDIO_OUTPUT_DIR
from pipeline.stt import stt_service
from pipeline.llm import llm_service
from pipeline.tts import tts_service
from pipeline.rag import rag_service
from pipeline.sessions import session_manager

logger = logging.getLogger(__name__)

# --- Page-read detection ---

# Multiple patterns tried in order; first match wins.
# Group layout: (page_num_g1, page_num_g2, page_num_g3, doc_hint_g4)
_PAGE_REQUEST_RE = re.compile(
    r'(?:'
    r'(?:read|show|get|give|tell)(?:\s+me)?(?:\s+about)?\s+page\s+(?:number\s+)?(\d+)'  # g1
    r'|what(?:\s+is|.s)\s+on\s+page\s+(?:number\s+)?(\d+)'                               # g2
    r'|page\s+(?:number\s+)?(\d+).*?(?:read|show|get|content|text|what)'                  # g3 (page first, verb later)
    r')'
    r'(?:\s+(?:of|from|in)\s+(.+))?',                                                     # g4 doc hint
    re.IGNORECASE,
)

# --- Sentence buffer for streaming LLM→TTS ---

_ABBREVIATIONS = frozenset({
    "Mr", "Mrs", "Ms", "Dr", "Prof", "Sr", "Jr", "St", "Ave", "Blvd",
    "Dept", "Est", "Fig", "Gen", "Gov", "Sgt", "Corp", "Inc", "Ltd", "Co",
    "vs", "etc", "approx", "dept", "est", "min", "max", "misc", "tech",
})

# Sentence-ending punctuation followed by whitespace and uppercase/quote
_SENTENCE_END_RE = re.compile(r'(?<=[.!?])\s+(?=[A-Z"\u201C])')
# Single-letter abbreviation pattern (e.g. "U." in "U.S.")
_SINGLE_LETTER_ABBR_RE = re.compile(r'\b[A-Z]\.$')
# Decimal number ending (e.g. "3." in "3.14")
_DECIMAL_END_RE = re.compile(r'\d\.$')
# Fallback split points for long buffers
_CLAUSE_SPLIT_RE = re.compile(r'[,;:]\s+(?=[A-Za-z])')

MIN_SENTENCE_LENGTH = 20
MAX_BUFFER_LENGTH = 500


class SentenceBuffer:
    """Accumulates streaming LLM tokens and yields complete sentences."""

    def __init__(self):
        self._buffer = ""

    def add(self, token: str) -> list[str]:
        """Add a token, return list of complete sentences (may be empty)."""
        self._buffer += token
        sentences = []

        # Split on paragraph breaks first
        while "\n\n" in self._buffer:
            before, after = self._buffer.split("\n\n", 1)
            sentence = before.strip()
            self._buffer = after
            if sentence:
                sentences.append(sentence)

        # Split on sentence-ending punctuation
        # Use search_start to skip past false positives (abbreviations, short fragments)
        search_start = 0
        while True:
            match = _SENTENCE_END_RE.search(self._buffer, search_start)
            if not match:
                break

            candidate = self._buffer[:match.start()].strip()

            # Check for abbreviations — skip this split, try next
            last_word_match = re.search(r'(\w+)\.$', candidate)
            if last_word_match and last_word_match.group(1) in _ABBREVIATIONS:
                search_start = match.end()
                continue

            # Check for single-letter abbreviation (U.S., A.I.)
            if _SINGLE_LETTER_ABBR_RE.search(candidate):
                search_start = match.end()
                continue

            # Check for decimal number (3.14)
            if _DECIMAL_END_RE.search(candidate):
                search_start = match.end()
                continue

            # Too short — skip, will merge with next sentence
            if len(candidate) < MIN_SENTENCE_LENGTH:
                search_start = match.end()
                continue

            sentences.append(candidate)
            self._buffer = self._buffer[match.end():]
            search_start = 0  # Reset for next search on updated buffer

        # Force-split if buffer is too long (LLM producing a run-on)
        if len(self._buffer) > MAX_BUFFER_LENGTH:
            matches = list(_CLAUSE_SPLIT_RE.finditer(self._buffer))
            if matches:
                last_match = matches[-1]
                split_pos = last_match.start() + 1  # Include the comma/semicolon
                sentence = self._buffer[:split_pos].strip()
                self._buffer = self._buffer[last_match.end():]
                if sentence:
                    sentences.append(sentence)

        return sentences

    def flush(self) -> str | None:
        """Return any remaining text as a final sentence."""
        remaining = self._buffer.strip()
        self._buffer = ""
        return remaining if remaining else None


class PipelineOrchestrator:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.max_history = 10

    @property
    def rag_enabled(self) -> bool:
        session = session_manager.get(self.session_id)
        return session.rag_enabled if session else True

    @rag_enabled.setter
    def rag_enabled(self, value: bool):
        session_manager.set_rag_enabled(self.session_id, value)

    def _get_history(self) -> list[dict]:
        return session_manager.get_history(self.session_id, self.max_history * 2)

    def _append_history(self, role: str, content: str):
        session_manager.append_history(self.session_id, role, content)

    def _detect_page_request(self, query: str) -> tuple[int, str | None] | None:
        """Detect if the user is asking to read a specific page. Returns (page_number, doc_name_hint) or None."""
        match = _PAGE_REQUEST_RE.search(query)
        if match:
            # Page number is in group 1, 2, or 3 (whichever branch matched)
            page_str = match.group(1) or match.group(2) or match.group(3)
            if not page_str:
                return None
            page_num = int(page_str)
            doc_hint = match.group(4).strip() if match.group(4) else None
            return page_num, doc_hint
        return None

    def _fetch_page(self, page_num: int, doc_hint: str | None) -> tuple[str, list[dict]] | None:
        """Fetch a full page by number. Resolves document by hint or falls back to session docs."""
        session_docs = rag_service.list_documents(session_id=self.session_id)
        if not session_docs:
            return None

        target_doc = None
        if doc_hint:
            # Fuzzy match: check if hint appears in filename (case-insensitive)
            hint_lower = doc_hint.lower()
            for doc in session_docs:
                if hint_lower in doc["filename"].lower():
                    target_doc = doc
                    break
            # Fall back to first doc with pages if no match
            if not target_doc:
                for doc in session_docs:
                    if doc.get("page_count", 0) > 0:
                        target_doc = doc
                        break
        else:
            # No hint — use only doc if there's one, or first doc with pages
            if len(session_docs) == 1:
                target_doc = session_docs[0]
            else:
                for doc in session_docs:
                    if doc.get("page_count", 0) > 0:
                        target_doc = doc
                        break

        if not target_doc:
            return None

        page = rag_service.get_page(target_doc["doc_id"], page_num)
        if not page:
            return None

        context = f"[Full page {page_num} from {page['filename']}]\n{page['text']}"
        sources = [{
            "filename": page["filename"],
            "page_number": page_num,
            "doc_id": page["doc_id"],
        }]
        return context, sources

    def _try_direct_page_read(self, query: str) -> dict | None:
        """If the query is a page-read request, return the page text directly (bypass LLM).
        Returns {"text": ..., "sources": [...]} or None.
        """
        page_req = self._detect_page_request(query)
        if not page_req:
            return None
        page_num, doc_hint = page_req
        result = self._fetch_page(page_num, doc_hint)
        if not result:
            return None
        page_text, sources = result
        # Strip the [Full page ...] label — return raw page content
        text = page_text.split("\n", 1)[1] if "\n" in page_text else page_text
        return {"text": text, "sources": sources}

    def _build_context(self, query: str) -> tuple[str, list[dict]]:
        """Build RAG context and collect source citations.
        Returns (context_string, sources_list).
        """
        if not self.rag_enabled:
            return "", []

        # Try page-level retrieval first (full pages = better context for small models)
        page_results = rag_service.retrieve_pages(query, k=3, session_id=self.session_id)
        if page_results:
            doc_results = page_results
        else:
            # Fall back to chunk-level for plain text docs without page structure
            doc_results = rag_service.retrieve(query, session_id=self.session_id)

        conv_chunks = rag_service.retrieve_conversations(query, session_id=self.session_id)

        parts = []
        sources = []
        seen_sources = set()

        if doc_results:
            doc_parts = []
            for chunk in doc_results:
                page_num = chunk.get("page_number", -1)
                filename = chunk.get("filename", "")
                label = f"[Source: {filename}"
                if page_num >= 0:
                    label += f", page {page_num}"
                label += "]"
                doc_parts.append(f"{label}\n{chunk['text']}")

                # Deduplicate sources
                source_key = (chunk.get("doc_id", ""), page_num)
                if source_key not in seen_sources:
                    seen_sources.add(source_key)
                    sources.append({
                        "filename": filename,
                        "page_number": page_num if page_num >= 0 else None,
                        "doc_id": chunk.get("doc_id", ""),
                    })

            parts.append("Document context:\n" + "\n---\n".join(doc_parts))

        if conv_chunks:
            parts.append("Previous conversations:\n" + "\n---\n".join(conv_chunks))

        context = "\n\n".join(parts)
        return context, sources

    async def process_voice(
        self,
        audio_bytes: bytes,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        status_callback=None,
    ) -> dict:
        """Full pipeline: audio → STT → RAG → LLM → TTS → audio."""
        timings = {}

        # 1. STT
        if status_callback:
            await status_callback("transcribing")
        t0 = time.time()
        transcript, duration = stt_service.transcribe_bytes(audio_bytes)
        timings["stt_ms"] = int((time.time() - t0) * 1000)
        logger.info("STT: '%s' (%.1fs audio, %dms)", transcript, duration, timings["stt_ms"])

        if not transcript.strip():
            return {
                "transcript": "",
                "response_text": "",
                "audio_url": None,
                "timings": timings,
                "sources": [],
            }

        # 2. Check for direct page read (bypass LLM)
        if status_callback:
            await status_callback("retrieving")
        t0 = time.time()
        direct = self._try_direct_page_read(transcript)
        if direct:
            response_text = direct["text"]
            sources = direct["sources"]
            timings["rag_ms"] = int((time.time() - t0) * 1000)
            timings["llm_ms"] = 0
            logger.info("Direct page read: %d chars", len(response_text))
        else:
            # 2b. RAG retrieval
            context, sources = self._build_context(transcript)
            timings["rag_ms"] = int((time.time() - t0) * 1000)

            # 3. LLM generation
            if status_callback:
                await status_callback("thinking")
            t0 = time.time()
            response_text = await llm_service.generate(
                user_message=transcript,
                context=context,
                conversation_history=self._get_history(),
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            timings["llm_ms"] = int((time.time() - t0) * 1000)
            logger.info("LLM: %d chars, %dms", len(response_text), timings["llm_ms"])

        # 4. TTS
        if status_callback:
            await status_callback("speaking")
        t0 = time.time()
        audio_wav = tts_service.synthesize(response_text)
        timings["tts_ms"] = int((time.time() - t0) * 1000)
        logger.info("TTS: %d bytes, %dms", len(audio_wav), timings["tts_ms"])

        # Save audio to file
        audio_id = str(uuid.uuid4())
        audio_path = AUDIO_OUTPUT_DIR / f"{audio_id}.wav"
        audio_path.write_bytes(audio_wav)

        # 5. Update conversation history and memory
        self._append_history("user", transcript)
        self._append_history("assistant", response_text)

        try:
            rag_service.store_conversation(transcript, response_text, session_id=self.session_id)
        except Exception as e:
            logger.warning("Failed to store conversation memory: %s", e)

        return {
            "transcript": transcript,
            "response_text": response_text,
            "audio_url": f"/audio/{audio_id}.wav",
            "audio_bytes": audio_wav,
            "timings": timings,
            "sources": sources,
        }

    async def process_voice_stream(
        self,
        audio_bytes: bytes,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
        status_callback=None,
        audio_chunk_callback=None,
        transcript_callback=None,
        cancel_event: asyncio.Event | None = None,
    ) -> dict:
        """Streaming voice pipeline: STT → RAG → LLM stream → sentence TTS → audio chunks."""
        timings = {}
        loop = asyncio.get_event_loop()

        # 1. STT
        if status_callback:
            await status_callback("transcribing")
        t0 = time.time()
        transcript, duration = stt_service.transcribe_bytes(audio_bytes)
        timings["stt_ms"] = int((time.time() - t0) * 1000)
        logger.info("STT: '%s' (%.1fs audio, %dms)", transcript, duration, timings["stt_ms"])

        if not transcript.strip():
            return {"transcript": "", "response_text": "", "timings": timings, "sources": []}

        # Send transcript immediately so user sees it in the UI
        if transcript_callback:
            await transcript_callback(transcript)

        # 2. Check for direct page read (bypass LLM)
        if status_callback:
            await status_callback("retrieving")
        t0 = time.time()
        direct = self._try_direct_page_read(transcript)

        if direct:
            # Direct page read — feed page text through SentenceBuffer → TTS
            full_response = direct["text"]
            sources = direct["sources"]
            timings["rag_ms"] = int((time.time() - t0) * 1000)
            timings["llm_ms"] = 0
            logger.info("Direct page read (stream): %d chars", len(full_response))

            if status_callback:
                await status_callback("speaking")

            chunk_index = 0
            tts_start = time.time()
            buf = SentenceBuffer()
            # Feed the text word-by-word through SentenceBuffer to get natural TTS chunks
            for word in full_response.split():
                if cancel_event and cancel_event.is_set():
                    break
                for sentence in buf.add(word + " "):
                    try:
                        wav_bytes = await loop.run_in_executor(
                            None, tts_service.synthesize, sentence
                        )
                        if audio_chunk_callback:
                            await audio_chunk_callback(wav_bytes, chunk_index)
                        chunk_index += 1
                    except Exception as e:
                        logger.error("TTS failed for chunk %d: %s", chunk_index, e)
                        chunk_index += 1

            remaining = buf.flush()
            if remaining:
                try:
                    wav_bytes = await loop.run_in_executor(
                        None, tts_service.synthesize, remaining
                    )
                    if audio_chunk_callback:
                        await audio_chunk_callback(wav_bytes, chunk_index)
                    chunk_index += 1
                except Exception as e:
                    logger.error("TTS failed for final chunk: %s", e)

            timings["tts_first_chunk_ms"] = int((time.time() - tts_start) * 1000)
            timings["tts_chunks"] = chunk_index

        else:
            # Normal flow: RAG retrieval → streaming LLM → TTS
            context, sources = self._build_context(transcript)
            timings["rag_ms"] = int((time.time() - t0) * 1000)

            # 3. Streaming LLM → sentence buffer → TTS → audio chunks
            if status_callback:
                await status_callback("thinking")

            sentence_queue: asyncio.Queue[str | None] = asyncio.Queue()
            full_response = ""
            chunk_index = 0
            llm_start = time.time()
            first_token_time = None
            first_chunk_time = None

            async def produce_sentences():
                """Stream LLM tokens, buffer into sentences, enqueue."""
                nonlocal full_response, first_token_time
                buf = SentenceBuffer()
                async for token in llm_service.generate_stream(
                    user_message=transcript,
                    context=context,
                    conversation_history=self._get_history(),
                    model=model,
                    temperature=temperature,
                    max_tokens=max_tokens,
                ):
                    if cancel_event and cancel_event.is_set():
                        return
                    if first_token_time is None:
                        first_token_time = time.time()
                    full_response += token
                    for sentence in buf.add(token):
                        await sentence_queue.put(sentence)

                remaining = buf.flush()
                if remaining:
                    await sentence_queue.put(remaining)
                await sentence_queue.put(None)  # sentinel

            async def consume_sentences():
                """Dequeue sentences, synthesize in thread pool, send audio chunks."""
                nonlocal chunk_index, first_chunk_time
                while True:
                    sentence = await sentence_queue.get()
                    if sentence is None:
                        break
                    if cancel_event and cancel_event.is_set():
                        break

                    if status_callback and chunk_index == 0:
                        await status_callback("speaking")

                    try:
                        wav_bytes = await loop.run_in_executor(
                            None, tts_service.synthesize, sentence
                        )
                        if first_chunk_time is None:
                            first_chunk_time = time.time()
                        if audio_chunk_callback:
                            await audio_chunk_callback(wav_bytes, chunk_index)
                        chunk_index += 1
                    except Exception as e:
                        logger.error("TTS failed for chunk %d: %s", chunk_index, e)
                        chunk_index += 1

            producer = asyncio.create_task(produce_sentences())
            consumer = asyncio.create_task(consume_sentences())
            await asyncio.gather(producer, consumer)

            total_time = time.time() - llm_start
            timings["llm_ms"] = int(total_time * 1000)
            if first_token_time:
                timings["llm_first_token_ms"] = int((first_token_time - llm_start) * 1000)
            if first_chunk_time:
                timings["tts_first_chunk_ms"] = int((first_chunk_time - llm_start) * 1000)
            timings["tts_chunks"] = chunk_index
            logger.info(
                "Stream: %d chars, %d chunks, first_audio=%dms, total=%dms",
                len(full_response), chunk_index,
                timings.get("tts_first_chunk_ms", 0), timings["llm_ms"],
            )

        # 4. Update conversation history and memory
        self._append_history("user", transcript)
        self._append_history("assistant", full_response)

        try:
            rag_service.store_conversation(transcript, full_response, session_id=self.session_id)
        except Exception as e:
            logger.warning("Failed to store conversation memory: %s", e)

        return {
            "transcript": transcript,
            "response_text": full_response,
            "timings": timings,
            "sources": sources,
        }

    async def process_text(
        self,
        message: str,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ) -> dict:
        """Text-only pipeline: text → RAG → LLM → text."""
        timings = {}

        # Check for direct page read (bypass LLM)
        t0 = time.time()
        direct = self._try_direct_page_read(message)
        if direct:
            response_text = direct["text"]
            sources = direct["sources"]
            timings["rag_ms"] = int((time.time() - t0) * 1000)
            timings["llm_ms"] = 0
            logger.info("Direct page read (text): %d chars", len(response_text))
        else:
            # RAG retrieval
            context, sources = self._build_context(message)
            timings["rag_ms"] = int((time.time() - t0) * 1000)

            # LLM generation
            t0 = time.time()
            response_text = await llm_service.generate(
                user_message=message,
                context=context,
                conversation_history=self._get_history(),
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            )
            timings["llm_ms"] = int((time.time() - t0) * 1000)

        # Update history
        self._append_history("user", message)
        self._append_history("assistant", response_text)

        try:
            rag_service.store_conversation(message, response_text, session_id=self.session_id)
        except Exception as e:
            logger.warning("Failed to store conversation memory: %s", e)

        return {
            "response": response_text,
            "timings": timings,
            "sources": sources,
        }

    async def process_text_stream(
        self,
        message: str,
        model: str | None = None,
        temperature: float = 0.7,
        max_tokens: int = 512,
    ):
        """Streaming text pipeline: text → RAG → LLM (stream) → tokens."""
        # Check for direct page read (bypass LLM)
        direct = self._try_direct_page_read(message)
        if direct:
            full_response = direct["text"]
            logger.info("Direct page read (text_stream): %d chars", len(full_response))
            yield full_response
        else:
            context, sources = self._build_context(message)

            full_response = ""
            async for token in llm_service.generate_stream(
                user_message=message,
                context=context,
                conversation_history=self._get_history(),
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
            ):
                full_response += token
                yield token

        # Update history after streaming completes
        self._append_history("user", message)
        self._append_history("assistant", full_response)

        try:
            rag_service.store_conversation(message, full_response, session_id=self.session_id)
        except Exception as e:
            logger.warning("Failed to store conversation memory: %s", e)


# --- Session-keyed orchestrator factory ---

_orchestrators: dict[str, PipelineOrchestrator] = {}


def get_orchestrator(session_id: str) -> PipelineOrchestrator:
    """Get or create an orchestrator for a given session."""
    if session_id not in _orchestrators:
        _orchestrators[session_id] = PipelineOrchestrator(session_id)
    return _orchestrators[session_id]


def remove_orchestrator(session_id: str):
    """Remove an orchestrator when a session is deleted."""
    _orchestrators.pop(session_id, None)
