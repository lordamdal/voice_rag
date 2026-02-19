# Voice RAG: A Lightweight Streaming Voice Assistant with Page-Level Document Retrieval

**Ahmed Ali (Amdal)**
Beaverhand Inc.

February 2026

---

## Abstract

We present Voice RAG, a fully local voice assistant that combines real-time speech interaction with retrieval-augmented generation over uploaded documents. The system achieves low-latency voice responses through a novel streaming pipeline that synthesizes speech sentence-by-sentence as the language model generates tokens, rather than waiting for full response completion. For document interaction, we introduce page-level RAG retrieval that stores and retrieves complete PDF pages rather than small text fragments, significantly improving context quality for small language models. A direct page-read bypass mechanism detects verbatim page requests and returns raw document content without LLM processing. The entire system runs on consumer hardware (4GB GPU, 16GB RAM) using open-source models with no cloud dependencies.

---

## 1. Introduction

Voice assistants have become ubiquitous, but most rely on cloud infrastructure for speech recognition, language understanding, and speech synthesis. This creates privacy concerns, introduces network latency, and requires ongoing API costs. Meanwhile, open-source language models, speech-to-text, and text-to-speech systems have reached sufficient quality for conversational use on consumer hardware.

Retrieval-augmented generation (RAG) extends language models by grounding responses in user-provided documents. However, most RAG implementations use fixed-size text chunks (typically 256-1024 characters) that fragment sentences and lose document structure. For small language models with limited reasoning capacity, these fragmented chunks provide insufficient context for accurate document-based answers.

This paper describes Voice RAG, a system that addresses three challenges:

1. **Latency**: How to deliver voice responses without waiting for full LLM generation
2. **Retrieval quality**: How to provide meaningful document context to small LLMs
3. **Verbatim access**: How to let users hear exact document content on demand

We solve these through a streaming sentence buffer pipeline, page-level ChromaDB storage with hybrid retrieval, and a regex-based page-read bypass that circumvents the language model entirely.

---

## 2. System Architecture

### 2.1 Overview

Voice RAG consists of a FastAPI backend, a React frontend, and four inference components: speech-to-text (Faster-Whisper), language model (Ollama), text-to-speech (Kokoro), and vector retrieval (ChromaDB with sentence-transformers). Communication between frontend and backend uses WebSocket for streaming voice and REST for text and configuration.

The processing pipeline for voice input follows this sequence:

```
Audio Input → STT → Page Detection → [RAG Retrieval → LLM → Sentence Buffer] → TTS → Audio Output
```

When page detection identifies a verbatim read request, the pipeline bypasses the RAG retrieval and LLM stages entirely, fetching page content directly from the vector store.

### 2.2 Component Selection

All components were selected for CPU compatibility and small footprint:

| Component | Model | Size | Device |
|-----------|-------|------|--------|
| Speech-to-text | Faster-Whisper (base) | ~150MB | CPU |
| Language model | Qwen3 1.7B via Ollama | ~1.4GB | GPU |
| Text-to-speech | Kokoro 82M | ~80MB | CPU |
| Embeddings | all-MiniLM-L6-v2 | ~22MB | CPU |
| Vector store | ChromaDB | Persistent | Disk |

The total inference footprint is approximately 1.7GB of GPU memory (LLM only) plus 250MB of CPU memory for the remaining components. This fits within a 4GB laptop GPU (NVIDIA RTX 500 Ada) with headroom.

---

## 3. Streaming Sentence Buffer Pipeline

### 3.1 Problem

Traditional voice assistants follow a sequential pipeline: generate the complete text response, then synthesize the entire response to speech. For a 200-word response, this means the user waits for both full LLM generation (2-4 seconds on small models) and full TTS synthesis (1-2 seconds) before hearing anything — a combined 3-6 second silence.

### 3.2 Approach

We introduce a sentence buffer that sits between the streaming LLM output and the TTS engine. As the LLM generates tokens one at a time, the buffer accumulates text and detects sentence boundaries. When a complete sentence is identified, it is immediately dispatched to TTS synthesis while the LLM continues generating the next sentence.

This creates a producer-consumer architecture:

- **Producer**: Streams LLM tokens into the sentence buffer, which emits complete sentences into an async queue
- **Consumer**: Dequeues sentences, synthesizes each to WAV audio in a thread pool, and streams the audio chunk to the client via WebSocket

The result is that the user begins hearing the response after the first sentence is generated and synthesized — typically within 1-2 seconds — while subsequent sentences are synthesized in parallel with LLM generation.

### 3.3 Sentence Boundary Detection

Naive splitting on periods fails on abbreviations (Mr., Dr., U.S.), decimal numbers (3.14), and single-letter abbreviations (A.I.). Our sentence buffer handles these cases:

