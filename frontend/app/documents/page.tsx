"use client";
import { useCallback, useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { api, getToken } from "@/lib/api";

type Doc = { id: number; filename: string; status: string; num_chunks: number; created_at: string; error?: string };

const statusColor: Record<string, string> = {
  indexed: "text-emerald-400", processing: "text-amber-400",
  queued: "text-slate-400", failed: "text-red-400",
};

export default function DocumentsPage() {
  const router = useRouter();
  const [docs, setDocs] = useState<Doc[]>([]);
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState("");

  const refresh = useCallback(async () => {
    try { setDocs(await api.documents()); } catch {}
  }, []);

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    refresh();
    const id = setInterval(refresh, 4000); // poll while ingestion runs
    return () => clearInterval(id);
  }, [refresh, router]);

  const onUpload = async (files: FileList | null) => {
    if (!files?.length) return;
    setUploading(true); setError("");
    try {
      for (const file of Array.from(files)) await api.upload(file);
      await refresh();
    } catch (e: any) {
      setError(e.message);
    } finally {
      setUploading(false);
    }
  };

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">Documents</h1>
      <label className="block cursor-pointer rounded-xl border-2 border-dashed border-slate-700 p-8 text-center text-sm text-slate-400 hover:border-emerald-600 hover:text-slate-200">
        {uploading ? "Uploading…" : "Click to upload PDF, CSV, XLSX, or text files (max 100 MB)"}
        <input type="file" multiple hidden accept=".pdf,.csv,.xlsx,.txt,.md,.log"
          onChange={(e) => onUpload(e.target.files)} />
      </label>
      {error && <p className="mt-2 text-sm text-red-400">{error}</p>}
      <div className="mt-6 divide-y divide-slate-800 rounded-xl border border-slate-800">
        {docs.length === 0 && <p className="p-4 text-sm text-slate-500">No documents yet.</p>}
        {docs.map((d) => (
          <div key={d.id} className="flex items-center gap-4 p-4 text-sm">
            <span className="flex-1 truncate">{d.filename}</span>
            <span className={statusColor[d.status] || "text-slate-400"}>
              {d.status}{d.status === "indexed" && ` · ${d.num_chunks} chunks`}
            </span>
            {d.error && <span className="max-w-xs truncate text-xs text-red-400">{d.error}</span>}
            <button onClick={async () => { await api.deleteDocument(d.id); refresh(); }}
              className="text-slate-500 hover:text-red-400">Delete</button>
          </div>
        ))}
      </div>
    </div>
  );
}
