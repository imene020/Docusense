"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";
import { api, setToken } from "@/lib/api";

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [org, setOrg] = useState("");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    setBusy(true); setError("");
    try {
      const res = mode === "login"
        ? await api.login(email, password)
        : await api.register(org, email, password);
      setToken(res.access_token);
      router.push("/");
    } catch (e: any) {
      setError(e.message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="mx-auto mt-16 max-w-sm rounded-xl border border-slate-800 bg-slate-900 p-6">
      <h1 className="mb-4 text-lg font-semibold">
        {mode === "login" ? "Sign in" : "Create your workspace"}
      </h1>
      {mode === "register" && (
        <input value={org} onChange={(e) => setOrg(e.target.value)} placeholder="Organization name"
          className="mb-3 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
      )}
      <input value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email" type="email"
        className="mb-3 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
      <input value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Password" type="password"
        onKeyDown={(e) => e.key === "Enter" && submit()}
        className="mb-4 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm" />
      {error && <p className="mb-3 text-sm text-red-400">{error}</p>}
      <button onClick={submit} disabled={busy}
        className="w-full rounded-lg bg-emerald-500 py-2 text-sm font-medium text-slate-950 hover:bg-emerald-400 disabled:opacity-50">
        {busy ? "Working..." : mode === "login" ? "Sign in" : "Create workspace"}
      </button>
      <button onClick={() => setMode(mode === "login" ? "register" : "login")}
        className="mt-3 w-full text-center text-xs text-slate-400 hover:text-slate-200">
        {mode === "login" ? "New here? Create a workspace" : "Already have an account? Sign in"}
      </button>
    </div>
  );
}
