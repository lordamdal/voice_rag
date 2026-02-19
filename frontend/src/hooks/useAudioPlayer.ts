import { useRef, useCallback } from "react";
import { base64ToBlob } from "../utils/audioUtils";

export function useAudioPlayer() {
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const queueRef = useRef<string[]>([]);
  const playingRef = useRef(false);

  const playBase64 = useCallback((b64: string) => {
    queueRef.current.push(b64);
    if (!playingRef.current) processQueue();
  }, []);

  const playUrl = useCallback((url: string) => {
    if (audioRef.current) {
      audioRef.current.pause();
    }
    const audio = new Audio(url);
    audioRef.current = audio;
    audio.play().catch(console.error);
  }, []);

  const processQueue = useCallback(() => {
    const b64 = queueRef.current.shift();
    if (!b64) {
      playingRef.current = false;
      return;
    }
    playingRef.current = true;
    const blob = base64ToBlob(b64);
    const url = URL.createObjectURL(blob);
    const audio = new Audio(url);
    audioRef.current = audio;
    audio.onended = () => {
      URL.revokeObjectURL(url);
      processQueue();
    };
    audio.onerror = () => {
      URL.revokeObjectURL(url);
      processQueue();
    };
    audio.play().catch(() => processQueue());
  }, []);

  const stop = useCallback(() => {
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    queueRef.current = [];
    playingRef.current = false;
  }, []);

  return { playBase64, playUrl, stop };
}
