"use client";

import { useQuery } from "@tanstack/react-query";
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend,
} from "recharts";
import {
  Mail, Send, CheckCircle2, AlertTriangle, UserX, Eye, MousePointerClick,
  Users, Activity, Gauge, Loader2,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import Link from "next/link";
import { api, Campaign, DashboardData } from "@/lib/api";
import { StatusBadge, EmptyState, fecha, numero } from "@/components/shared";
import { FileText } from "lucide-react";

function StatCard({
  titulo, valor, icon: Icon, tono = "text-emerald-600",
  detalle,
}: {
  titulo: string; valor: number | string; icon: React.ComponentType<{ className?: string }>;
  tono?: string; detalle?: string;
}) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-xs font-medium text-muted-foreground truncate">{titulo}</p>
            <p className="text-2xl font-bold mt-1 tabular-nums">{valor}</p>
            {detalle && <p className="text-[11px] text-muted-foreground mt-0.5 truncate">{detalle}</p>}
          </div>
          <div className="h-9 w-9 rounded-lg bg-muted flex items-center justify-center shrink-0">
            <Icon className={`h-4.5 w-4.5 ${tono}`} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

export default function PanelPage() {
  const { data, isLoading, isError } = useQuery({
    queryKey: ["dashboard"],
    queryFn: () => api.get<DashboardData>("/api/v1/stats/dashboard"),
    refetchInterval: 30_000,
  });

  const { data: series } = useQuery({
    queryKey: ["series"],
    queryFn: () => api.get<{ puntos: { fecha: string; envios: number; delivery: number; bounce: number; complaint: number; open: number; click: number }[] }>("/api/v1/stats/series?dias=14"),
    refetchInterval: 60_000,
  });

  const { data: campanas } = useQuery({
    queryKey: ["campanas-recientes"],
    queryFn: () => api.get<Paged2>("/api/v1/campaigns?per_page=5"),
  });

  const r = data?.resumen;
  const cuota = data?.cuota_ses;
  const cuotaPct = cuota?.max_24h ? Math.min(100, ((cuota.sent_last_24h || 0) / cuota.max_24h) * 100) : 0;
  const vacio = r && r.campanas_total === 0 && (r.contactos?.total ?? 0) === 0;

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Panel principal</h1>
        <p className="text-sm text-muted-foreground">Métricas reales de tus campañas y entregabilidad</p>
      </div>

      {isLoading ? (
        <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
          {Array.from({ length: 8 }).map((_, i) => <Skeleton key={i} className="h-24" />)}
        </div>
      ) : isError || !r ? (
        <Card><CardContent className="p-8 text-center text-muted-foreground">
          No se pudieron cargar las estadísticas. Comprueba que la API esté operativa.
        </CardContent></Card>
      ) : vacio ? (
        <Card>
          <CardContent className="pt-0">
            <EmptyState
              icon={Activity}
              titulo="Aún no hay datos"
              descripcion="Crea tus primeras plantillas e importa contactos con consentimiento para comenzar. Las métricas aparecerán aquí con datos reales, nunca ficticios."
              accion={
                <div className="flex gap-2 mt-2">
                  <Button asChild className="bg-emerald-600 hover:bg-emerald-700">
                    <Link href="/panel/contactos">Importar contactos</Link>
                  </Button>
                  <Button asChild variant="outline">
                    <Link href="/panel/plantillas">Crear plantilla</Link>
                  </Button>
                </div>
              }
            />
          </CardContent>
        </Card>
      ) : (
        <>
          <div className="grid gap-4 grid-cols-2 lg:grid-cols-4">
            <StatCard titulo="Campañas totales" valor={numero(r.campanas_total)} icon={Mail}
              detalle={`${r.campanas_activas} activas`} />
            <StatCard titulo="Aceptados por SES" valor={numero(r.aceptados_ses)} icon={Send}
              detalle="Solicitud recibida por SES" tono="text-sky-600" />
            <StatCard titulo="Entregas confirmadas" valor={numero(r.entregas)} icon={CheckCircle2}
              detalle="Notificadas por SES" />
            <StatCard titulo="Rebotes" valor={numero(r.rebotes + (r.rebotes_temporales || 0))} icon={AlertTriangle}
              detalle={`${r.rebotes} permanentes · ${r.rebotes_temporales || 0} temporales`} tono="text-amber-600" />
            <StatCard titulo="Quejas de spam" valor={numero(r.quejas)} icon={UserX} tono="text-red-600" />
            <StatCard titulo="Bajas" valor={numero(r.bajas)} icon={UserX} tono="text-orange-600" />
            <StatCard titulo="Aperturas*" valor={numero(r.aperturas)} icon={Eye} tono="text-violet-600" />
            <StatCard titulo="Clics" valor={numero(r.clics)} icon={MousePointerClick} tono="text-teal-600" />
          </div>

          <div className="grid gap-4 lg:grid-cols-3">
            <Card className="lg:col-span-2">
              <CardHeader>
                <CardTitle>Evolución diaria (14 días)</CardTitle>
                <CardDescription>Envíos, entregas, rebotes y quejas registradas</CardDescription>
              </CardHeader>
              <CardContent className="h-72">
                {series?.puntos && (
                  <ResponsiveContainer width="100%" height="100%">
                    <AreaChart data={series.puntos} margin={{ top: 5, right: 5, left: -15, bottom: 0 }}>
                      <defs>
                        <linearGradient id="gEnv" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#10b981" stopOpacity={0.35} />
                          <stop offset="100%" stopColor="#10b981" stopOpacity={0} />
                        </linearGradient>
                        <linearGradient id="gEnt" x1="0" y1="0" x2="0" y2="1">
                          <stop offset="0%" stopColor="#38bdf8" stopOpacity={0.3} />
                          <stop offset="100%" stopColor="#38bdf8" stopOpacity={0} />
                        </linearGradient>
                      </defs>
                      <CartesianGrid strokeDasharray="3 3" className="stroke-muted" />
                      <XAxis dataKey="fecha" tickFormatter={(v: string) => v.slice(5)} fontSize={11} />
                      <YAxis fontSize={11} allowDecimals={false} />
                      <Tooltip labelFormatter={(l) => `Fecha: ${l}`} />
                      <Legend wrapperStyle={{ fontSize: 12 }} />
                      <Area type="monotone" dataKey="envios" name="Envíos" stroke="#10b981" fill="url(#gEnv)" strokeWidth={2} />
                      <Area type="monotone" dataKey="delivery" name="Entregas" stroke="#38bdf8" fill="url(#gEnt)" strokeWidth={2} />
                      <Area type="monotone" dataKey="bounce" name="Rebotes" stroke="#f59e0b" fill="transparent" strokeWidth={1.5} />
                      <Area type="monotone" dataKey="complaint" name="Quejas" stroke="#ef4444" fill="transparent" strokeWidth={1.5} />
                    </AreaChart>
                  </ResponsiveContainer>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Gauge className="h-4 w-4 text-emerald-600" /> Cuota de Amazon SES
                </CardTitle>
                <CardDescription>Región {cuota?.region || "us-east-2"}</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                {cuota?.error ? (
                  <p className="text-sm text-muted-foreground">
                    Cuota no disponible ahora ({cuota.error}). El envío no se ve afectado.
                  </p>
                ) : (
                  <>
                    <div>
                      <div className="flex justify-between text-sm mb-1.5">
                        <span className="text-muted-foreground">Consumo (24 h)</span>
                        <span className="font-medium tabular-nums">
                          {numero(cuota?.sent_last_24h || 0)} / {numero(cuota?.max_24h || 0)}
                        </span>
                      </div>
                      <Progress value={cuotaPct} className="h-2" />
                    </div>
                    <div className="flex justify-between text-sm">
                      <span className="text-muted-foreground">Máximo por segundo</span>
                      <span className="font-medium tabular-nums">{cuota?.max_per_second ?? "—"}</span>
                    </div>
                  </>
                )}
                <div className="grid grid-cols-2 gap-2 pt-2 border-t">
                  <div className="text-center p-2 rounded-lg bg-muted/50">
                    <p className="text-lg font-bold tabular-nums">{numero(r.contactos?.total ?? 0)}</p>
                    <p className="text-[11px] text-muted-foreground">Contactos</p>
                  </div>
                  <div className="text-center p-2 rounded-lg bg-muted/50">
                    <p className="text-lg font-bold tabular-nums">{numero(r.suprimidos)}</p>
                    <p className="text-[11px] text-muted-foreground">Suprimidos</p>
                  </div>
                </div>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardHeader>
              <CardTitle>Campañas recientes</CardTitle>
            </CardHeader>
            <CardContent>
              {campanas && campanas.items.length > 0 ? (
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Campaña</TableHead>
                      <TableHead className="hidden sm:table-cell">Estado</TableHead>
                      <TableHead className="hidden md:table-cell">Enviados</TableHead>
                      <TableHead className="text-right">Creada</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {campanas.items.map((c) => (
                      <TableRow key={c.id}>
                        <TableCell>
                          <Link href={`/panel/campanas/${c.id}`} className="font-medium hover:text-emerald-600 hover:underline">
                            {c.name}
                          </Link>
                          <p className="text-xs text-muted-foreground truncate max-w-[240px]">{c.subject}</p>
                        </TableCell>
                        <TableCell className="hidden sm:table-cell"><StatusBadge estado={c.status} /></TableCell>
                        <TableCell className="hidden md:table-cell tabular-nums">
                          {c.progreso ? `${c.progreso.procesados}/${c.progreso.total}` : "—"}
                        </TableCell>
                        <TableCell className="text-right text-muted-foreground text-sm">{fecha(c.created_at)}</TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              ) : (
                <EmptyState icon={FileText} titulo="Sin campañas todavía"
                  descripcion="Crea tu primera campaña a partir de una plantilla."
                  accion={<Button asChild variant="outline" className="mt-1"><Link href="/panel/campanas/nueva">Nueva campaña</Link></Button>} />
              )}
            </CardContent>
          </Card>

          <p className="text-xs text-muted-foreground flex items-start gap-1.5">
            <Eye className="h-3.5 w-3.5 mt-0.5 shrink-0" />
            * Las aperturas son aproximadas: los bloqueadores de imágenes, proxies y mecanismos de
            privacidad pueden subestimarlas o distorsionarlas. Ninguna métrica garantiza llegada a Recibidos.
          </p>
        </>
      )}
    </div>
  );
}

interface Paged2 {
  items: Campaign[];
}
