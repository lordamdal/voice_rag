import { useState, useEffect } from "react";
import { ChatPanel } from "./components/ChatPanel";
import { VoiceButton } from "./components/VoiceButton";
import { WaveformViz } from "./components/WaveformViz";
import { StatusBar } from "./components/StatusBar";
import { DocumentUpload } from "./components/DocumentUpload";
import { SettingsPanel } from "./components/SettingsPanel";
import { TextInput } from "./components/TextInput";
import { useChatStore } from "./stores/chatStore";

function App() {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const sessions = useChatStore((s) => s.sessions);
  const activeSessionId = useChatStore((s) => s.activeSessionId);
  const loadSessions = useChatStore((s) => s.loadSessions);
  const createSession = useChatStore((s) => s.createSession);
  const switchSession = useChatStore((s) => s.switchSession);
  const deleteSession = useChatStore((s) => s.deleteSession);

  useEffect(() => {
    loadSessions();
  }, [loadSessions]);

  return (
    <div className="flex h-screen bg-slate-900">
      {/* Sidebar */}
      {sidebarOpen && (
        <aside className="w-64 bg-slate-800/50 border-r border-slate-700 flex flex-col">
          {/* Header + New Chat */}
          <div className="flex items-center justify-between p-4 pb-2">
            <h1 className="text-sm font-bold text-slate-200">Voice RAG</h1>
            <div className="flex items-center gap-1">
              <button
                onClick={createSession}
                className="text-slate-400 hover:text-white p-1"
                title="New chat"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M12 4v16m8-8H4" />
                </svg>
              </button>
              <button
                onClick={() => setSidebarOpen(false)}
                className="text-slate-400 hover:text-white p-1"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M11 19l-7-7 7-7m8 14l-7-7 7-7" />
                </svg>
              </button>
            </div>
          </div>

          {/* Session list */}
          <div className="flex-1 overflow-y-auto px-2 py-1 space-y-0.5">
            {sessions.map((session) => (
              <div
                key={session.id}
                className={`group flex items-center rounded-lg px-3 py-2 cursor-pointer transition-colors ${
                  session.id === activeSessionId
                    ? "bg-slate-700 text-slate-100"
                    : "text-slate-400 hover:bg-slate-700/50 hover:text-slate-200"
                }`}
                onClick={() => switchSession(session.id)}
              >
                <svg className="w-3.5 h-3.5 mr-2 flex-shrink-0 opacity-50" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
                <span className="text-xs truncate flex-1">{session.title}</span>
                <button
                  onClick={(e) => {
                    e.stopPropagation();
                    deleteSession(session.id);
                  }}
                  className="text-slate-500 hover:text-red-400 opacity-0 group-hover:opacity-100 flex-shrink-0 ml-1"
                >
                  <svg className="w-3 h-3" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                    <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                  </svg>
                </button>
              </div>
            ))}
            {sessions.length === 0 && (
              <p className="text-xs text-slate-500 text-center py-4">
                No chats yet
              </p>
            )}
          </div>

          {/* Documents section */}
          <div className="border-t border-slate-700 p-4">
            <DocumentUpload />
          </div>
        </aside>
      )}

      {/* Main area */}
      <main className="flex-1 flex flex-col relative overflow-hidden">
        {/* Top bar */}
        <header className="flex items-center justify-between px-4 py-2 border-b border-slate-700 bg-slate-800/30">
          <div className="flex items-center gap-2">
            {!sidebarOpen && (
              <button
                onClick={() => setSidebarOpen(true)}
                className="text-slate-400 hover:text-white mr-2"
              >
                <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M4 6h16M4 12h16M4 18h16" />
                </svg>
              </button>
            )}
            <StatusBar />
          </div>
          <button
            onClick={() => setSettingsOpen(!settingsOpen)}
            className="text-slate-400 hover:text-white"
          >
            <svg className="w-5 h-5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1.5}>
              <path strokeLinecap="round" strokeLinejoin="round" d="M9.594 3.94c.09-.542.56-.94 1.11-.94h2.593c.55 0 1.02.398 1.11.94l.213 1.281c.063.374.313.686.645.87.074.04.147.083.22.127.324.196.72.257 1.075.124l1.217-.456a1.125 1.125 0 011.37.49l1.296 2.247a1.125 1.125 0 01-.26 1.431l-1.003.827c-.293.24-.438.613-.431.992a6.759 6.759 0 010 .255c-.007.378.138.75.43.99l1.005.828c.424.35.534.954.26 1.43l-1.298 2.247a1.125 1.125 0 01-1.369.491l-1.217-.456c-.355-.133-.75-.072-1.076.124a6.57 6.57 0 01-.22.128c-.331.183-.581.495-.644.869l-.213 1.28c-.09.543-.56.941-1.11.941h-2.594c-.55 0-1.02-.398-1.11-.94l-.213-1.281c-.062-.374-.312-.686-.644-.87a6.52 6.52 0 01-.22-.127c-.325-.196-.72-.257-1.076-.124l-1.217.456a1.125 1.125 0 01-1.369-.49l-1.297-2.247a1.125 1.125 0 01.26-1.431l1.004-.827c.292-.24.437-.613.43-.992a6.932 6.932 0 010-.255c.007-.378-.138-.75-.43-.99l-1.004-.828a1.125 1.125 0 01-.26-1.43l1.297-2.247a1.125 1.125 0 011.37-.491l1.216.456c.356.133.751.072 1.076-.124.072-.044.146-.087.22-.128.332-.183.582-.495.644-.869l.214-1.281z" />
              <path strokeLinecap="round" strokeLinejoin="round" d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
            </svg>
          </button>
        </header>

        {/* Chat messages */}
        <ChatPanel />

        {/* Waveform + Voice + Text input area */}
        <div className="border-t border-slate-700 bg-slate-800/30">
          <WaveformViz />
          <div className="flex items-center justify-center py-3">
            <VoiceButton />
          </div>
          <TextInput />
        </div>

        {/* Settings panel overlay */}
        <SettingsPanel open={settingsOpen} onClose={() => setSettingsOpen(false)} />
      </main>
    </div>
  );
}

export default App;
