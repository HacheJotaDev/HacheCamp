"use client";

import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import { BarChart3, Eye, MousePointerClick, Target, AlertTriangle, Info } from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, Campaign } from "@/lib/api";
import { numero } from "@/components/shared";

interface Metricas {
  total: number;
  aceptados_ses: number;
  entregados: number;
  pendientes: number;
  fallidos: number;
  rebotes: number;
  rebotes_temporales: number;
  quejas: number;
  rechazados?: number;
  retrasos?: number;
  incierto?: number;
  success_rate: number | null;
  bounce_rate: number | null;
  open_rate: number | null;
  click_rate: number | null;
  aperturas_unicas: number;
  clics_unicos: number;
  denominadores: Record<string, string>;
  notas?: Record<string, string>;
}

interface Punto {
  fecha: string;
  envios: number;
  delivery: number;
  bounce: number;
  complaint: number;
  open: number;
  click: number;
}

export default function EstadisticasPage() {
  const [dias, setDias] = useState("30");
  const [campana, setCampana] = useState("todas");

  const { data: campanas } = useQuery({
    queryKey: ["campanas-todas"],
    queryFn: () => api.get<{ items: Campaign[] }>("/api/v1/campaigns?per_page=100"),
  });

  const qs = `dias=${dias}${campana !== "todas" ? `&campana=${campana}` : ""}`;
  const { data: metricas, isLoading: cargandoMetricas } = useQuery({
    queryKey: ["metricas", campana],
    queryFn: () => api.get<Metricas>(`/api/v1/stats/metrics${campana !== "todas" ? `?campana=${campana}` : ""}`),
    refetchInterval: 60_000,
  });
  const { data: serie } = useQuery({
    queryKey: ["serie-estadisticas", dias, campana],
    queryFn: () => api.get<{ puntos: Punto[] }>(`/api/v1/stats/series?${qs}`),
    refetchInterval: 60_000,
  });

  const nombreCampana = campanas?.items.find((c) => c.id === campana)?.name;

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Estadísticas</h1>
          <p className="text-sm text-muted-foreground">
            {nombreCampana ? `Campaña: ${nombreCampana}` : "Todas las campañas"} · datos reales de SES
          </p>
        </div>
        <div className="flex gap-2">
          <Select value={dias} onValueChange={setDias}>
            <SelectTrigger className="w-36"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="7">Últimos 7 días</SelectItem>
              <SelectItem value="14">Últimos 14 días</SelectItem>
              <SelectItem value="30">Últimos 30 días</SelectItem>
              <SelectItem value="90">Últimos 90 días</SelectItem>
            </SelectContent>
          </Select>
          <Select value={campana} onValueChange={setCampana}>
            <SelectTrigger className="w-full sm:w-56"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem value="todas">Todas las campañas</SelectItem>
              {campanas?.items.map((c) => (
                <SelectItem key={c.id} value={c.id}>{c.name}</SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>
      </div>

      {cargandoMetricas || !metricas ? (
        <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-24" />)}
        </div>
      ) : (
        <>
          <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
            <Tarjeta titulo="Success Rate" valor={pct(metricas.success_rate)}
              detalle={`${numero(metricas.entregados)} entregadas`} icon={Target} tono="text-emerald-600" />
            <Tarjeta titulo="Bounce Rate" valor={pct(metricas.bounce_rate)}
              detalle={`${numero(metricas.rebotes + metricas.rebotes_temporales)} rebotes / ${numero(metricas.aceptados_ses)} aceptados`} icon={AlertTriangle} tono="text-amber-600" />
            <Tarjeta titulo="Open Rate" valor={pct(metricas.open_rate)}
              detalle={`${numero(metricas.aperturas_unicas)} destinatarios únicos`} icon={Eye} tono="text-violet-600" />
            <Tarjeta titulo="Click Rate" valor={pct(metricas.click_rate)}
              detalle={`${numero(metricas.clics_unicos)} destinatarios únicos`} icon={MousePointerClick} tono="text-teal-600" />
          </div>

          <div className="grid gap-3 grid-cols-2 lg:grid-cols-4">
            <TarjetaMini titulo="Aceptados por SES" valor={metricas.aceptados_ses}
              detalle="La API aceptó los mensajes" />
            <TarjetaMini titulo="Entregados" valor={metricas.entregados}
              detalle="Confirmados por el servidor receptor" />
            <TarjetaMini titulo="Pendientes" valor={metricas.pendientes}
              detalle="En cola o en proceso" />
            <TarjetaMini titulo="Fallidos" valor={metricas.fallidos}
              detalle="Incluye rechazos por SES" />
            <TarjetaMini titulo="Rechazados" valor={metricas.rechazados ?? 0}
              detalle="SES los rechazó antes de aceptarlos" />
            <TarjetaMini titulo="Retrasos" valor={metricas.retrasos ?? 0}
              detalle="En tránsito: SES avisó de demora" />
            <TarjetaMini titulo="Inciertos" valor={metricas.incierto ?? 0}
              detalle="Sin confirmación; requieren reconciliación" />
            <TarjetaMini titulo="Quejas" valor={metricas.quejas}
              detalle="Marcados como spam por el destinatario" />
          </div>
        </>
      )}

      <Card>
        <CardHeader>
          <CardTitle>Evolución diaria</CardTitle>
          <CardDescription>Envíos aceptados, entregas, rebotes, quejas, aperturas y clics</CardDescription>
        </CardHeader>
        <CardContent className="h-80">
          {serie?.puntos && (
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={serie.puntos} margin={{ top: 5, right: 5, left: -15, bottom: 0 }}>
                <defs>
                  <linearGradient id="egEnv" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#10b981" stopOpacity={0.35} />
                    <stop offset="100%" stopColor="#10b981" stopOpacity={0} />
                  </linearGradient>
                  <linearGradient id="egEnt" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#38bdf8" stopOpacity={0.3} />
                    <stop offset="100%" stopColor="#38bdf8" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                <XAxis dataKey="fecha" tickFormatter={(v: string) => v.slice(5)} fontSize={11} />
                <YAxis fontSize={11} allowDecimals={false} />
                <Tooltip labelFormatter={(l) => `Fecha: ${l}`} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Area type="monotone" dataKey="envios" name="Envíos" stroke="#10b981" fill="url(#egEnv)" strokeWidth={2} />
                <Area type="monotone" dataKey="delivery" name="Entregas" stroke="#38bdf8" fill="url(#egEnt)" strokeWidth={2} />
                <Area type="monotone" dataKey="open" name="Aperturas" stroke="#8b5cf6" fill="transparent" strokeWidth={1.5} />
                <Area type="monotone" dataKey="click" name="Clics" stroke="#14b8a6" fill="transparent" strokeWidth={1.5} />
                <Area type="monotone" dataKey="bounce" name="Rebotes" stroke="#f59e0b" fill="transparent" strokeWidth={1.5} />
                <Area type="monotone" dataKey="complaint" name="Quejas" stroke="#ef4444" fill="transparent" strokeWidth={1.5} />
              </AreaChart>
            </ResponsiveContainer>
          )}
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2 text-base">
            <Info className="h-4 w-4 text-muted-foreground" /> Cómo se calculan las tasas
          </CardTitle>
        </CardHeader>
        <CardContent className="text-sm space-y-2 text-muted-foreground">
          {metricas?.denominadores && Object.entries(metricas.denominadores).map(([k, v]) => (
            <p key={k}><b className="text-foreground">{etiquetaTasa(k)}:</b> {v}.</p>
          ))}
          {metricas?.notas && Object.entries(metricas.notas).map(([k, v]) => (
            <p key={k}><b className="text-foreground">{etiquetaContador(k)}:</b> {v}</p>
          ))}
          <p className="text-xs pt-1 border-t">
            Las aperturas son aproximadas (bloqueadores de imágenes, proxies de privacidad). Un mismo
            destinatario con varias aperturas cuenta como una sola persona. Distinguir «aceptado por SES»
            (la API aceptó el mensaje) de «entregado» (el servidor receptor lo confirmó).
          </p>
        </CardContent>
      </Card>
    </div>
  );
}

function pct(v: number | null | undefined): string {
  if (v === null || v === undefined) return "—";
  return `${v.toFixed(1)}%`;
}

function etiquetaTasa(k: string): string {
  const mapa: Record<string, string> = {
    success_rate: "Success Rate",
    bounce_rate: "Bounce Rate",
    open_rate: "Open Rate",
    click_rate: "Click Rate",
  };
  return mapa[k] ?? k;
}

function etiquetaContador(k: string): string {
  const mapa: Record<string, string> = {
    retrasos: "Retrasos",
    rechazados: "Rechazados",
    incierto: "Inciertos",
  };
  return mapa[k] ?? k;
}

function Tarjeta({ titulo, valor, detalle, icon: Icon, tono }: {
  titulo: string; valor: string; detalle: string;
  icon: React.ComponentType<{ className?: string }>; tono: string;
}) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-xs font-medium text-muted-foreground">{titulo}</p>
            <p className="text-2xl font-bold mt-1 tabular-nums">{valor}</p>
            <p className="text-[11px] text-muted-foreground mt-0.5 truncate">{detalle}</p>
          </div>
          <div className="h-9 w-9 rounded-lg bg-muted flex items-center justify-center shrink-0">
            <Icon className={`h-4.5 w-4.5 ${tono}`} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function TarjetaMini({ titulo, valor, detalle }: { titulo: string; valor: number; detalle?: string }) {
  return (
    <Card>
      <CardContent className="p-3 text-center">
        <p className="text-lg font-bold tabular-nums">{numero(valor)}</p>
        <p className="text-[11px] text-muted-foreground">{titulo}</p>
        {detalle && <p className="text-[10px] text-muted-foreground/70 mt-0.5 line-clamp-2">{detalle}</p>}
      </CardContent>
    </Card>
  );
}