1. **Paragraph breaks**: Split immediately on double newlines
2. **Sentence-ending punctuation**: Split on `.!?` followed by whitespace and an uppercase letter or quotation mark
3. **Abbreviation filtering**: Skip split points where the preceding word matches a known abbreviation set (Mr, Mrs, Dr, Prof, St, Ave, Corp, Inc, etc.)
4. **Single-letter abbreviation detection**: Skip splits after patterns like `U.` in `U.S.A.`
5. **Decimal protection**: Skip splits after digit-period patterns like `3.` in `3.14`
6. **Minimum length enforcement**: Sentences shorter than 20 characters are merged with the next sentence to avoid choppy TTS output
7. **Long buffer fallback**: If the buffer exceeds 500 characters without a sentence break (LLM producing a run-on), force-split at the last clause boundary (comma, semicolon, or colon)

### 3.4 Performance

On our test hardware (RTX 500 Ada 4GB, Intel Core i7), the streaming pipeline achieves:

- **First audio latency**: 1.5-2.5 seconds (STT + first LLM sentence + TTS)
- **Sequential pipeline latency**: 4-7 seconds (full generation + full synthesis)
- **Improvement**: 2-3x reduction in perceived latency

---

## 4. Page-Level RAG Retrieval

### 4.1 Problem

Standard RAG implementations split documents into fixed-size chunks (typically 256-1024 characters) with overlap, embed each chunk, and retrieve the top-k most similar chunks as context. This approach has two problems for small language models:

1. **Context fragmentation**: A 500-character chunk may split mid-sentence or mid-paragraph, losing semantic coherence
2. **Loss of document structure**: Page numbers, section boundaries, and cross-paragraph reasoning are destroyed

When we initially deployed 500-character chunk retrieval with Qwen3 1.7B, the model frequently responded with "I don't have access to documents" or provided incorrect answers, despite relevant chunks being injected into the prompt. The chunks were too fragmented for the small model to synthesize into coherent answers.

### 4.2 Dual-Collection Storage

We maintain three ChromaDB collections:

1. **documents**: Traditional 500-character chunks with cosine similarity embeddings. Each chunk stores metadata including doc_id, filename, chunk_index, page_number, source_type, and session_id.

2. **pages**: Complete page text from PDF documents, one entry per page. Each page is embedded as a single vector. This preserves full paragraphs, tables, and cross-sentence reasoning within each page.

3. **conversations**: Past user-assistant exchanges stored as embeddings for conversational memory retrieval.

For PDF documents, both collections are populated during ingestion: the pages collection receives full page text, and the documents collection receives chunked text with page number metadata.

### 4.3 Hybrid Retrieval Strategy

During retrieval, the system follows a preference order:

1. **Pages first**: Query the pages collection for the top-3 most similar full pages. If results are found, use them as context.
2. **Chunks fallback**: If no page-level results exist (e.g., plain text files without page structure), fall back to the documents collection for top-3 chunks.
3. **Conversation context**: Additionally retrieve up to 2 relevant past conversation exchanges from the conversations collection.

The rationale: a single page typically contains 300-800 words of coherent, structured text. Three retrieved pages provide 900-2400 words of context — sufficient for most questions — while maintaining paragraph and section integrity. For a 1.7B parameter model with limited context synthesis ability, this coherent context dramatically outperforms fragmented chunks.

### 4.4 Context Injection

A critical finding during development: small language models (1.7B parameters) largely ignore information placed in the system message. We moved document context from the system prompt into the user message, formatted as:

```
Use the following document text to answer my question.

Document text:
---
[Source: paper.pdf, page 3]
Full page text here...
---

My question: What does section 2 discuss?
```

This placement ensures the model treats the document text as part of the immediate query rather than background instructions, significantly improving response accuracy.

### 4.5 Session-Scoped Retrieval

Documents and conversations are tagged with a session_id in ChromaDB metadata. Retrieval queries apply a where-filter to return only results from the active session. This prevents cross-session context leakage when users maintain multiple chat threads with different documents.

---

## 5. Direct Page-Read Bypass

### 5.1 Problem

When users ask "read page 3" or "can you read that first page?", the LLM receives the full page as context but invariably summarizes rather than reading verbatim. This is inherent to language model behavior — models are trained to be helpful by summarizing, not by echoing input.

### 5.2 Approach

We bypass the LLM entirely for page-read requests. A multi-pattern regex detects page-read intent:

```
Pattern 1: (read|show|get|give|tell) [me] [about] page [number] {N}
Pattern 2: what (is|'s) on page [number] {N}
Pattern 3: page [number] {N} ... (read|show|get|content|text|what)
Optional:  ... (of|from|in) {document_hint}
```

When a match is found, the orchestrator:

