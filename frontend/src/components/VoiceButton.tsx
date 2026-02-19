import { useCallback, useEffect, useRef } from "react";
import { useAudioRecorder } from "../hooks/useAudioRecorder";
import { useWebSocket } from "../hooks/useWebSocket";
import { useChatStore } from "../stores/chatStore";

export function VoiceButton() {
  const {
    stage,
    setStage,
    setRecording,
    conversationMode,
    setConversationMode,
    isPlayingResponse,
  } = useChatStore();

  const conversationActiveRef = useRef(false);

  const {
    connected,
    sendAudioData,
    sendEndSignal,
    sendCancel,
    stopPlayback,
    setOnPlaybackComplete,
  } = useWebSocket();

  const {
    isRecording,
    startRecording,
    stopRecording,
    startContinuous,
    stopContinuous,
    pauseVAD,
  } = useAudioRecorder({
    onAudioChunk: (pcm16: ArrayBuffer) => {
      sendAudioData(pcm16);
    },
    onSpeechStart: () => {
      const store = useChatStore.getState();
      // Barge-in: if agent is currently speaking, interrupt it
      if (store.isPlayingResponse) {
        stopPlayback();
        sendCancel();
      }
      store.setRecording(true);
      store.setStage("listening");
    },
    onSpeechEnd: () => {
      const store = useChatStore.getState();
      store.setRecording(false);
      store.setStage("transcribing");
      sendEndSignal({
        model: store.currentModel,
        temperature: store.temperature,
        max_tokens: store.maxTokens,
        session_id: store.activeSessionId || undefined,
      });
    },
  });

  // Auto-restart listening after response playback completes
  useEffect(() => {
    setOnPlaybackComplete(() => {
      if (conversationActiveRef.current) {
        setTimeout(() => {
          if (conversationActiveRef.current) {
            pauseVAD();
            useChatStore.getState().setStage("listening");
          }
        }, 300);
      }
    });
    return () => setOnPlaybackComplete(null);
  }, [setOnPlaybackComplete, pauseVAD]);

  // --- Manual mode handler (existing behavior) ---

  const handleManualToggle = useCallback(async () => {
    if (isRecording) {
      const blob = stopRecording();
      setRecording(false);
      if (blob) {
        setStage("transcribing");
        const formData = new FormData();
        formData.append("audio", blob, "recording.wav");
        const currentSessionId = useChatStore.getState().activeSessionId;
        if (currentSessionId) {
          formData.append("session_id", currentSessionId);
        }
        try {
          const response = await fetch("/api/voice", {
            method: "POST",
            body: formData,
          });
          const result = await response.json();
          const store = useChatStore.getState();
          if (result.session_id && result.session_id !== currentSessionId) {
            store.setActiveSessionId(result.session_id);
            store.loadSessions();
          }
          if (result.transcript) {
            store.addMessage({ role: "user", content: result.transcript });
          }
          if (result.response_text) {
            store.addMessage({
              role: "assistant",
              content: result.response_text,
              timings: result.timings,
              audioUrl: result.audio_url,
              sources: result.sources,
            });
            if (result.audio_url) {
              const audio = new Audio(result.audio_url);
              setStage("speaking");
              audio.onended = () => setStage("idle");
              audio.play().catch(() => setStage("idle"));
            } else {
              setStage("idle");
            }
          } else {
            setStage("idle");
          }
        } catch (err) {
          console.error("Voice pipeline error:", err);
          setStage("idle");
        }
      } else {
        setStage("idle");
      }
    } else {
      try {
        await startRecording();
        setRecording(true);
        setStage("listening");
      } catch (err) {
        console.error("Mic access error:", err);
      }
    }
  }, [isRecording, startRecording, stopRecording, setStage, setRecording]);

  // --- Continuous mode handler ---

  const handleConversationToggle = useCallback(async () => {
    if (conversationMode) {
      conversationActiveRef.current = false;
      setConversationMode(false);
      stopContinuous();
      sendCancel();
      setRecording(false);
      setStage("idle");
    } else {
      try {
        setConversationMode(true);
        conversationActiveRef.current = true;
        await startContinuous();
        setStage("listening");
      } catch (err) {
        console.error("Mic access error:", err);
        setConversationMode(false);
        conversationActiveRef.current = false;
      }
    }
  }, [
    conversationMode,
    setConversationMode,
    startContinuous,
    stopContinuous,
    sendCancel,
    setRecording,
    setStage,
  ]);

  const isProcessing =
    stage !== "idle" && stage !== "listening" && !conversationMode;

  return (
    <div className="flex flex-col items-center gap-3">
      {/* Mode toggle */}
      <div className="flex items-center gap-1">
        <button
          onClick={() => {
            if (conversationMode) {
              conversationActiveRef.current = false;
              setConversationMode(false);
              stopContinuous();
              sendCancel();
              setRecording(false);
              setStage("idle");
            }
          }}
          className={`text-xs px-2.5 py-1 rounded-full transition-colors ${
            !conversationMode
              ? "text-blue-300 bg-blue-500/20 ring-1 ring-blue-500/40"
              : "text-slate-500 hover:text-slate-400"
          }`}
        >
          Manual
        </button>
        <button
          onClick={() => {
            if (!conversationMode && stage === "idle") {
              handleConversationToggle();
            }
          }}
          className={`text-xs px-2.5 py-1 rounded-full transition-colors ${
            conversationMode
              ? "text-green-300 bg-green-500/20 ring-1 ring-green-500/40"
              : "text-slate-500 hover:text-slate-400"
          }`}
        >
          Continuous
        </button>
      </div>

      {/* Main button */}
      <button
        onClick={
          conversationMode ? handleConversationToggle : handleManualToggle
        }
        disabled={isProcessing}
        className={`relative w-20 h-20 rounded-full transition-all duration-300 flex items-center justify-center
          ${
            conversationMode
              ? isRecording
                ? "bg-green-500 hover:bg-green-600 scale-110"
                : isPlayingResponse
                ? "bg-emerald-600"
                : "bg-green-600 hover:bg-green-700"
              : isRecording
              ? "bg-red-500 hover:bg-red-600 scale-110"
              : isProcessing
              ? "bg-slate-600 cursor-not-allowed"
              : "bg-blue-500 hover:bg-blue-600 hover:scale-105"
          }
        `}
      >
        {/* Pulse ring */}
        {(isRecording ||
          (conversationMode &&
            !isPlayingResponse &&
            stage === "listening")) && (
          <span
            className={`absolute inset-0 rounded-full animate-pulse-ring ${
              conversationMode ? "bg-green-400" : "bg-red-400"
            }`}
          />
        )}

        {/* Icon */}
        <svg
          className="w-8 h-8 text-white relative z-10"
          fill="none"
          viewBox="0 0 24 24"
          stroke="currentColor"
          strokeWidth={2}
        >
          {conversationMode ? (
            // Stop icon to exit conversation mode
            <rect
              x="6"
              y="6"
              width="12"
              height="12"
              rx="2"
              fill="currentColor"
            />
          ) : isRecording ? (
            <rect
              x="6"
              y="6"
              width="12"
              height="12"
              rx="1"
              fill="currentColor"
            />
          ) : isProcessing ? (
            <path
              strokeLinecap="round"
              d="M12 2v4m0 12v4m-7.07-3.93l2.83-2.83m8.48-8.48l2.83-2.83M2 12h4m12 0h4m-3.93 7.07l-2.83-2.83M6.76 6.76L3.93 3.93"
              className="animate-spin origin-center"
            />
          ) : (
            <>
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"
              />
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                d="M19 10v2a7 7 0 01-14 0v-2M12 19v4m-4 0h8"
              />
            </>
          )}
        </svg>
      </button>

      <span className="text-xs text-slate-400">
        {conversationMode
          ? isRecording
            ? "Speaking..."
            : isPlayingResponse
            ? "Responding..."
            : stage !== "idle" && stage !== "listening"
            ? stage.charAt(0).toUpperCase() + stage.slice(1) + "..."
            : "Listening... (tap to stop)"
          : isRecording
          ? "Tap to stop"
          : isProcessing
          ? stage.charAt(0).toUpperCase() + stage.slice(1) + "..."
          : "Tap to speak"}
      </span>

      {!connected && (
        <span className="text-xs text-yellow-400">Connecting...</span>
      )}
    </div>
  );
}
