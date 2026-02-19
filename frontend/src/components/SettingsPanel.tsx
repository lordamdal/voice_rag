import { useEffect, useRef, useState } from "react";
import { useChatStore } from "../stores/chatStore";

interface Voice {
  id: string;
  label: string;
}

export function SettingsPanel({ open, onClose }: { open: boolean; onClose: () => void }) {
  const {
    ragEnabled,
    setRagEnabled,
    currentModel,
    setCurrentModel,
    availableModels,
    setAvailableModels,
    temperature,
    setTemperature,
    maxTokens,
    setMaxTokens,
  } = useChatStore();

  const loadedRef = useRef(false);
  const [voices, setVoices] = useState<Voice[]>([]);
  const [currentVoice, setCurrentVoice] = useState("af_heart");

  useEffect(() => {
    if (!loadedRef.current) {
      loadedRef.current = true;
      fetch("/api/models")
        .then((r) => r.json())
        .then((models) => {
          setAvailableModels(models.map((m: { name: string }) => m.name));
        })
        .catch(() => {});
      fetch("/api/voices")
        .then((r) => r.json())
        .then((data) => {
          setVoices(data.voices);
          setCurrentVoice(data.current);
        })
        .catch(() => {});
    }
  }, [setAvailableModels]);

  const handleRagToggle = async (enabled: boolean) => {
    setRagEnabled(enabled);
    const sessionId = useChatStore.getState().activeSessionId;
    if (sessionId) {
      await fetch(`/api/sessions/${sessionId}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ rag_enabled: enabled }),
      }).catch(() => {});
    }
  };

  const handleModelChange = async (model: string) => {
    setCurrentModel(model);
    await fetch("/api/settings", {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ model }),
    }).catch(() => {});
  };

  const handleVoiceChange = async (voice: string) => {
    setCurrentVoice(voice);
    await fetch("/api/voices", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ voice }),
    }).catch(() => {});
  };

  if (!open) return null;

  return (
    <div className="absolute right-0 top-0 h-full w-72 bg-slate-800 border-l border-slate-700 p-4 space-y-5 z-20 overflow-y-auto">
      <div className="flex items-center justify-between">
        <h3 className="text-sm font-semibold text-slate-200">Settings</h3>
        <button
          onClick={onClose}
          className="text-slate-400 hover:text-white"
        >
          <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
            <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
          </svg>
        </button>
      </div>

      {/* Model selector */}
      <div>
        <label className="block text-xs text-slate-400 mb-1">Model</label>
        <select
          value={currentModel}
          onChange={(e) => handleModelChange(e.target.value)}
          className="w-full bg-slate-700 text-sm text-slate-200 rounded px-2 py-1.5 border border-slate-600 focus:border-blue-500 outline-none"
        >
          {availableModels.length > 0 ? (
            availableModels.map((m) => (
              <option key={m} value={m}>
                {m}
              </option>
            ))
          ) : (
            <option value={currentModel}>{currentModel}</option>
          )}
        </select>
      </div>

      {/* Voice selector */}
      <div>
        <label className="block text-xs text-slate-400 mb-1">Voice</label>
        <select
          value={currentVoice}
          onChange={(e) => handleVoiceChange(e.target.value)}
          className="w-full bg-slate-700 text-sm text-slate-200 rounded px-2 py-1.5 border border-slate-600 focus:border-blue-500 outline-none"
        >
          {voices.length > 0 ? (
            voices.map((v) => (
              <option key={v.id} value={v.id}>
                {v.label}
              </option>
            ))
          ) : (
            <option value={currentVoice}>{currentVoice}</option>
          )}
        </select>
      </div>

      {/* RAG toggle */}
      <div className="flex items-center justify-between">
        <label className="text-xs text-slate-400">RAG enabled</label>
        <button
          onClick={() => handleRagToggle(!ragEnabled)}
          className={`relative w-10 h-5 rounded-full transition-colors ${
            ragEnabled ? "bg-blue-500" : "bg-slate-600"
          }`}
        >
          <span
            className={`absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform ${
              ragEnabled ? "translate-x-5" : ""
            }`}
          />
        </button>
      </div>

      {/* Temperature */}
      <div>
        <label className="block text-xs text-slate-400 mb-1">
          Temperature: {temperature.toFixed(1)}
        </label>
        <input
          type="range"
          min="0"
          max="2"
          step="0.1"
          value={temperature}
          onChange={(e) => setTemperature(parseFloat(e.target.value))}
          className="w-full accent-blue-500"
        />
      </div>

      {/* Max tokens */}
      <div>
        <label className="block text-xs text-slate-400 mb-1">
          Max tokens: {maxTokens}
        </label>
        <input
          type="range"
          min="64"
          max="2048"
          step="64"
          value={maxTokens}
          onChange={(e) => setMaxTokens(parseInt(e.target.value))}
          className="w-full accent-blue-500"
        />
      </div>
    </div>
  );
}
