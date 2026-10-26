"use client";

import { use, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft, Loader2, Send, CheckCircle2, CalendarClock, Pause, Play, XCircle,
  Copy, TestTube2, RefreshCw, FileText,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Progress } from "@/components/ui/progress";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { api, Campaign, CampaignMessage, Paged } from "@/lib/api";
import { EmptyState, StatusBadge, fecha, numero } from "@/components/shared";
import { useAuth } from "@/lib/providers";

export default function CampanaDetallePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const { user } = useAuth();
  const qc = useQueryClient();
  const esAdmin = user?.role === "admin";

  const invalidate = () => {
    qc.invalidateQueries({ queryKey: ["campana", id] });
    qc.invalidateQueries({ queryKey: ["campanas"] });
    qc.invalidateQueries({ queryKey: ["mensajes-campana", id] });
  };

  const { data: c, isLoading } = useQuery({
    queryKey: ["campana", id],
    queryFn: () => api.get<Campaign>(`/api/v1/campaigns/${id}`),
    refetchInterval: (q) => (["en_cola", "enviando"].includes(q.state.data?.status ?? "") ? 4000 : 15000),
  });

  const accion = useMutation({
    mutationFn: ({ path, body }: { path: string; body?: unknown }) =>
      api.post<{ estado?: string; [k: string]: unknown }>(`/api/v1/campaigns/${id}/${path}`, body ?? {}),
    onSuccess: (res) => {
      toast.success("Acción realizada" + (res.estado ? `: ${res.estado.replace("_", " ")}` : ""));
      invalidate();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const programar = useMutation({
    mutationFn: (vals: { scheduled_at: string }) =>
      api.post(`/api/v1/campaigns/${id}/schedule`, {
        scheduled_at: new Date(vals.scheduled_at).toISOString(),
        timezone: "America/Lima",
      }),
    onSuccess: () => { toast.success("Campaña programada"); invalidate(); },
    onError: (e: Error) => toast.error(e.message),
  });

  const [dialogoPrueba, setDialogoPrueba] = useState(false);
  const [dialogoProgramar, setDialogoProgramar] = useState(false);
  const [fechaProgramada, setFechaProgramada] = useState("");
  const [correoPrueba, setCorreoPrueba] = useState("");

  const enviarPrueba = useMutation({
    mutationFn: () => api.post(`/api/v1/campaigns/${id}/test-send`, { to_email: correoPrueba }),
    onSuccess: () => { toast.success("Prueba enviada mediante SES"); setDialogoPrueba(false); },
    onError: (e: Error) => toast.error(e.message),
  });

  const duplicar = useMutation({
    mutationFn: () => api.post<Campaign>(`/api/v1/campaigns/${id}/duplicate`),
    onSuccess: (nueva) => { toast.success("Campaña duplicada"); window.location.href = `/panel/campanas/${nueva.id}`; },
    onError: (e: Error) => toast.error(e.message),
  });

  const { data: mensajes } = useQuery({
    queryKey: ["mensajes-campana", id],
    queryFn: () => api.get<Paged<CampaignMessage>>(`/api/v1/campaigns/${id}/messages?per_page=25`),
    enabled: Boolean(c && (c.progreso?.total ?? 0) > 0),
    refetchInterval: 10_000,
  });

  const { data: eventos } = useQuery({
    queryKey: ["eventos-campana", id],
    queryFn: () => api.get<{ items: { id: string; tipo: string; correo: string | null; url: string | null; ocurrido: string }[] }>(`/api/v1/campaigns/${id}/events?per_page=50`),
    enabled: Boolean(c && (c.progreso?.total ?? 0) > 0),
    refetchInterval: 10_000,
  });

  if (isLoading || !c) {
    return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-muted-foreground" /></div>;
  }

  const progreso = c.progreso;
  const pct = progreso && progreso.total > 0 ? (progreso.procesados / progreso.total) * 100 : 0;
  const puedeEnviar = c.approved_at && ["borrador", "en_revision", "programada", "pausada"].includes(c.status);

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex items-start gap-3 min-w-0">
          <Button variant="ghost" size="icon" asChild aria-label="Volver">
            <Link href="/panel/campanas"><ArrowLeft className="h-4 w-4" /></Link>
          </Button>
          <div className="min-w-0">
            <div className="flex items-center gap-2 flex-wrap">
              <h1 className="text-xl md:text-2xl font-bold tracking-tight truncate">{c.name}</h1>
              <StatusBadge estado={c.status} />
              {c.approved_at && (
                <Badge variant="outline" className="text-emerald-600 border-emerald-500/40">
                  <CheckCircle2 className="h-3 w-3 mr-1" /> Aprobada
                </Badge>
              )}
            </div>
            <p className="text-sm text-muted-foreground mt-0.5 truncate">{c.subject}</p>
          </div>
        </div>

        {/* Acciones según estado */}
        <div className="flex flex-wrap gap-2">
          {!c.approved_at && esAdmin && <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700" onClick={() => accion.mutate({ path: "approve" })}><CheckCircle2 className="mr-1.5 h-4 w-4" /> Aprobar</Button>}
          {!c.approved_at && !esAdmin && c.status === "borrador" && <Button size="sm" variant="outline" onClick={() => accion.mutate({ path: "submit-review" })}>Enviar a revisión</Button>}
          {c.approved_at && ["borrador", "en_revision", "programada", "pausada"].includes(c.status) && (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700"><Send className="mr-1.5 h-4 w-4" /> Enviar ahora</Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>¿Enviar la campaña a todos los destinatarios?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Se encolarán los mensajes y el worker los enviará mediante Amazon SES
                    respetando la velocidad configurada. Esta acción no se puede deshacer,
                    aunque podrás pausar o cancelar mientras haya mensajes pendientes.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancelar</AlertDialogCancel>
                  <AlertDialogAction className="bg-emerald-600 hover:bg-emerald-700"
                    onClick={() => accion.mutate({ path: "send-now" })}>
                    Confirmar envío
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}
          {!c.approved_at && (
            <Button size="sm" variant="outline" onClick={() => setDialogoProgramar(true)} disabled={!esAdmin}>
              <CalendarClock className="mr-1.5 h-4 w-4" /> Programar
            </Button>
          )}
          {c.status === "enviando" && <Button size="sm" variant="outline" onClick={() => accion.mutate({ path: "pause" })}><Pause className="mr-1.5 h-4 w-4" /> Pausar</Button>}
          {c.status === "pausada" && <Button size="sm" variant="outline" onClick={() => accion.mutate({ path: "resume" })}><Play className="mr-1.5 h-4 w-4" /> Reanudar</Button>}
          {["programada", "en_cola", "enviando", "pausada", "borrador", "en_revision"].includes(c.status) && (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button size="sm" variant="outline" className="text-red-600 border-red-500/40"><XCircle className="mr-1.5 h-4 w-4" /> Cancelar</Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>¿Cancelar la campaña?</AlertDialogTitle>
                  <AlertDialogDescription>Los mensajes pendientes quedarán cancelados y no se enviarán.</AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>No</AlertDialogCancel>
                  <AlertDialogAction className="bg-red-600 hover:bg-red-700" onClick={() => accion.mutate({ path: "cancel" })}>Cancelar campaña</AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}
          <Button size="sm" variant="outline" onClick={() => setDialogoPrueba(true)}><TestTube2 className="mr-1.5 h-4 w-4" /> Prueba</Button>
          <Button size="sm" variant="ghost" onClick={() => duplicar.mutate()}><Copy className="mr-1.5 h-4 w-4" /> Duplicar</Button>
        </div>
      </div>

      {/* Progreso */}
      {progreso && progreso.total > 0 && (
        <Card>
          <CardContent className="p-4 space-y-3">
            <div className="flex items-center justify-between text-sm">
              <span className="font-medium">Progreso de envío</span>
              <span className="text-muted-foreground tabular-nums">
                {numero(progreso.procesados)} de {numero(progreso.total)} procesados
              </span>
            </div>
            <Progress value={pct} className="h-2" />
            <div className="grid grid-cols-2 sm:grid-cols-4 lg:grid-cols-6 gap-2 text-center text-xs">
              {[
                ["pendiente", "Pendientes"], ["aceptado", "Aceptados SES"], ["entregado", "Entregados"],
                ["rebote_permanente", "Rebotes"], ["queja", "Quejas"], ["error", "Errores"],
              ].map(([k, l]) => (
                <div key={k} className="rounded-lg bg-muted/60 p-2">
                  <p className="text-base font-bold tabular-nums">{numero(progreso.por_estado[k] || 0)}</p>
                  <p className="text-muted-foreground">{l}</p>
                </div>
              ))}
            </div>
          </CardContent>
        </Card>
      )}

      <Tabs defaultValue="detalles">
        <TabsList>
          <TabsTrigger value="detalles">Detalles</TabsTrigger>
          <TabsTrigger value="mensajes" disabled={!mensajes}>Destinatarios</TabsTrigger>
          <TabsTrigger value="eventos" disabled={!eventos}>Eventos</TabsTrigger>
        </TabsList>

        <TabsContent value="detalles">
          <div className="grid gap-4 md:grid-cols-2 mt-2">
            <Card>
              <CardHeader><CardTitle>Configuración</CardTitle></CardHeader>
              <CardContent className="text-sm space-y-2">
                <Fila k="Remitente" v={`${c.from_name} <${c.from_email}>`} />
                <Fila k="Responder a" v={c.reply_to || "—"} />
                <Fila k="Categoría" v={c.category} />
                <Fila k="Zona horaria" v={c.timezone} />
                <Fila k="Programada" v={c.scheduled_at ? fecha(c.scheduled_at) : "—"} />
                <Fila k="Aprobada" v={fecha(c.approved_at)} />
                <Fila k="Creada" v={fecha(c.created_at)} />
              </CardContent>
            </Card>
            <Card>
              <CardHeader><CardTitle>Contenido</CardTitle></CardHeader>
              <CardContent className="text-sm space-y-2">
                <Fila k="Formato" v={c.mode === "bloques" ? `Bloques (${c.blocks?.length ?? 0})` : "HTML fuente"} />
                <Fila k="Texto plano" v={c.plain_text ? "Generado automáticamente" : "—"} />
                <p className="text-xs text-muted-foreground pt-1">
                  El HTML y el texto plano se renderizan por destinatario con variables
                  ({"{{contact.first_name}}"}) y se sanitizan antes del envío.
                </p>
              </CardContent>
            </Card>
          </div>
        </TabsContent>

        <TabsContent value="mensajes">
          <Card className="mt-2">
            <CardContent className="p-0">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Correo</TableHead>
                    <TableHead>Estado</TableHead>
                    <TableHead className="hidden sm:table-cell">Intentos</TableHead>
                    <TableHead className="hidden md:table-cell">Enviado</TableHead>
                    <TableHead className="hidden lg:table-cell">Detalle</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {mensajes?.items.map((m) => (
                    <TableRow key={m.id}>
                      <TableCell className="font-medium">{m.email}</TableCell>
                      <TableCell><StatusBadge estado={m.status} /></TableCell>
                      <TableCell className="hidden sm:table-cell tabular-nums">{m.attempts}</TableCell>
                      <TableCell className="hidden md:table-cell text-muted-foreground text-sm">{fecha(m.sent_at)}</TableCell>
                      <TableCell className="hidden lg:table-cell text-xs text-muted-foreground max-w-[220px] truncate">
                        {m.last_error || m.ses_message_id || "—"}
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </CardContent>
          </Card>
        </TabsContent>

        <TabsContent value="eventos">
          <Card className="mt-2">
            <CardContent className="p-4">
              {eventos && eventos.items.length > 0 ? (
                <div className="space-y-1.5 text-sm">
                  {eventos.items.map((e) => (
                    <div key={e.id} className="flex items-center gap-2.5 border-b pb-1.5 last:border-0">
                      <Badge variant="outline" className="text-[10px] w-fit">{e.tipo}</Badge>
                      <span className="text-muted-foreground">{e.correo || "—"}</span>
                      {e.url && <span className="text-xs truncate max-w-[220px] text-sky-600">{e.url}</span>}
                      <span className="ml-auto text-xs text-muted-foreground shrink-0">{fecha(e.ocurrido)}</span>
                    </div>
                  ))}
                </div>
              ) : (
                <EmptyState icon={RefreshCw} titulo="Sin eventos aún"
                  descripcion="Los eventos de SES (entregas, rebotes, quejas, aperturas y clics) aparecerán aquí en cuanto se reciban." />
              )}
            </CardContent>
          </Card>
        </TabsContent>
      </Tabs>

      {/* Diálogo prueba */}
      <Dialog open={dialogoPrueba} onOpenChange={setDialogoPrueba}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Enviar prueba de la campaña</DialogTitle>
            <DialogDescription>
              Envío real vía Amazon SES con el contenido de esta campaña y datos de ejemplo.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label>Correo destinatario</Label>
            <Input type="email" value={correoPrueba} onChange={(e) => setCorreoPrueba(e.target.value)}
              placeholder="tu@correo.com" />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogoPrueba(false)}>Cancelar</Button>
            <Button className="bg-emerald-600 hover:bg-emerald-700"
              disabled={!correoPrueba.includes("@") || enviarPrueba.isPending}
              onClick={() => enviarPrueba.mutate()}>
              {enviarPrueba.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Enviar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      {/* Diálogo programar */}
      <Dialog open={dialogoProgramar} onOpenChange={setDialogoProgramar}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Programar envío</DialogTitle>
            <DialogDescription>
              La campaña debe estar aprobada por un administrador. Zona horaria: America/Lima.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label>Fecha y hora</Label>
            <Input type="datetime-local" value={fechaProgramada} onChange={(e) => setFechaProgramada(e.target.value)} />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setDialogoProgramar(false)}>Cancelar</Button>
            <Button className="bg-emerald-600 hover:bg-emerald-700"
              disabled={!fechaProgramada || programar.isPending}
              onClick={() => programar.mutate({ scheduled_at: fechaProgramada })}>
              {programar.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Programar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Fila({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex flex-col sm:flex-row justify-between gap-1 border-b pb-2 last:border-0">
      <span className="text-muted-foreground">{k}</span>
      <span className="font-medium text-right break-all">{v}</span>
    </div>
  );
}
