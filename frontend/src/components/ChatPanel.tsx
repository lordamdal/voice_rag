import { useEffect, useRef } from "react";
import { useChatStore } from "../stores/chatStore";

export function ChatPanel() {
  const messages = useChatStore((s) => s.messages);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    scrollRef.current?.scrollTo({
      top: scrollRef.current.scrollHeight,
      behavior: "smooth",
    });
  }, [messages]);

  return (
    <div ref={scrollRef} className="flex-1 overflow-y-auto p-4 space-y-3">
      {messages.length === 0 && (
        <div className="flex flex-col items-center justify-center h-full text-slate-500">
          <svg className="w-16 h-16 mb-4 opacity-30" fill="none" viewBox="0 0 24 24" stroke="currentColor">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
          </svg>
          <p className="text-sm">Tap the mic to start talking</p>
          <p className="text-xs mt-1">or type a message below</p>
        </div>
      )}

      {messages.map((msg) => (
        <div
          key={msg.id}
          className={`flex ${msg.role === "user" ? "justify-end" : "justify-start"}`}
        >
          <div
            className={`max-w-[80%] rounded-2xl px-4 py-2.5 ${
              msg.role === "user"
                ? "bg-blue-600 text-white rounded-br-md"
                : "bg-slate-700 text-slate-100 rounded-bl-md"
            }`}
          >
            <p className="text-sm whitespace-pre-wrap">{msg.content}</p>

            {/* Source citations */}
            {msg.role === "assistant" && msg.sources && msg.sources.length > 0 && (
              <div className="mt-2 pt-2 border-t border-slate-600/50 flex flex-wrap items-center gap-1">
                <span className="text-[10px] text-slate-400">Sources:</span>
                {msg.sources.map((s, i) => (
                  <span
                    key={i}
                    className="text-[10px] bg-slate-600/50 text-slate-300 rounded px-1.5 py-0.5"
                  >
                    {s.filename}
                    {s.page_number != null && s.page_number >= 0 && `, p.${s.page_number}`}
                  </span>
                ))}
              </div>
            )}

            <div className="flex items-center gap-2 mt-1">
              <span className="text-[10px] opacity-50">
                {new Date(msg.timestamp).toLocaleTimeString([], {
                  hour: "2-digit",
                  minute: "2-digit",
                })}
              </span>
              {msg.timings && (
                <span className="text-[10px] opacity-40">
                  {Object.entries(msg.timings)
                    .map(([k, v]) => `${k.replace("_ms", "")}: ${v}ms`)
                    .join(" | ")}
                </span>
              )}
              {msg.audioUrl && (
                <button
                  onClick={() => {
                    const audio = new Audio(msg.audioUrl);
                    audio.play();
                  }}
                  className="text-[10px] opacity-50 hover:opacity-100 underline"
                >
                  replay
                </button>
              )}
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}
