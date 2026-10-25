"use client";
import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { AreaChart, Card, Metric, Text } from "@tremor/react";
import { api, getToken } from "@/lib/api";

export default function DashboardPage() {
  const router = useRouter();
  const [overview, setOverview] = useState<any>(null);
  const [series, setSeries] = useState<any[]>([]);
  const [recent, setRecent] = useState<any[]>([]);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getToken()) { router.push("/login"); return; }
    (async () => {
      try {
        const [o, t, r] = await Promise.all([
          api.metricsOverview(), api.metricsTimeseries(), api.metricsRecent(),
        ]);
        setOverview(o);
        setSeries(t.map((p: any) => ({ ...p, time: new Date(p.time).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit" }) })));
        setRecent(r);
      } catch (e: any) {
        setError(e.message);
      }
    })();
  }, [router]);

  if (error) return <p className="text-sm text-red-400">Dashboard requires an admin account. ({error})</p>;
  if (!overview) return <p className="text-sm text-slate-500">Loading metrics…</p>;

  const kpis = [
    { label: "Queries (7d)", value: overview.queries_7d },
    { label: "Avg latency", value: `${(overview.avg_latency_ms / 1000).toFixed(1)}s` },
    { label: "p95 latency", value: `${(overview.p95_latency_ms / 1000).toFixed(1)}s` },
    { label: "Tokens / sec", value: overview.avg_tokens_per_second },
    { label: "Cache hit rate", value: `${(overview.cache_hit_rate * 100).toFixed(0)}%` },
    {
      label: "Satisfaction",
      value: overview.satisfaction_rate === null ? "—" : `${(overview.satisfaction_rate * 100).toFixed(0)}%`,
    },
  ];

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">Operations dashboard</h1>
      <div className="grid grid-cols-2 gap-4 md:grid-cols-3 lg:grid-cols-6">
        {kpis.map((k) => (
          <Card key={k.label} className="bg-slate-900 ring-slate-800">
            <Text className="text-slate-400">{k.label}</Text>
            <Metric className="text-slate-100">{k.value}</Metric>
          </Card>
        ))}
      </div>

      <Card className="mt-6 bg-slate-900 ring-slate-800">
        <Text className="text-slate-400">Agent routing mix (7 days)</Text>
        <div className="mt-3 flex flex-wrap gap-6 text-sm">
          {Object.entries(overview.routes_7d || {}).map(([route, count]) => (
            <div key={route}>
              <span className="text-2xl font-semibold text-slate-100">{String(count)}</span>
              <span className="ml-2 text-slate-400">{route.replace("_", "-")}</span>
            </div>
          ))}
          {Object.keys(overview.routes_7d || {}).length === 0 && <span className="text-slate-500">No queries yet.</span>}
        </div>
      </Card>

      <Card className="mt-6 bg-slate-900 ring-slate-800">
        <Text className="text-slate-400">Query volume & latency (hourly, last 7 days)</Text>
        <AreaChart className="mt-4 h-64" data={series} index="time"
          categories={["queries", "avg_latency_ms"]} colors={["emerald", "amber"]}
          showLegend valueFormatter={(v: number) => `${v}`} />
      </Card>

      <Card className="mt-6 bg-slate-900 ring-slate-800">
        <Text className="mb-3 text-slate-400">Recent queries</Text>
        <div className="divide-y divide-slate-800 text-sm">
          {recent.map((q) => (
            <div key={q.id} className="flex items-center gap-4 py-2">
              <span className="flex-1 truncate text-slate-300">{q.query}</span>
              <span className="rounded bg-slate-800 px-1.5 py-0.5 text-xs text-slate-300">{q.route}</span>
              <span className="text-slate-500">{(q.latency_ms / 1000).toFixed(1)}s</span>
              <span className="text-slate-500">{q.iterations} pass{q.iterations > 1 ? "es" : ""}</span>
              {q.cache_hit && <span className="text-emerald-400">cached</span>}
              {q.feedback === 1 && <span>👍</span>}
              {q.feedback === -1 && <span>👎</span>}
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}
