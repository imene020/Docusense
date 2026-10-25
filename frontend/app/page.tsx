"use client";
import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api, getToken } from "@/lib/api";

type Source = { document_id: number; filename: string; page: number | null; snippet: string; score: number };
type Msg = {
  role: "user" | "assistant";
  content: string;
  sources?: Source[];
  metrics?: any;
};

export default function ChatPage() {
  const router = useRouter();
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [sessionId, setSessionId] = useState<number | undefined>();
  const [busy, setBusy] = useState(false);
  const bottomRef = useRef<HTMLDivElement>(null);

  useEffect(() => { if (!getToken()) router.push("/login"); }, [router]);
  useEffect(() => bottomRef.current?.scrollIntoView({ behavior: "smooth" }), [messages]);

  const send = async () => {
    const query = input.trim();
    if (!query || busy) return;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: query }]);
    setBusy(true);
    try {
      const res = await api.chat(query, sessionId);
      setSessionId(res.session_id);
      setMessages((m) => [...m, {
        role: "assistant", content: res.answer,
        sources: res.sources, metrics: res.metrics,
      }]);
    } catch (e: any) {
      setMessages((m) => [...m, { role: "assistant", content: `Error: ${e.message}` }]);
    } finally {
      setBusy(false);
    }
  };

  const vote = async (metricId: number, value: number) => {
    try { await api.feedback(metricId, value); } catch {}
  };

  return (
    <div className="flex h-[calc(100vh-8rem)] flex-col">
      <div className="flex-1 space-y-4 overflow-y-auto pb-4">
        {messages.length === 0 && (
          <div className="mt-24 text-center text-slate-500">
            <p className="text-lg">Ask anything about your documents.</p>
            <p className="mt-1 text-sm">Upload files on the Documents page, then start a conversation.</p>
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} className={`max-w-2xl rounded-xl px-4 py-3 text-sm ${
            m.role === "user" ? "ml-auto bg-emerald-600/20 border border-emerald-700/40"
                              : "bg-slate-900 border border-slate-800"}`}>
            <p className="whitespace-pre-wrap">{m.content}</p>
            {m.sources && m.sources.length > 0 && (
              <div className="mt-3 border-t border-slate-800 pt-2">
                <p className="mb-1 text-xs font-medium text-slate-400">Sources</p>
                {m.sources.map((s, j) => (
                  <p key={j} className="truncate text-xs text-slate-500">
                    [{j + 1}] {s.filename}{s.page ? ` · p.${s.page}` : ""}
                  </p>
                ))}
              </div>
            )}
            {m.metrics?.sub_questions?.length > 0 && (
              <div className="mt-3 border-t border-slate-800 pt-2">
                <p className="mb-1 text-xs font-medium text-slate-400">Broken into sub-questions</p>
                {m.metrics.sub_questions.map((q: string, j: number) => (
                  <p key={j} className="text-xs text-slate-500">{j + 1}. {q}</p>
                ))}
              </div>
            )}
            {m.metrics?.trace && (
              <details className="mt-2 text-xs text-slate-500">
                <summary className="cursor-pointer hover:text-slate-300">Agent steps</summary>
                <p className="mt-1 font-mono">{m.metrics.trace.join(" → ")}</p>
              </details>
            )}
            {m.metrics && (
              <div className="mt-2 flex items-center gap-3 text-xs text-slate-500">
                {m.metrics.route && (
                  <span className="rounded bg-slate-800 px-1.5 py-0.5 text-slate-300">{m.metrics.route.replace("_", "-")}</span>
                )}
                <span>{(m.metrics.latency_ms / 1000).toFixed(1)}s</span>
                {m.metrics.tokens_per_second > 0 && <span>{m.metrics.tokens_per_second} tok/s</span>}
                {m.metrics.cache_hit && <span className="text-emerald-400">cached</span>}
                {m.metrics.rewrites > 0 && <span>{m.metrics.rewrites} self-corrected</span>}
                {m.metrics.metric_id && (
                  <span className="ml-auto flex gap-2">
                    <button onClick={() => vote(m.metrics.metric_id, 1)} className="hover:text-emerald-400">👍</button>
                    <button onClick={() => vote(m.metrics.metric_id, -1)} className="hover:text-red-400">👎</button>
                  </span>
                )}
              </div>
            )}
          </div>
        ))}
        {busy && <div className="max-w-2xl rounded-xl border border-slate-800 bg-slate-900 px-4 py-3 text-sm text-slate-400">Routing, retrieving and grading context…</div>}
        <div ref={bottomRef} />
      </div>
      <div className="flex gap-2 border-t border-slate-800 pt-4">
        <input value={input} onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
          placeholder="Ask about your documents…"
          className="flex-1 rounded-lg border border-slate-700 bg-slate-900 px-4 py-2 text-sm focus:border-emerald-500 focus:outline-none" />
        <button onClick={send} disabled={busy}
          className="rounded-lg bg-emerald-500 px-5 py-2 text-sm font-medium text-slate-950 hover:bg-emerald-400 disabled:opacity-50">
          Send
        </button>
      </div>
    </div>
  );
}
