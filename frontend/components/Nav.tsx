"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { clearToken, getToken } from "@/lib/api";
import { useEffect, useState } from "react";

const links = [
  { href: "/", label: "Chat" },
  { href: "/documents", label: "Documents" },
  { href: "/dashboard", label: "Dashboard" },
];

export default function Nav() {
  const pathname = usePathname();
  const router = useRouter();
  const [authed, setAuthed] = useState(false);
  useEffect(() => setAuthed(!!getToken()), [pathname]);

  return (
    <nav className="border-b border-slate-800 bg-slate-900/60 backdrop-blur">
      <div className="mx-auto flex max-w-6xl items-center gap-6 px-4 py-3">
        <span className="font-semibold tracking-tight text-emerald-400">DocuSense</span>
        {links.map((l) => (
          <Link
            key={l.href}
            href={l.href}
            className={`text-sm ${pathname === l.href ? "text-white" : "text-slate-400 hover:text-slate-200"}`}
          >
            {l.label}
          </Link>
        ))}
        <div className="ml-auto">
          {authed ? (
            <button
              onClick={() => { clearToken(); router.push("/login"); }}
              className="text-sm text-slate-400 hover:text-slate-200"
            >
              Sign out
            </button>
          ) : (
            <Link href="/login" className="text-sm text-slate-400 hover:text-slate-200">Sign in</Link>
          )}
        </div>
      </div>
    </nav>
  );
}
