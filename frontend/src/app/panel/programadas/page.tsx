"use client";

import Link from "next/link";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  CalendarClock, History, Loader2, Pencil, XCircle, ArrowRight,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { api, Campaign } from "@/lib/api";
import { EmptyState, StatusBadge, fecha, numero } from "@/components/shared";

const ZONAS = [
  "America/Lima", "America/Bogota", "America/Mexico_City", "America/Santiago",
  "America/Buenos_Aires", "America/Madrid", "UTC",
];

export default function ProgramadasPage() {
  const qc = useQueryClient();
  const [dialogo, setDialogo] = useState<Campaign | null>(null);
  const [fechaHora, setFechaHora] = useState("");
  const [zona, setZona] = useState("America/Lima");

  const { data: proximas, isLoading } = useQuery({
    queryKey: ["programadas-proximas"],
    queryFn: () => api.get<PagedC>("/api/v1/campaigns/scheduled/upcoming"),
    refetchInterval: 30_000,
  });
  const { data: historial } = useQuery({
    queryKey: ["programadas-historial"],
    queryFn: () => api.get<PagedC>("/api/v1/campaigns/scheduled/history"),
  });

  const reprogramar = useMutation({
    mutationFn: (c: Campaign) =>
      api.put(`/api/v1/campaigns/${c.id}/schedule`, {
        scheduled_at: new Date(fechaHora).toISOString(),
        timezone: zona,
      }),
    onSuccess: () => {
      toast.success("Programación modificada");
      setDialogo(null);
      qc.invalidateQueries({ queryKey: ["programadas-proximas"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const cancelarProgramacion = useMutation({
    mutationFn: (c: Campaign) => api.post(`/api/v1/campaigns/${c.id}/schedule/cancel`),
    onSuccess: () => {
      toast.success("Programación cancelada; la campaña vuelve a borrador");
      qc.invalidateQueries({ queryKey: ["programadas-proximas"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const abrirDialogo = (c: Campaign) => {
    setDialogo(c);
    // Precarga: fecha actual de la campaña en formato datetime-local
    if (c.scheduled_at) {
      const d = new Date(c.scheduled_at);
      d.setMinutes(d.getMinutes() - d.getTimezoneOffset());
      setFechaHora(d.toISOString().slice(0, 16));
      setZona(c.timezone || "America/Lima");
    }
  };

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Envíos programados</h1>
        <p className="text-sm text-muted-foreground">
          Se ejecutan automáticamente a la hora indicada; persisten tras reinicios del servidor.
        </p>
      </div>

      <Tabs defaultValue="proximas">
        <TabsList>
          <TabsTrigger value="proximas" className="gap-1.5">
            <CalendarClock className="h-4 w-4" /> Próximos
            {proximas?.total ? <span className="ml-1 text-xs">({numero(proximas.total)})</span> : null}
          </TabsTrigger>
          <TabsTrigger value="historial" className="gap-1.5">
            <History className="h-4 w-4" /> Historial
            {historial?.total ? <span className="ml-1 text-xs">({numero(historial.total)})</span> : null}
          </TabsTrigger>
        </TabsList>

        <TabsContent value="proximas">
          {isLoading ? (
            <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-16" />)}</div>
          ) : !proximas || proximas.items.length === 0 ? (
            <Card>
              <CardContent>
                <EmptyState icon={CalendarClock} titulo="No hay envíos programados"
                  descripcion="Programa una campaña desde el asistente de creación (paso 4) y aparecerá aquí."
                  accion={<Button asChild className="mt-2 bg-emerald-600 hover:bg-emerald-700">
                    <Link href="/panel/campanas/nueva">Nueva campaña</Link></Button>} />
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-2 mt-2">
              {proximas.items.map((c) => (
                <Card key={c.id}>
                  <CardContent className="p-4 flex flex-col sm:flex-row sm:items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Link href={`/panel/campanas/${c.id}`} className="font-medium hover:text-emerald-600 hover:underline truncate">
                          {c.name}
                        </Link>
                        <StatusBadge estado={c.status} />
                      </div>
                      <p className="text-sm text-muted-foreground truncate">{c.subject}</p>
                      <p className="text-xs text-muted-foreground mt-1 flex items-center gap-1">
                        <CalendarClock className="h-3.5 w-3.5" />
                        {fecha(c.scheduled_at)} · {c.timezone}
                      </p>
                    </div>
                    <div className="flex gap-2 shrink-0">
                      <Button size="sm" variant="outline" onClick={() => abrirDialogo(c)}>
                        <Pencil className="mr-1.5 h-4 w-4" /> Modificar
                      </Button>
                      <Button size="sm" variant="outline" className="text-red-600 border-red-500/40"
                        disabled={cancelarProgramacion.isPending}
                        onClick={() => {
                          if (confirm(`¿Cancelar la programación de «${c.name}»? La campaña volverá a borrador.`)) {
                            cancelarProgramacion.mutate(c);
                          }
                        }}>
                        <XCircle className="mr-1.5 h-4 w-4" /> Cancelar
                      </Button>
                    </div>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="historial">
          {!historial || historial.items.length === 0 ? (
            <Card>
              <CardContent>
                <EmptyState icon={History} titulo="Sin historial todavía"
                  descripcion="Las campañas que estuvieron programadas aparecerán aquí cuando se completen o cancelen." />
              </CardContent>
            </Card>
          ) : (
            <div className="space-y-2 mt-2">
              {historial.items.map((c) => (
                <Card key={c.id}>
                  <CardContent className="p-4 flex flex-col sm:flex-row sm:items-center gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <Link href={`/panel/campanas/${c.id}`} className="font-medium hover:text-emerald-600 hover:underline truncate">
                          {c.name}
                        </Link>
                        <StatusBadge estado={c.status} />
                      </div>
                      <p className="text-xs text-muted-foreground mt-1">
                        Programada para {fecha(c.scheduled_at)} · {c.timezone}
                        {c.completed_at && <> · Finalizó {fecha(c.completed_at)}</>}
                      </p>
                    </div>
                    <Button size="sm" variant="ghost" asChild className="shrink-0">
                      <Link href={`/panel/campanas/${c.id}`}>Ver detalle <ArrowRight className="ml-1 h-4 w-4" /></Link>
                    </Button>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>

      {/* Diálogo reprogramar */}
      <Dialog open={Boolean(dialogo)} onOpenChange={(v) => !v && setDialogo(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Modificar programación</DialogTitle>
            <DialogDescription>
              {dialogo?.name} · fecha y zona horaria actuales: {fecha(dialogo?.scheduled_at)} ({dialogo?.timezone})
            </DialogDescription>
          </DialogHeader>
          <div className="grid sm:grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label>Nueva fecha y hora</Label>
              <Input type="datetime-local" value={fechaHora} onChange={(e) => setFechaHora(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label>Zona horaria</Label>
              <Select value={zona} onValueChange={setZona}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {ZONAS.map((z) => <SelectItem key={z} value={z}>{z}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogo(null)}>Cancelar</Button>
            <Button className="bg-emerald-600 hover:bg-emerald-700"
              disabled={!fechaHora || reprogramar.isPending}
              onClick={() => dialogo && reprogramar.mutate(dialogo)}>
              {reprogramar.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Guardar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

interface PagedC {
  total: number;
  items: Campaign[];
}
