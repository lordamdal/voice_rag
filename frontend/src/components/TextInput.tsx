import { useState, useCallback } from "react";
import { useChatStore } from "../stores/chatStore";

export function TextInput() {
  const [text, setText] = useState("");
  const { addMessage, setStage, stage } = useChatStore();
  const isProcessing = stage !== "idle" && stage !== "listening";

  const handleSend = useCallback(async () => {
    const message = text.trim();
    if (!message || isProcessing) return;

    setText("");
    addMessage({ role: "user", content: message });
    setStage("thinking");

    try {
      // Ensure we have a session
      const store = useChatStore.getState();
      let sessionId = store.activeSessionId;
      if (!sessionId) {
        sessionId = await store.createSession();
      }

      const res = await fetch("/api/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message, session_id: sessionId }),
      });
      const data = await res.json();

      // Update active session if backend created one
      if (data.session_id && data.session_id !== sessionId) {
        useChatStore.getState().setActiveSessionId(data.session_id);
      }

      addMessage({
        role: "assistant",
        content: data.response,
        timings: data.timings,
        sources: data.sources,
      });

      // Refresh session list to pick up title changes
      useChatStore.getState().loadSessions();
    } catch (err) {
      console.error("Chat error:", err);
      addMessage({ role: "assistant", content: "Error: failed to get response." });
    } finally {
      setStage("idle");
    }
  }, [text, isProcessing, addMessage, setStage]);

  return (
    <div className="flex gap-2 p-3 border-t border-slate-700">
      <input
        type="text"
        value={text}
        onChange={(e) => setText(e.target.value)}
        onKeyDown={(e) => e.key === "Enter" && handleSend()}
        placeholder="Type a message..."
        disabled={isProcessing}
        className="flex-1 bg-slate-800 text-sm text-slate-200 rounded-lg px-3 py-2 border border-slate-600 focus:border-blue-500 outline-none placeholder-slate-500 disabled:opacity-50"
      />
      <button
        onClick={handleSend}
        disabled={!text.trim() || isProcessing}
        className="bg-blue-500 hover:bg-blue-600 disabled:bg-slate-600 disabled:cursor-not-allowed text-white text-sm px-4 py-2 rounded-lg transition-colors"
      >
        Send
      </button>
    </div>
  );
}