1. Identifies the target document (by hint matching or defaulting to the session's document)
2. Fetches the full page directly from the pages ChromaDB collection by page ID
3. Returns the raw page text as the response
4. Skips LLM generation entirely (llm_ms = 0 in timings)

For the streaming voice pipeline, the page text is fed word-by-word through the same sentence buffer used for LLM output, producing natural TTS audio chunks.

### 5.3 Performance

Direct page reads complete in under 100ms (retrieval only) compared to 2-4 seconds for LLM-mediated responses. The user hears the exact document content with zero summarization or interpretation.

---

## 6. Frontend Design

### 6.1 Dual Voice Modes

The interface supports two voice interaction modes:

- **Manual mode**: Press-to-record, release-to-send. The user controls when recording starts and stops.
- **Continuous mode**: Always-listening with browser-based voice activity detection (VAD). Recording starts automatically when speech is detected and ends after a silence threshold. After the assistant responds, the system automatically resumes listening.

Both modes communicate via the same WebSocket protocol, sending raw PCM16 audio frames and receiving base64-encoded WAV audio chunks.

### 6.2 Real-Time Audio Streaming

Audio chunks arrive via WebSocket as the backend synthesizes each sentence. The frontend maintains an ordered playback queue — each chunk is appended and played sequentially through the Web Audio API. This produces continuous speech output even though audio is generated in discrete sentence-sized pieces.

### 6.3 Source Citations

When the response includes RAG-retrieved content, source citations (filename, page number) are displayed alongside the assistant's message. This provides transparency about which document sections informed the response.

### 6.4 State Management

Application state is managed with Zustand, a lightweight alternative to Redux. The store tracks:

- Messages per session (with timings, sources, timestamps)
- Active session and session list
- Pipeline stage (idle, transcribing, retrieving, thinking, speaking)
- Uploaded documents per session
- Playback state and audio queue

---

## 7. Error Resilience

### 7.1 LLM Retry Logic

Ollama occasionally returns HTTP 500 errors during model loading or GPU memory pressure. The LLM service implements automatic retry with linear backoff:

- Up to 2 retries on status codes 500, 502, 503
- Up to 2 retries on connection and read errors
- Delay: 1 second * attempt number (1s, 2s)
- Non-retryable errors (400, 404, etc.) raise immediately

For streaming generation, retry applies to the initial connection. Once token streaming begins, mid-stream failures are not retried to avoid duplicate output.

### 7.2 User-Facing Error Messages

Pipeline errors are propagated to the frontend via WebSocket error messages and displayed as assistant chat messages ("Sorry, something went wrong. Please try again.") rather than failing silently.

### 7.3 TTS Failure Tolerance

In the streaming pipeline, if TTS synthesis fails for one sentence, that chunk is skipped and the pipeline continues with the next sentence. This prevents a single TTS error from breaking the entire response.

---

## 8. Limitations and Future Work

### 8.1 Current Limitations

- **Model intelligence**: Qwen3 1.7B has limited reasoning capacity for complex questions. Upgrading to 4B or 8B parameters would significantly improve conversation quality.
- **STT accuracy**: Whisper base model occasionally mis-transcribes words, especially domain-specific terminology.
- **Page-read detection**: The regex-based approach handles common phrasings but may miss unusual formulations.
- **Single-user**: The current architecture assumes a single concurrent user. WebSocket sessions are not multiplexed.
- **Document types**: Only PDF, TXT, and MD files are supported. No DOCX, HTML, or spreadsheet support.

### 8.2 Future Directions

- **Larger model support**: The architecture is model-agnostic via Ollama. Upgrading to larger models (Qwen3 8B, Llama 3.1 8B) requires only configuration changes.
- **GPU-accelerated TTS**: Kokoro supports CUDA but currently runs on CPU to reserve GPU memory for the LLM.
- **Multi-modal retrieval**: Incorporating table and image extraction from PDFs alongside text.
- **Agent tool use**: With a sufficiently capable LLM, the orchestrator could support tool-calling for calculations, web search, or structured data queries.
- **Containerization**: Docker packaging for portable deployment.

---

## 9. Conclusion

Voice RAG demonstrates that a fully functional voice assistant with document retrieval can run entirely on consumer hardware using open-source components. The streaming sentence buffer reduces perceived latency by 2-3x compared to sequential pipelines. Page-level RAG retrieval provides coherent context that small language models can effectively use, and the direct page-read bypass enables verbatim document access without LLM intermediation.

The system is designed for privacy-sensitive use cases where documents cannot leave the local machine, and for environments where cloud API costs or network latency are prohibitive. All source code is available for inspection and modification.

---

## References

1. Lewis, P., et al. "Retrieval-Augmented Generation for Knowledge-Intensive NLP Tasks." NeurIPS 2020.
2. Radford, A., et al. "Robust Speech Recognition via Large-Scale Weak Supervision." OpenAI 2022. (Whisper)
3. Qwen Team. "Qwen3 Technical Report." Alibaba 2025.
4. Sentence-Transformers. "all-MiniLM-L6-v2." Hugging Face Model Hub.
5. ChromaDB. "The AI-native open-source embedding database." https://www.trychroma.com
6. Kokoro. "Lightweight text-to-speech model." https://github.com/hexgrad/kokoro

---

*Correspondence: Ahmed Ali (Amdal), Beaverhand Inc.*
