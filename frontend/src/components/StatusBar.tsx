import { useChatStore, type PipelineStage } from "../stores/chatStore";

const STAGES: { key: PipelineStage; label: string; icon: string }[] = [
  { key: "listening", label: "Listen", icon: "M" },
  { key: "transcribing", label: "STT", icon: "T" },
  { key: "retrieving", label: "RAG", icon: "R" },
  { key: "thinking", label: "LLM", icon: "L" },
  { key: "speaking", label: "TTS", icon: "S" },
];

const STAGE_ORDER: Record<string, number> = {
  idle: -1,
  listening: 0,
  transcribing: 1,
  retrieving: 2,
  thinking: 3,
  speaking: 4,
};

export function StatusBar() {
  const stage = useChatStore((s) => s.stage);
  const conversationMode = useChatStore((s) => s.conversationMode);
  const currentIdx = STAGE_ORDER[stage] ?? -1;

  return (
    <div className="flex items-center justify-center gap-1 py-2">
      {conversationMode && (
        <div className="flex items-center gap-1 px-2 py-1 rounded text-xs font-medium bg-green-500/20 text-green-400 ring-1 ring-green-500/50 mr-2">
          <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
          Live
        </div>
      )}
      {STAGES.map((s, idx) => {
        const isActive = idx === currentIdx;
        const isPast = idx < currentIdx;
        return (
          <div key={s.key} className="flex items-center">
            <div
              className={`flex items-center gap-1 px-2 py-1 rounded text-xs font-medium transition-all duration-300 ${
                isActive
                  ? "bg-blue-500/20 text-blue-400 ring-1 ring-blue-500/50"
                  : isPast
                  ? "bg-green-500/10 text-green-500/60"
                  : "text-slate-600"
              }`}
            >
              <span>{s.label}</span>
            </div>
            {idx < STAGES.length - 1 && (
              <div
                className={`w-4 h-px mx-0.5 ${
                  isPast ? "bg-green-500/40" : "bg-slate-700"
                }`}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}
