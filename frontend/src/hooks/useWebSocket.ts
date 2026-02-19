import { useRef, useCallback, useEffect, useState } from "react";
import { useChatStore, type PipelineStage, type SourceCitation } from "../stores/chatStore";
import { base64ToBlob } from "../utils/audioUtils";

interface WSMessage {
  type: "status" | "transcript" | "response" | "audio" | "audio_chunk" | "audio_done" | "error";
  stage?: PipelineStage;
  text?: string;
  data?: string;
  index?: number;
  message?: string;
  timings?: Record<string, number>;
  sources?: SourceCitation[];
  session_id?: string;
}

export function useWebSocket() {
  const wsRef = useRef<WebSocket | null>(null);
  const [connected, setConnected] = useState(false);
  const { addMessage, setStage } = useChatStore();
  const audioQueueRef = useRef<Blob[]>([]);
  const playingRef = useRef(false);
  const currentAudioRef = useRef<HTMLAudioElement | null>(null);
  const onPlaybackCompleteRef = useRef<(() => void) | null>(null);

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const ws = new WebSocket(`${protocol}//${window.location.host}/ws/voice`);
    ws.binaryType = "arraybuffer";

    ws.onopen = () => {
      setConnected(true);
    };

    ws.onclose = () => {
      setConnected(false);
      setTimeout(connect, 2000);
    };

    ws.onerror = () => {
      ws.close();
    };

    ws.onmessage = (event) => {
      if (typeof event.data === "string") {
        const msg: WSMessage = JSON.parse(event.data);
        handleMessage(msg);
      }
    };

    wsRef.current = ws;
  }, []);

  const handleMessage = useCallback(
    (msg: WSMessage) => {
      switch (msg.type) {
        case "status":
          if (msg.stage) setStage(msg.stage);
          // If backend returns to idle and no audio is queued/playing,
          // signal completion (handles empty-transcript edge case)
          if (
            msg.stage === "idle" &&
            !playingRef.current &&
            audioQueueRef.current.length === 0
          ) {
            useChatStore.getState().setPlayingResponse(false);
            onPlaybackCompleteRef.current?.();
          }
          break;
        case "transcript":
          if (msg.text) {
            addMessage({ role: "user", content: msg.text });
          }
          break;
        case "response":
          if (msg.text) {
            addMessage({
              role: "assistant",
              content: msg.text,
              timings: msg.timings,
              sources: msg.sources,
            });
            // Update session_id if returned and refresh sessions for title updates
            if (msg.session_id) {
              const store = useChatStore.getState();
              if (!store.activeSessionId || store.activeSessionId !== msg.session_id) {
                store.setActiveSessionId(msg.session_id);
              }
              store.loadSessions();
            }
          }
          break;
        case "audio":
          if (msg.data) {
            const blob = base64ToBlob(msg.data);
            playAudio(blob);
          }
          break;
        case "audio_chunk":
          if (msg.data) {
            const chunkBlob = base64ToBlob(msg.data);
            playAudio(chunkBlob);
          }
          break;
        case "audio_done":
          // All chunks sent. playNext() chain handles sequential playback
          // and fires onPlaybackComplete when queue empties.
          break;
        case "error":
          console.error("Pipeline error:", msg.message);
          addMessage({
            role: "assistant",
            content: "Sorry, something went wrong. Please try again.",
          });
          setStage("idle");
          useChatStore.getState().setPlayingResponse(false);
          onPlaybackCompleteRef.current?.();
          break;
      }
    },
    [addMessage, setStage]
  );

  const playAudio = useCallback((blob: Blob) => {
    audioQueueRef.current.push(blob);
    if (!playingRef.current) {
      playNext();
    }
  }, []);

  const playNext = useCallback(() => {
    const blob = audioQueueRef.current.shift();
    if (!blob) {
      playingRef.current = false;
      useChatStore.getState().setPlayingResponse(false);
      onPlaybackCompleteRef.current?.();
      return;
    }
    playingRef.current = true;
    useChatStore.getState().setPlayingResponse(true);
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    currentAudioRef.current = audio;
    audio.onended = () => {
      URL.revokeObjectURL(url);
      currentAudioRef.current = null;
      playNext();
    };
    audio.onerror = () => {
      URL.revokeObjectURL(url);
      currentAudioRef.current = null;
      playNext();
    };
    audio.play().catch(() => {
      currentAudioRef.current = null;
      playNext();
    });
  }, []);

  const sendAudioData = useCallback((data: ArrayBuffer) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(data);
    }
  }, []);

  const sendEndSignal = useCallback(
    (opts?: { model?: string; temperature?: number; max_tokens?: number; session_id?: string }) => {
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(
          JSON.stringify({ type: "end", ...opts })
        );
      }
    },
    []
  );

  const stopPlayback = useCallback(() => {
    // Stop currently playing audio
    if (currentAudioRef.current) {
      currentAudioRef.current.pause();
      currentAudioRef.current = null;
    }
    // Clear queued audio
    audioQueueRef.current = [];
    playingRef.current = false;
    useChatStore.getState().setPlayingResponse(false);
  }, []);

  const sendCancel = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "cancel" }));
    }
  }, []);

  const setOnPlaybackComplete = useCallback(
    (cb: (() => void) | null) => {
      onPlaybackCompleteRef.current = cb;
    },
    []
  );

  useEffect(() => {
    connect();
    return () => {
      wsRef.current?.close();
    };
  }, [connect]);

  return {
    connected,
    sendAudioData,
    sendEndSignal,
    sendCancel,
    stopPlayback,
    setOnPlaybackComplete,
  };
}
