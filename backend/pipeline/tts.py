import io
import logging
import re
import wave

import numpy as np

from config import TTS_VOICE, TTS_LANG_CODE

logger = logging.getLogger(__name__)

# Patterns for text cleaning before TTS
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL)
_MARKDOWN_BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
_MARKDOWN_ITALIC_RE = re.compile(r"\*(.+?)\*")
_MARKDOWN_ITALIC2_RE = re.compile(r"_(.+?)_")
_MARKDOWN_CODE_RE = re.compile(r"`(.+?)`")
_MARKDOWN_HEADER_RE = re.compile(r"^#{1,6}\s+", re.MULTILINE)
_MARKDOWN_BULLET_RE = re.compile(r"^[\s]*[-*+]\s+", re.MULTILINE)
_MARKDOWN_NUMBERED_RE = re.compile(r"^[\s]*\d+\.\s+", re.MULTILINE)
_URL_RE = re.compile(r"https?://\S+")
_EMOJI_RE = re.compile(
    "["
    "\U0001F600-\U0001F64F"  # emoticons
    "\U0001F300-\U0001F5FF"  # symbols & pictographs
    "\U0001F680-\U0001F6FF"  # transport & map symbols
    "\U0001F1E0-\U0001F1FF"  # flags
    "\U00002702-\U000027B0"  # dingbats
    "\U0001F900-\U0001F9FF"  # supplemental symbols
    "\U0001FA00-\U0001FA6F"  # chess symbols
    "\U0001FA70-\U0001FAFF"  # symbols extended-A
    "\U00002600-\U000026FF"  # misc symbols
    "\U0000FE00-\U0000FE0F"  # variation selectors
    "\U0000200D"             # zero width joiner
    "]+",
    re.UNICODE,
)
_MULTI_SPACE_RE = re.compile(r"[ \t]+")
_MULTI_NEWLINE_RE = re.compile(r"\n{2,}")
# Strip emotion tags that Chatterbox supported but Kokoro does not
_EMOTION_TAG_RE = re.compile(
    r"\[(laugh|chuckle|cough|sigh|gasp|sniff|groan|shush|clear throat|pause)\]",
    re.IGNORECASE,
)

# Available Kokoro voices
KOKORO_VOICES = {
    "af_heart": "American Female - Heart",
    "af_alloy": "American Female - Alloy",
    "af_aoede": "American Female - Aoede",
    "af_bella": "American Female - Bella",
    "af_jessica": "American Female - Jessica",
    "af_kore": "American Female - Kore",
    "af_nicole": "American Female - Nicole",
    "af_nova": "American Female - Nova",
    "af_river": "American Female - River",
    "af_sarah": "American Female - Sarah",
    "af_sky": "American Female - Sky",
    "am_adam": "American Male - Adam",
    "am_echo": "American Male - Echo",
    "am_eric": "American Male - Eric",
    "am_liam": "American Male - Liam",
    "am_michael": "American Male - Michael",
    "am_onyx": "American Male - Onyx",
    "am_puck": "American Male - Puck",
    "bf_alice": "British Female - Alice",
    "bf_emma": "British Female - Emma",
    "bf_isabella": "British Female - Isabella",
    "bf_lily": "British Female - Lily",
    "bm_daniel": "British Male - Daniel",
    "bm_fable": "British Male - Fable",
    "bm_george": "British Male - George",
    "bm_lewis": "British Male - Lewis",
}


class TTSService:
    def __init__(self):
        self._pipeline = None
        self._sample_rate: int = 24000
        self._voice: str = TTS_VOICE

    def load(self):
        """Load Kokoro TTS pipeline."""
        try:
            from kokoro import KPipeline

            lang_code = TTS_LANG_CODE
            logger.info("Loading Kokoro TTS (lang=%s, voice=%s)...", lang_code, self._voice)
            self._pipeline = KPipeline(lang_code=lang_code, repo_id="hexgrad/Kokoro-82M")
            logger.info("Kokoro TTS loaded, sample_rate=%d", self._sample_rate)
        except Exception:
            logger.exception("Failed to load Kokoro TTS")
            self._pipeline = None

    def synthesize(self, text: str) -> bytes:
        """Convert text to WAV audio bytes using Kokoro."""
        if self._pipeline is None:
            raise RuntimeError("Kokoro TTS is not loaded")

        text = self._clean_for_tts(text)

        if not text.strip():
            return self._silent_wav()

        # Kokoro yields chunks — collect all and concatenate
        audio_chunks = []
        for _graphemes, _phonemes, audio_chunk in self._pipeline(text, voice=self._voice):
            audio_chunks.append(audio_chunk.numpy())

        if not audio_chunks:
            return self._silent_wav()

        wav_np = np.concatenate(audio_chunks)
        return self._float_to_wav(wav_np)

    def set_voice(self, voice: str):
        """Change the active voice."""
        if voice not in KOKORO_VOICES:
            raise ValueError(f"Unknown voice: {voice}. Available: {list(KOKORO_VOICES.keys())}")

        # If accent changed, need to update lang_code and reload pipeline
        new_lang = voice[0]  # 'a' or 'b'
        old_lang = self._voice[0] if self._voice else "a"

        self._voice = voice
        logger.info("Voice set to: %s (%s)", voice, KOKORO_VOICES[voice])

        if new_lang != old_lang and self._pipeline is not None:
            logger.info("Accent changed, reloading pipeline with lang_code=%s", new_lang)
            from kokoro import KPipeline
            self._pipeline = KPipeline(lang_code=new_lang, repo_id="hexgrad/Kokoro-82M")

    @staticmethod
    def _clean_for_tts(text: str) -> str:
        """Clean text for natural-sounding TTS output."""
        # Strip thinking blocks
        text = _THINK_RE.sub("", text)
        # Remove emotion tags (Kokoro doesn't support them)
        text = _EMOTION_TAG_RE.sub("", text)
        # Remove URLs
        text = _URL_RE.sub("", text)
        # Remove emojis
        text = _EMOJI_RE.sub("", text)
        # Remove markdown formatting (keep the inner text)
        text = _MARKDOWN_BOLD_RE.sub(r"\1", text)
        text = _MARKDOWN_ITALIC_RE.sub(r"\1", text)
        text = _MARKDOWN_ITALIC2_RE.sub(r"\1", text)
        text = _MARKDOWN_CODE_RE.sub(r"\1", text)
        text = _MARKDOWN_HEADER_RE.sub("", text)
        text = _MARKDOWN_BULLET_RE.sub("", text)
        text = _MARKDOWN_NUMBERED_RE.sub("", text)
        # Collapse whitespace
        text = _MULTI_SPACE_RE.sub(" ", text)
        text = _MULTI_NEWLINE_RE.sub(". ", text)
        return text.strip()

    def _float_to_wav(self, wav_np: np.ndarray) -> bytes:
        """Convert float32 audio array to WAV bytes."""
        # Convert float32 [-1, 1] to int16 PCM
        wav_np = np.clip(wav_np, -1.0, 1.0)
        pcm16 = (wav_np * 32767).astype(np.int16)

        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._sample_rate)
            wf.writeframes(pcm16.tobytes())
        return buf.getvalue()

    def _silent_wav(self, duration_ms: int = 100) -> bytes:
        """Generate a short silent WAV for empty text."""
        n_samples = int(self._sample_rate * duration_ms / 1000)
        silence = np.zeros(n_samples, dtype=np.int16)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self._sample_rate)
            wf.writeframes(silence.tobytes())
        return buf.getvalue()


tts_service = TTSService()
