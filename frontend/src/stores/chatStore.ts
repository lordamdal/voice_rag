import { create } from "zustand";

export type PipelineStage =
  | "idle"
  | "listening"
  | "transcribing"
  | "retrieving"
  | "thinking"
  | "speaking";

export interface SourceCitation {
  filename: string;
  page_number?: number | null;
  doc_id: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant";
  content: string;
  timestamp: number;
  timings?: Record<string, number>;
  audioUrl?: string;
  sources?: SourceCitation[];
}

export interface Session {
  id: string;
  title: string;
  createdAt: number;
  updatedAt: number;
  messageCount: number;
}

export interface Document {
  doc_id: string;
  filename: string;
  chunks: number;
  page_count?: number;
  source_type?: string;
}

interface ChatState {
  messages: Message[];
  documents: Document[];
  sessions: Session[];
  activeSessionId: string | null;
  stage: PipelineStage;
  isRecording: boolean;
  conversationMode: boolean;
  isPlayingResponse: boolean;
  ragEnabled: boolean;
  currentModel: string;
  availableModels: string[];
  temperature: number;
  maxTokens: number;

  addMessage: (msg: Omit<Message, "id" | "timestamp">) => void;
  setStage: (stage: PipelineStage) => void;
  setRecording: (recording: boolean) => void;
  setConversationMode: (mode: boolean) => void;
  setPlayingResponse: (playing: boolean) => void;
  setRagEnabled: (enabled: boolean) => void;
  setCurrentModel: (model: string) => void;
  setAvailableModels: (models: string[]) => void;
  setTemperature: (temp: number) => void;
  setMaxTokens: (tokens: number) => void;
  setDocuments: (docs: Document[]) => void;
  addDocument: (doc: Document) => void;
  removeDocument: (docId: string) => void;
  clearMessages: () => void;

  // Session management
  loadSessions: () => Promise<void>;
  createSession: () => Promise<string>;
  switchSession: (id: string) => Promise<void>;
  deleteSession: (id: string) => Promise<void>;
  renameSession: (id: string, title: string) => Promise<void>;
  setActiveSessionId: (id: string | null) => void;
  updateSessionInList: (id: string, updates: Partial<Session>) => void;
  loadDocuments: (sessionId: string | null) => Promise<void>;
}

export const useChatStore = create<ChatState>((set, get) => ({
  messages: [],
  documents: [],
  sessions: [],
  activeSessionId: null,
  stage: "idle",
  isRecording: false,
  conversationMode: false,
  isPlayingResponse: false,
  ragEnabled: true,
  currentModel: "qwen3:1.7b",
  availableModels: [],
  temperature: 0.7,
  maxTokens: 512,

  addMessage: (msg) =>
    set((state) => ({
      messages: [
        ...state.messages,
        { ...msg, id: crypto.randomUUID(), timestamp: Date.now() },
      ],
    })),

  setStage: (stage) => set({ stage }),
  setRecording: (isRecording) => set({ isRecording }),
  setConversationMode: (conversationMode) => set({ conversationMode }),
  setPlayingResponse: (isPlayingResponse) => set({ isPlayingResponse }),
  setRagEnabled: (ragEnabled) => set({ ragEnabled }),
  setCurrentModel: (currentModel) => set({ currentModel }),
  setAvailableModels: (availableModels) => set({ availableModels }),
  setTemperature: (temperature) => set({ temperature }),
  setMaxTokens: (maxTokens) => set({ maxTokens }),
  setDocuments: (documents) => set({ documents }),
  addDocument: (doc) =>
    set((state) => ({ documents: [...state.documents, doc] })),
  removeDocument: (docId) =>
    set((state) => ({
      documents: state.documents.filter((d) => d.doc_id !== docId),
    })),
  clearMessages: () => set({ messages: [] }),

  setActiveSessionId: (id) => set({ activeSessionId: id }),

  updateSessionInList: (id, updates) =>
    set((state) => ({
      sessions: state.sessions.map((s) =>
        s.id === id ? { ...s, ...updates } : s
      ),
    })),

  loadSessions: async () => {
    try {
      const res = await fetch("/api/sessions");
      const data = await res.json();
      const sessions: Session[] = data.map(
        (s: { session_id: string; title: string; created_at: number; updated_at: number; message_count: number }) => ({
          id: s.session_id,
          title: s.title,
          createdAt: s.created_at,
          updatedAt: s.updated_at,
          messageCount: s.message_count,
        })
      );
      set({ sessions });

      // Auto-switch to most recent session if none active
      const { activeSessionId } = get();
      if (!activeSessionId && sessions.length > 0) {
        await get().switchSession(sessions[0].id);
      }
    } catch (err) {
      console.error("Failed to load sessions:", err);
    }
  },

  createSession: async () => {
    try {
      const res = await fetch("/api/sessions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title: "New chat" }),
      });
      const data = await res.json();
      const session: Session = {
        id: data.session_id,
        title: data.title,
        createdAt: data.created_at,
        updatedAt: data.created_at,
        messageCount: 0,
      };
      set((state) => ({
        sessions: [session, ...state.sessions],
        activeSessionId: session.id,
        messages: [],
      }));
      return session.id;
    } catch (err) {
      console.error("Failed to create session:", err);
      return "";
    }
  },

  switchSession: async (id) => {
    try {
      const res = await fetch(`/api/sessions/${id}`);
      if (!res.ok) return;
      const data = await res.json();

      // Reconstruct messages from conversation history
      const messages: Message[] = data.conversation_history.map(
        (entry: { role: string; content: string }, i: number) => ({
          id: crypto.randomUUID(),
          role: entry.role as "user" | "assistant",
          content: entry.content,
          timestamp: data.created_at * 1000 + i * 1000,
        })
      );

      set({
        activeSessionId: id,
        messages,
        ragEnabled: data.rag_enabled,
      });

      // Reload documents for this session
      await get().loadDocuments(id);
    } catch (err) {
      console.error("Failed to switch session:", err);
    }
  },

  deleteSession: async (id) => {
    try {
      await fetch(`/api/sessions/${id}`, { method: "DELETE" });
      const { sessions, activeSessionId } = get();
      const remaining = sessions.filter((s) => s.id !== id);
      set({ sessions: remaining });

      if (activeSessionId === id) {
        if (remaining.length > 0) {
          await get().switchSession(remaining[0].id);
        } else {
          set({ activeSessionId: null, messages: [] });
        }
      }
    } catch (err) {
      console.error("Failed to delete session:", err);
    }
  },

  renameSession: async (id, title) => {
    try {
      await fetch(`/api/sessions/${id}`, {
        method: "PATCH",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ title }),
      });
      set((state) => ({
        sessions: state.sessions.map((s) =>
          s.id === id ? { ...s, title } : s
        ),
      }));
    } catch (err) {
      console.error("Failed to rename session:", err);
    }
  },

  loadDocuments: async (sessionId) => {
    try {
      const url = sessionId
        ? `/api/documents?session_id=${sessionId}`
        : "/api/documents";
      const res = await fetch(url);
      const docs = await res.json();
      set({ documents: docs });
    } catch (err) {
      console.error("Failed to load documents:", err);
      set({ documents: [] });
    }
  },
}));
