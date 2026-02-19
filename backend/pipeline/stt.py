import io
import logging
import wave
import numpy as np
from faster_whisper import WhisperModel
from config import WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE, AUDIO_SAMPLE_RATE

logger = logging.getLogger(__name__)


class STTService:
    def __init__(self):
        self._model: WhisperModel | None = None

    def load(self):
        logger.info(
            "Loading Whisper model=%s device=%s compute_type=%s",
            WHISPER_MODEL, WHISPER_DEVICE, WHISPER_COMPUTE_TYPE,
        )
        self._model = WhisperModel(
            WHISPER_MODEL,
            device=WHISPER_DEVICE,
            compute_type=WHISPER_COMPUTE_TYPE,
        )
        logger.info("Whisper model loaded")

    @property
    def model(self) -> WhisperModel:
        if self._model is None:
            raise RuntimeError("STT model not loaded. Call load() first.")
        return self._model

    def transcribe_bytes(self, audio_bytes: bytes) -> tuple[str, float]:
        """Transcribe WAV/PCM audio bytes. Returns (text, duration_seconds)."""
        audio_array = self._bytes_to_array(audio_bytes)
        segments, info = self.model.transcribe(
            audio_array,
            beam_size=1,
            language="en",
            vad_filter=True,
        )
        text = " ".join(seg.text.strip() for seg in segments)
        return text, info.duration

    def _bytes_to_array(self, audio_bytes: bytes) -> np.ndarray:
        """Convert WAV or raw PCM bytes to float32 numpy array."""
        try:
            with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                frames = wf.readframes(wf.getnframes())
                sample_width = wf.getsampwidth()
                if sample_width == 2:
                    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                elif sample_width == 4:
                    audio = np.frombuffer(frames, dtype=np.int32).astype(np.float32) / 2147483648.0
                else:
                    audio = np.frombuffer(frames, dtype=np.int16).astype(np.float32) / 32768.0
                return audio
        except wave.Error:
            # Assume raw PCM 16-bit
            audio = np.frombuffer(audio_bytes, dtype=np.int16).astype(np.float32) / 32768.0
            return audio


stt_service = STTService()
