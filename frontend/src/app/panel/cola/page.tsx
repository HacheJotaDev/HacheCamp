"use client";

import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ListOrdered, Pause, Play, XCircle, RefreshCw, Gauge, Clock, Clock4,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Progress } from "@/components/ui/progress";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { api } from "@/lib/api";
import { EmptyState, StatusBadge, fecha, numero } from "@/components/shared";

interface ColaItem {
  id: string;
  name: string;
  subject: string;
  status: string;
  total: number;
  procesados: number;
  por_estado: Record<string, number>;
  enviados: number;
  pendientes: number;
  entregados: number;
  fallidos: number;
  rebotes: number;
  reintentos: number;
  velocidad_por_minuto: number;
  hora_inicio: string | null;
  hora_fin: string | null;
  ultimo_error: string | null;
}

export default function ColaPage() {
  const qc = useQueryClient();

  const { data, isLoading } = useQuery({
    queryKey: ["cola"],
    queryFn: () => api.get<{ items: ColaItem[]; actualizado: string }>("/api/v1/campaigns/queue/active"),
    refetchInterval: 3000, // actualización en tiempo real (polling eficiente)
  });

  const accion = useMutation({
    mutationFn: ({ id, path }: { id: string; path: string }) =>
      api.post(`/api/v1/campaigns/${id}/${path}`),
    onSuccess: (_res, vars) => {
      toast.success(vars.path === "pause" ? "Campaña pausada"
        : vars.path === "resume" ? "Campaña reanudada" : "Campaña cancelada");
      qc.invalidateQueries({ queryKey: ["cola"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const items = data?.items ?? [];

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Cola de envíos</h1>
          <p className="text-sm text-muted-foreground">
            Progreso en tiempo real · se actualiza cada 3 segundos
          </p>
        </div>
        <div className="flex items-center gap-2 text-xs text-muted-foreground">
          <RefreshCw className="h-3.5 w-3.5" /> Última actualización: {data ? fecha(data.actualizado) : "—"}
        </div>
      </div>

      {isLoading ? (
        <div className="space-y-3">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-40" />)}</div>
      ) : items.length === 0 ? (
        <Card>
          <CardContent>
            <EmptyState
              icon={ListOrdered}
              titulo="La cola está vacía"
              descripcion="Cuando envíes o programes una campaña, su progreso aparecerá aquí en tiempo real."
              accion={
                <Button asChild className="mt-2 bg-emerald-600 hover:bg-emerald-700">
                  <Link href="/panel/campanas/nueva">Crear campaña</Link>
                </Button>
              }
            />
          </CardContent>
        </Card>
      ) : (
        <div className="space-y-4">
          {items.map((c) => {
            const pct = c.total > 0 ? (c.procesados / c.total) * 100 : 0;
            return (
              <Card key={c.id}>
                <CardHeader className="pb-2">
                  <div className="flex flex-wrap items-start justify-between gap-2">
                    <div className="min-w-0">
                      <CardTitle className="text-base flex items-center gap-2 flex-wrap">
                        <Link href={`/panel/campanas/${c.id}`} className="hover:text-emerald-600 hover:underline truncate">
                          {c.name}
                        </Link>
                        <StatusBadge estado={c.status} />
                      </CardTitle>
                      <p className="text-xs text-muted-foreground truncate">{c.subject}</p>
                    </div>
                    <div className="flex gap-2 shrink-0">
                      {c.status === "enviando" && (
                        <Button size="sm" variant="outline"
                          onClick={() => accion.mutate({ id: c.id, path: "pause" })}>
                          <Pause className="mr-1.5 h-4 w-4" /> Pausar
                        </Button>
                      )}
                      {c.status === "pausada" && (
                        <Button size="sm" variant="outline"
                          onClick={() => accion.mutate({ id: c.id, path: "resume" })}>
                          <Play className="mr-1.5 h-4 w-4" /> Reanudar
                        </Button>
                      )}
                      {["en_cola", "enviando", "pausada"].includes(c.status) && (
                        <AlertDialog>
                          <AlertDialogTrigger asChild>
                            <Button size="sm" variant="outline" className="text-red-600 border-red-500/40">
                              <XCircle className="mr-1.5 h-4 w-4" /> Cancelar
                            </Button>
                          </AlertDialogTrigger>
                          <AlertDialogContent>
                            <AlertDialogHeader>
                              <AlertDialogTitle>¿Cancelar «{c.name}»?</AlertDialogTitle>
                              <AlertDialogDescription>
                                Los {numero(c.pendientes)} mensajes pendientes quedarán cancelados y no se enviarán.
                                Los ya enviados no se ven afectados.
                              </AlertDialogDescription>
                            </AlertDialogHeader>
                            <AlertDialogFooter>
                              <AlertDialogCancel>No</AlertDialogCancel>
                              <AlertDialogAction className="bg-red-600 hover:bg-red-700"
                                onClick={() => accion.mutate({ id: c.id, path: "cancel" })}>
                                Sí, cancelar campaña
                              </AlertDialogAction>
                            </AlertDialogFooter>
                          </AlertDialogContent>
                        </AlertDialog>
                      )}
                    </div>
                  </div>
                </CardHeader>
                <CardContent className="space-y-3">
                  <div>
                    <div className="flex items-center justify-between text-sm mb-1.5">
                      <span className="tabular-nums font-medium">
                        {numero(c.procesados)} / {numero(c.total)} procesados
                      </span>
                      <span className="text-muted-foreground tabular-nums">{pct.toFixed(1)}%</span>
                    </div>
                    <Progress value={pct} className="h-2.5" />
                  </div>

                  <div className="grid grid-cols-3 sm:grid-cols-5 lg:grid-cols-8 gap-2 text-center text-xs">
                    {[
                      ["Total", c.total, "text-foreground"],
                      ["Enviados", c.enviados, "text-sky-600"],
                      ["Pendientes", c.pendientes, "text-muted-foreground"],
                      ["Entregados", c.entregados, "text-emerald-600"],
                      ["Fallidos", c.fallidos, "text-red-600"],
                      ["Rebotes", c.rebotes, "text-amber-600"],
                      ["Reintentos", c.reintentos, "text-violet-600"],
                    ].map(([l, v, cls]) => (
                      <div key={String(l)} className="rounded-lg bg-muted/60 p-2">
                        <p className={`text-base font-bold tabular-nums ${cls}`}>{numero(v as number)}</p>
                        <p className="text-muted-foreground">{l}</p>
                      </div>
                    ))}
                    <div className="rounded-lg bg-muted/60 p-2">
                      <p className="text-base font-bold tabular-nums flex items-center justify-center gap-1">
                        <Gauge className="h-3.5 w-3.5 text-emerald-600" />{c.velocidad_por_minuto}
                      </p>
                      <p className="text-muted-foreground">Por minuto</p>
                    </div>
                  </div>

                  <div className="flex flex-wrap gap-x-5 gap-y-1 text-xs text-muted-foreground">
                    <span className="flex items-center gap-1">
                      <Clock className="h-3.5 w-3.5" /> Inicio: {c.hora_inicio ? fecha(c.hora_inicio) : "—"}
                    </span>
                    <span className="flex items-center gap-1">
                      <Clock4 className="h-3.5 w-3.5" /> Fin: {c.hora_fin ? fecha(c.hora_fin) : "en curso"}
                    </span>
                  </div>

                  {c.ultimo_error && (
                    <p className="text-xs text-red-600 bg-red-500/10 rounded-md p-2.5 break-all">
                      Último error: {c.ultimo_error}
                    </p>
                  )}
                </CardContent>
              </Card>
            );
          })}
        </div>
      )}
    </div>
  );
}
