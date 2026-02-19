import { useCallback, useEffect, useRef, useState } from "react";
import { useChatStore } from "../stores/chatStore";

export function DocumentUpload() {
  const { documents, addDocument, removeDocument, activeSessionId, loadDocuments } = useChatStore();
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);

  // Reload documents when session changes
  useEffect(() => {
    loadDocuments(activeSessionId);
  }, [activeSessionId, loadDocuments]);

  const uploadFile = useCallback(async (file: File) => {
    setUploading(true);
    try {
      const formData = new FormData();
      formData.append("file", file);
      const sessionId = useChatStore.getState().activeSessionId;
      if (sessionId) {
        formData.append("session_id", sessionId);
      }
      const res = await fetch("/api/documents", {
        method: "POST",
        body: formData,
      });
      if (!res.ok) {
        const err = await res.json();
        alert(`Upload failed: ${err.detail}`);
        return;
      }
      const data = await res.json();
      addDocument({
        doc_id: data.doc_id,
        filename: data.filename,
        chunks: data.chunks,
      });
    } catch (err) {
      console.error("Upload error:", err);
      alert("Upload failed");
    } finally {
      setUploading(false);
    }
  }, [addDocument]);

  const handleDelete = useCallback(async (docId: string) => {
    try {
      await fetch(`/api/documents/${docId}`, { method: "DELETE" });
      removeDocument(docId);
    } catch (err) {
      console.error("Delete error:", err);
    }
  }, [removeDocument]);

  const handleDrop = useCallback(
    (e: React.DragEvent) => {
      e.preventDefault();
      setDragOver(false);
      const files = Array.from(e.dataTransfer.files);
      files.forEach(uploadFile);
    },
    [uploadFile]
  );

  return (
    <div className="space-y-3">
      <h3 className="text-sm font-semibold text-slate-300">Documents</h3>

      {/* Drop zone */}
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setDragOver(true);
        }}
        onDragLeave={() => setDragOver(false)}
        onDrop={handleDrop}
        onClick={() => fileInputRef.current?.click()}
        className={`border-2 border-dashed rounded-lg p-4 text-center cursor-pointer transition-colors ${
          dragOver
            ? "border-blue-400 bg-blue-500/10"
            : "border-slate-600 hover:border-slate-500"
        }`}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".pdf,.txt,.md"
          className="hidden"
          onChange={(e) => {
            const file = e.target.files?.[0];
            if (file) uploadFile(file);
            e.target.value = "";
          }}
        />
        {uploading ? (
          <p className="text-xs text-blue-400">Uploading...</p>
        ) : (
          <p className="text-xs text-slate-400">
            Drop PDF, TXT, or MD files here
          </p>
        )}
      </div>

      {/* Document list */}
      {documents.length > 0 && (
        <ul className="space-y-1">
          {documents.map((doc) => (
            <li
              key={doc.doc_id}
              className="flex items-center justify-between bg-slate-800 rounded px-3 py-1.5 text-xs"
            >
              <div className="truncate flex-1 mr-2">
                <span className="text-slate-200">{doc.filename}</span>
                <span className="text-slate-500 ml-2">{doc.chunks} chunks</span>
              </div>
              <button
                onClick={() => handleDelete(doc.doc_id)}
                className="text-slate-500 hover:text-red-400 flex-shrink-0"
              >
                <svg className="w-3.5 h-3.5" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={2}>
                  <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                </svg>
              </button>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
