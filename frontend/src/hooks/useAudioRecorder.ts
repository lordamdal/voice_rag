import { useRef, useCallback, useState } from "react";
import { float32ToWav, downsample } from "../utils/audioUtils";
import { useChatStore } from "../stores/chatStore";

const TARGET_SAMPLE_RATE = 16000;

// VAD thresholds
const VAD_ENERGY_THRESHOLD = 0.01; // RMS level to detect speech
const VAD_SILENCE_DURATION_MS = 900; // Silence before triggering end-of-speech
const VAD_SPEECH_MIN_MS = 200; // Minimum speech duration to avoid false triggers

export interface AudioRecorderOptions {
  onAudioChunk?: (pcm16: ArrayBuffer) => void;
  onSpeechStart?: () => void;
  onSpeechEnd?: () => void;
}

/** Convert Float32 [-1, 1] to Int16 PCM ArrayBuffer for WebSocket streaming */
function float32ToPcm16(float32: Float32Array): Int16Array {
  const pcm16 = new Int16Array(float32.length);
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]));
    pcm16[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return pcm16;
}

export function useAudioRecorder(options?: AudioRecorderOptions) {
  const [isRecording, setIsRecording] = useState(false);
  const mediaStreamRef = useRef<MediaStream | null>(null);
  const audioContextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);
  const analyserRef = useRef<AnalyserNode | null>(null);

  // VAD state refs
  const continuousModeRef = useRef(false);
  const vadStateRef = useRef<"silence" | "speech">("silence");
  const silenceStartRef = useRef(0);
  const speechStartRef = useRef(0);
  const optionsRef = useRef(options);
  optionsRef.current = options;

  const setupAudioPipeline = useCallback(
    async (continuous: boolean) => {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          sampleRate: TARGET_SAMPLE_RATE,
          echoCancellation: true,
          noiseSuppression: true,
        },
      });

      const audioContext = new AudioContext({ sampleRate: TARGET_SAMPLE_RATE });
      const source = audioContext.createMediaStreamSource(stream);

      const analyser = audioContext.createAnalyser();
      analyser.fftSize = 2048;
      source.connect(analyser);

      const processor = audioContext.createScriptProcessor(4096, 1, 1);
      analyser.connect(processor);
      processor.connect(audioContext.destination);

      chunksRef.current = [];

      processor.onaudioprocess = (e) => {
        const float32Data = e.inputBuffer.getChannelData(0);

        // Manual mode: just buffer chunks
        if (!continuousModeRef.current) {
          chunksRef.current.push(new Float32Array(float32Data));
          return;
        }

        // Continuous mode: VAD + streaming

        // Compute RMS energy
        let sumSquares = 0;
        for (let i = 0; i < float32Data.length; i++) {
          sumSquares += float32Data[i] * float32Data[i];
        }
        const rms = Math.sqrt(sumSquares / float32Data.length);
        const isSpeech = rms > VAD_ENERGY_THRESHOLD;
        const now = Date.now();

        // VAD state machine
        if (vadStateRef.current === "silence") {
          if (isSpeech) {
            vadStateRef.current = "speech";
            speechStartRef.current = now;
            silenceStartRef.current = 0;
            optionsRef.current?.onSpeechStart?.();
          }
        } else {
          // In speech state
          if (isSpeech) {
            silenceStartRef.current = 0; // Reset silence timer
          } else {
            if (silenceStartRef.current === 0) {
              silenceStartRef.current = now;
            } else if (
              now - silenceStartRef.current >
              VAD_SILENCE_DURATION_MS
            ) {
              const speechDuration = now - speechStartRef.current;
              if (speechDuration > VAD_SPEECH_MIN_MS) {
                // Real end of speech
                vadStateRef.current = "silence";
                silenceStartRef.current = 0;
                optionsRef.current?.onSpeechEnd?.();
              } else {
                // Too short, false trigger — reset
                vadStateRef.current = "silence";
                silenceStartRef.current = 0;
              }
              return;
            }
          }
        }

        // Stream PCM16 chunks to backend while in speech state
        if (vadStateRef.current === "speech") {
          const pcm16 = float32ToPcm16(float32Data);
          optionsRef.current?.onAudioChunk?.(pcm16.buffer);
        }
      };

      mediaStreamRef.current = stream;
      audioContextRef.current = audioContext;
      processorRef.current = processor;
      analyserRef.current = analyser;
    },
    []
  );

  const teardownAudio = useCallback(() => {
    if (processorRef.current) {
      processorRef.current.disconnect();
      processorRef.current = null;
    }
    if (audioContextRef.current) {
      audioContextRef.current.close();
      audioContextRef.current = null;
    }
    if (mediaStreamRef.current) {
      mediaStreamRef.current.getTracks().forEach((t) => t.stop());
      mediaStreamRef.current = null;
    }
    analyserRef.current = null;
  }, []);

  // --- Manual mode (existing behavior) ---

  const startRecording = useCallback(async () => {
    continuousModeRef.current = false;
    await setupAudioPipeline(false);
    setIsRecording(true);
  }, [setupAudioPipeline]);

  const stopRecording = useCallback((): Blob | null => {
    teardownAudio();
    setIsRecording(false);

    if (chunksRef.current.length === 0) return null;

    const totalLength = chunksRef.current.reduce((s, c) => s + c.length, 0);
    const merged = new Float32Array(totalLength);
    let offset = 0;
    for (const chunk of chunksRef.current) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }
    chunksRef.current = [];

    return float32ToWav(merged, TARGET_SAMPLE_RATE);
  }, [teardownAudio]);

  // --- Continuous mode (new) ---

  const startContinuous = useCallback(async () => {
    continuousModeRef.current = true;
    vadStateRef.current = "silence";
    silenceStartRef.current = 0;
    speechStartRef.current = 0;
    await setupAudioPipeline(true);
  }, [setupAudioPipeline]);

  const stopContinuous = useCallback(() => {
    continuousModeRef.current = false;
    vadStateRef.current = "silence";
    silenceStartRef.current = 0;
    teardownAudio();
    setIsRecording(false);
  }, [teardownAudio]);

  const pauseVAD = useCallback(() => {
    vadStateRef.current = "silence";
    silenceStartRef.current = 0;
  }, []);

  return {
    isRecording,
    startRecording,
    stopRecording,
    startContinuous,
    stopContinuous,
    pauseVAD,
    analyserRef,
  };
}
