"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Users, Plus, Upload, Download, Search, Loader2, FileSpreadsheet, Trash2, Eye,
  ListX, CheckCircle2, XCircle, Ban, Clock, Undo2, Eraser, AlertTriangle,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Checkbox } from "@/components/ui/checkbox";
import { Progress } from "@/components/ui/progress";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { api, BulkOperation, Contact, ContactList, ImportJob, Paged } from "@/lib/api";
import { useAuth } from "@/lib/providers";
import { EmptyState, StatusBadge, fecha, numero } from "@/components/shared";

const ETIQUETAS_TIPO_BULK: Record<string, string> = {
  eliminar_ids: "Eliminar selección",
  eliminar_filtro: "Eliminar filtrados",
  vaciar_todo: "Vaciar todos los contactos",
  purgar: "Purga física",
};

const ETIQUETAS_ESTADO_BULK: Record<BulkOperation["status"], string> = {
  pendiente: "En cola",
  procesando: "Procesando",
  completada: "Completada",
  fallida: "Fallida",
  cancelada: "Cancelada",
  cancelada_parcial: "Cancelada (parcial)",
};

export default function ContactosPage() {
  const [q, setQ] = useState("");
  const [estado, setEstado] = useState("todos");
  const [listId, setListId] = useState("todas");
  const [page, setPage] = useState(1);
  const [papelera, setPapelera] = useState(false);
  const [seleccion, setSeleccion] = useState<Set<string>>(new Set());
  // ids a eliminar en el diálogo de confirmación (selección múltiple o fila individual)
  const [idsAEliminar, setIdsAEliminar] = useState<string[] | null>(null);
  const [aEliminarFiltrados, setAEliminarFiltrados] = useState(false);
  const [vaciarAbierto, setVaciarAbierto] = useState(false);
  // Operación masiva en curso (progreso del panel)
  const [opId, setOpId] = useState<string | null>(null);
  const qc = useQueryClient();
  const { user } = useAuth();
  const esAdmin = user?.role === "admin";

  const filtros = useMemo(() => {
    const p = new URLSearchParams({ page: String(page), per_page: "25" });
    if (q) p.set("q", q);
    if (estado !== "todos") p.set("estado", estado);
    if (listId !== "todas") p.set("list_id", listId);
    if (papelera) p.set("papelera", "true");
    return p.toString();
  }, [q, estado, listId, page, papelera]);

  const { data, isLoading } = useQuery({
    queryKey: ["contactos", filtros],
    queryFn: () => api.get<Paged<Contact>>(`/api/v1/contacts?${filtros}`),
  });

  const { data: listas } = useQuery({
    queryKey: ["listas"],
    queryFn: () => api.get<ContactList[]>("/api/v1/lists"),
  });

  // Progreso de la última operación masiva lanzada desde esta sesión
  const { data: op } = useQuery({
    queryKey: ["bulk-op", opId],
    queryFn: () => api.get<BulkOperation>(`/api/v1/contacts/bulk/${opId}`),
    enabled: !!opId,
    refetchInterval: (query) =>
      query.state.data && (query.state.data.status === "pendiente" || query.state.data.status === "procesando")
        ? 2000 : false,
  });

  const opActiva = !!op && (op.status === "pendiente" || op.status === "procesando");

  useEffect(() => {
    if (op && !opActiva) {
      qc.invalidateQueries({ queryKey: ["contactos"] });
    }
  }, [opActiva, op, qc]);

  const invalidar = () => {
    qc.invalidateQueries({ queryKey: ["contactos"] });
    qc.invalidateQueries({ queryKey: ["bulk-op"] });
    setSeleccion(new Set());
  };

  const borrarMasivo = useMutation({
    mutationFn: (modo: "eliminar" | "dar_baja") =>
      api.post<{ procesados: number; afectados?: number }>("/api/v1/contacts/bulk-delete", {
        ids: idsAEliminar ?? [], modo, confirmar: true,
      }),
    onSuccess: (res, modo) => {
      toast.success(modo === "eliminar"
        ? `${res.afectados ?? res.procesados} contacto(s) en la papelera (restaurables)`
        : `${res.procesados} contacto(s) dados de baja y suprimidos`);
      setIdsAEliminar(null);
      invalidar();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const eliminarFiltrados = useMutation({
    mutationFn: () =>
      api.post<BulkOperation>("/api/v1/contacts/bulk-delete-filtrado", {
        q: q || null, estado: estado !== "todos" ? estado : null,
        list_id: listId !== "todas" ? listId : null, confirmar: true,
      }),
    onSuccess: (res) => {
      toast.info(`Eliminación en cola: ${numero(res.total_objetivo)} contacto(s) en proceso`);
      setAEliminarFiltrados(false);
      setOpId(res.id);
      invalidar();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const cancelarOp = useMutation({
    mutationFn: (id: string) => api.post(`/api/v1/contacts/bulk/${id}/cancel`),
    onSuccess: () => toast.info("Cancelación solicitada (efectiva entre lotes)"),
    onError: (e: Error) => toast.error(e.message),
  });

  const restaurarOp = useMutation({
    mutationFn: (id: string) => api.post<{ restaurados: number }>(`/api/v1/contacts/bulk/${id}/restore`),
    onSuccess: (res) => {
      toast.success(`${numero(res.restaurados)} contacto(s) restaurados`);
      invalidar();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const restaurarIndividual = useMutation({
    mutationFn: (id: string) => api.post(`/api/v1/contacts/${id}/restore`),
    onSuccess: () => {
      toast.success("Contacto restaurado");
      invalidar();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const items = data?.items ?? [];
  const todosSeleccionados = items.length > 0 && items.every((c) => seleccion.has(c.id));
  const alternarTodos = () => {
    setSeleccion(todosSeleccionados ? new Set() : new Set(items.map((c) => c.id)));
  };

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            Contactos {papelera && <span className="text-muted-foreground">· Papelera</span>}
          </h1>
          <p className="text-sm text-muted-foreground">
            {data ? `${numero(data.total)} contacto(s)${papelera ? " en papelera" : ""}` : "Suscriptores de tus campañas"}
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <ImportarDialog onImportado={invalidar} />
          <Button variant="outline" asChild>
            <a href="/api/v1/contacts/export/csv" download><Download className="mr-2 h-4 w-4" /> Exportar</a>
          </Button>
          <NuevoContactoDialog onCreado={invalidar} />
        </div>
      </div>

      {/* Barra de herramientas */}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant={papelera ? "default" : "outline"} onClick={() => { setPapelera(!papelera); setPage(1); }}>
          <Trash2 className="mr-2 h-4 w-4" /> Papelera
        </Button>
        {esAdmin && !papelera && (
          <>
            <Button variant="outline" onClick={() => setAEliminarFiltrados(true)}>
              <Eraser className="mr-2 h-4 w-4" /> Eliminar filtrados…
            </Button>
            <Button variant="outline" className="text-red-600 hover:text-red-700 border-red-600/40"
              onClick={() => setVaciarAbierto(true)}>
              <AlertTriangle className="mr-2 h-4 w-4" /> Vaciar todos los contactos
            </Button>
          </>
        )}
      </div>

      {/* Progreso de operación masiva */}
      {op && (
        <Card>
          <CardContent className="p-4 space-y-2">
            <div className="flex items-center gap-2 text-sm flex-wrap">
              <span className="font-medium">{ETIQUETAS_TIPO_BULK[op.tipo] ?? op.tipo}</span>
              <span className={opActiva ? "text-emerald-600" : "text-muted-foreground"}>
                {ETIQUETAS_ESTADO_BULK[op.status] ?? op.status}
              </span>
              {op.cancel_requested && opActiva && <span className="text-amber-600 text-xs">· cancelando…</span>}
            </div>
            <Progress value={op.progreso_pct} />
            <p className="text-xs text-muted-foreground">
              {numero(op.procesados)} de {numero(op.total_objetivo)} ({op.progreso_pct}%)
              {opActiva && " · continúa en segundo plano; puedes navegar por el panel"}
            </p>
            <div className="flex flex-wrap gap-2 text-xs">
              <span className="rounded bg-muted px-2 py-0.5">Afectados: {numero(op.afectados)}</span>
              {op.omitidos > 0 && <span className="rounded bg-muted px-2 py-0.5">Omitidos: {numero(op.omitidos)}</span>}
            </div>
            {op.status === "fallida" && op.last_error && (
              <p className="text-xs text-red-600">{op.last_error}</p>
            )}
            {!opActiva && op.afectados > 0 && op.tipo !== "purgar" && esAdmin && (
              <Button size="sm" variant="outline" onClick={() => restaurarOp.mutate(op.id)}>
                <Undo2 className="mr-2 h-4 w-4" /> Restaurar los {numero(op.afectados)} eliminados
              </Button>
            )}
            {opActiva && (
              <Button size="sm" variant="outline" onClick={() => cancelarOp.mutate(op.id)}>
                <Ban className="mr-2 h-4 w-4" /> Cancelar operación
              </Button>
            )}
          </CardContent>
        </Card>
      )}

      {seleccion.size > 0 && !papelera && (
        <div className="flex flex-wrap items-center gap-3 rounded-lg border bg-emerald-500/5 px-4 py-3 text-sm">
          <span className="font-medium">{seleccion.size} seleccionado(s)</span>
          <div className="flex gap-2 ml-auto">
            <Button size="sm" variant="outline" onClick={() => setIdsAEliminar(Array.from(seleccion))}>
              <Trash2 className="mr-1.5 h-4 w-4" /> Eliminar seleccionados
            </Button>
            <Button size="sm" variant="ghost" onClick={() => setSeleccion(new Set())}>
              Cancelar selección
            </Button>
          </div>
        </div>
      )}

      <Card>
        <CardContent className="p-4 space-y-4">
          <div className="flex flex-col sm:flex-row gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Buscar por correo…"
                className="pl-9"
                inputMode="email"
                value={q}
                onChange={(e) => { setQ(e.target.value); setPage(1); }}
              />
            </div>
            <Select value={estado} onValueChange={(v) => { setEstado(v); setPage(1); }}>
              <SelectTrigger className="w-full sm:w-44"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="todos">Todos los estados</SelectItem>
                <SelectItem value="suscripto">Suscripto</SelectItem>
                <SelectItem value="dado_baja">Dado de baja</SelectItem>
                <SelectItem value="rebote">Rebote</SelectItem>
                <SelectItem value="queja">Queja</SelectItem>
              </SelectContent>
            </Select>
            <Select value={listId} onValueChange={(v) => { setListId(v); setPage(1); }} disabled={papelera}>
              <SelectTrigger className="w-full sm:w-44"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="todas">Todas las listas</SelectItem>
                {listas?.map((l) => <SelectItem key={l.id} value={l.id}>{l.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>

          {isLoading ? (
            <div className="space-y-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
          ) : !data || data.items.length === 0 ? (
            <EmptyState
              icon={papelera ? Trash2 : Users}
              titulo={papelera ? "Papelera vacía" : q || estado !== "todos" ? "Sin resultados" : "Aún no hay contactos"}
              descripcion={papelera
                ? "Los contactos eliminados aparecen aquí y pueden restaurarse con su historial completo."
                : q || estado !== "todos"
                  ? "Prueba con otros criterios de búsqueda."
                  : "Importa tu lista CSV/TXT o crea un contacto escribiendo solo su correo."}
            />
          ) : (
            <>
              <div className="overflow-x-auto -mx-4 px-4">
                <Table>
                  <TableHeader>
                    <TableRow>
                      {!papelera && (
                        <TableHead className="w-10">
                          <Checkbox
                            aria-label="Seleccionar todos"
                            checked={todosSeleccionados}
                            onCheckedChange={alternarTodos}
                          />
                        </TableHead>
                      )}
                      <TableHead>Correo</TableHead>
                      <TableHead>Estado</TableHead>
                      <TableHead className="hidden md:table-cell">Listas</TableHead>
                      <TableHead className="hidden md:table-cell">{papelera ? "Eliminado" : "Alta"}</TableHead>
                      <TableHead className="text-right">Acciones</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.items.map((c) => (
                      <TableRow key={c.id} className={seleccion.has(c.id) ? "bg-emerald-500/5" : ""}>
                        {!papelera && (
                          <TableCell>
                            <Checkbox
                              aria-label={`Seleccionar ${c.email}`}
                              checked={seleccion.has(c.id)}
                              onCheckedChange={(v) => {
                                const nuevo = new Set(seleccion);
                                if (v) nuevo.add(c.id); else nuevo.delete(c.id);
                                setSeleccion(nuevo);
                              }}
                            />
                          </TableCell>
                        )}
                        <TableCell>
                          <Link href={`/panel/contactos/${c.id}`} className="font-medium hover:text-emerald-600 hover:underline break-all">
                            {c.email}
                          </Link>
                        </TableCell>
                        <TableCell><StatusBadge estado={c.status} /></TableCell>
                        <TableCell className="hidden md:table-cell text-sm text-muted-foreground">
                          {c.lists?.map((l) => l.name).join(", ") || "—"}
                        </TableCell>
                        <TableCell className="hidden md:table-cell text-sm text-muted-foreground">
                          {papelera ? fecha(c.deleted_at ?? c.created_at) : fecha(c.created_at)}
                        </TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-0.5">
                            {papelera ? (
                              esAdmin && (
                                <Button variant="ghost" size="icon" aria-label={`Restaurar ${c.email}`}
                                  title="Restaurar contacto"
                                  onClick={() => restaurarIndividual.mutate(c.id)}>
                                  <Undo2 className="h-4 w-4" />
                                </Button>
                              )
                            ) : (
                              <>
                                <Button variant="ghost" size="icon" asChild aria-label="Ver detalle">
                                  <Link href={`/panel/contactos/${c.id}`}><Eye className="h-4 w-4" /></Link>
                                </Button>
                                {esAdmin && (
                                  <Button
                                    variant="ghost" size="icon" aria-label={`Eliminar ${c.email}`}
                                    className="text-red-600 hover:text-red-700"
                                    onClick={() => setIdsAEliminar([c.id])}
                                  >
                                    <Trash2 className="h-4 w-4" />
                                  </Button>
                                )}
                              </>
                            )}
                          </div>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <div className="flex items-center justify-between text-sm text-muted-foreground">
                <span>Página {data.page} de {Math.max(1, Math.ceil(data.total / data.per_page))}</span>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                    Anterior
                  </Button>
                  <Button variant="outline" size="sm" disabled={page * data.per_page >= data.total} onClick={() => setPage((p) => p + 1)}>
                    Siguiente
                  </Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>

      {/* Confirmación de eliminación (individual o masiva) */}
      <AlertDialog
        open={idsAEliminar !== null}
        onOpenChange={(v) => { if (!v) setIdsAEliminar(null); }}
      >
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>
              {idsAEliminar && idsAEliminar.length === 1
                ? "¿Eliminar este contacto?"
                : `¿Eliminar ${idsAEliminar?.length ?? 0} contacto(s)?`}
            </AlertDialogTitle>
            <AlertDialogDescription>
              Puedes <b>eliminarlos</b> (pasan a la <b>papelera</b>: conservan su historial
              y un administrador puede restaurarlos; las importaciones no los reactivan)
              o <b>darlos de baja</b> (quedan en supresión y nunca recibirán campañas).
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter className="flex-col sm:flex-row gap-2">
            <AlertDialogCancel>No, cancelar</AlertDialogCancel>
            <Button variant="outline" className="border-amber-500/50 text-amber-600"
              disabled={borrarMasivo.isPending}
              onClick={() => borrarMasivo.mutate("dar_baja")}>
              <ListX className="mr-1.5 h-4 w-4" /> Dar de baja
            </Button>
            <AlertDialogAction className="bg-red-600 hover:bg-red-700"
              onClick={(e) => { e.preventDefault(); borrarMasivo.mutate("eliminar"); }}>
              <Trash2 className="mr-1.5 h-4 w-4" /> Eliminar (papelera)
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Confirmación de eliminación filtrada */}
      <AlertDialog open={aEliminarFiltrados} onOpenChange={setAEliminarFiltrados}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>¿Eliminar todos los contactos filtrados?</AlertDialogTitle>
            <AlertDialogDescription>
              Se <b>papelera</b> todo contacto que coincida con los filtros actuales
              (búsqueda, estado y lista). Es una operación en segundo plano con
              progreso y cancelación; los datos se conservan y es restaurable.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction className="bg-red-600 hover:bg-red-700"
              disabled={eliminarFiltrados.isPending}
              onClick={(e) => { e.preventDefault(); eliminarFiltrados.mutate(); }}>
              <Trash2 className="mr-1.5 h-4 w-4" /> Eliminar filtrados
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Vaciado global con confirmación reforzada */}
      <VaciarTodoDialog
        abierto={vaciarAbierto}
        onCerrar={() => setVaciarAbierto(false)}
        onLanzada={(id) => { setOpId(id); invalidar(); }}
      />
    </div>
  );
}

function VaciarTodoDialog({ abierto, onCerrar, onLanzada }: {
  abierto: boolean; onCerrar: () => void; onLanzada: (opId: string) => void;
}) {
  const [frase, setFrase] = useState("");
  const qc = useQueryClient();

  // Preview con react-query (sin efectos con setState: lint react-hooks)
  const { data: preview } = useQuery({
    queryKey: ["vaciar-todo-preview"],
    queryFn: () => api.get<{
      contactos_activos: number; en_papelera: number;
      campanas_activas: number; importaciones_en_curso: number;
    }>("/api/v1/contacts/vaciar-todo/preview"),
    enabled: abierto,
    staleTime: 0,
  });

  const vaciar = useMutation({
    mutationFn: () => api.post<BulkOperation>("/api/v1/contacts/vaciar-todo", {
      confirmar: true, confirmar_texto: frase.trim(),
    }),
    onSuccess: (res) => {
      toast.info(`Vaciado en cola: ${numero(res.total_objetivo)} contacto(s) en proceso`);
      qc.invalidateQueries({ queryKey: ["contactos"] });
      onLanzada(res.id);
      onCerrar();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const fraseCorrecta = frase.trim() === "VACIAR CONTACTOS";
  const hayIncompatibles = !!preview && (preview.campanas_activas > 0 || preview.importaciones_en_curso > 0);
  const activos = preview?.contactos_activos ?? 0;

  return (
    <Dialog open={abierto} onOpenChange={(v) => { if (!v) { setFrase(""); onCerrar(); } }}>
      <DialogContent className="max-w-lg">
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2 text-red-600">
            <AlertTriangle className="h-5 w-5" /> Vaciar todos los contactos
          </DialogTitle>
          <DialogDescription>
            Operación administrativa de alto impacto. Escribe la frase exacta para confirmar.
          </DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm">
          {preview ? (
            <div className="grid grid-cols-2 gap-2">
              <div className="rounded-lg bg-muted/60 p-2.5">
                <p className="text-muted-foreground text-xs">Contactos activos afectados</p>
                <p className="font-bold text-lg tabular-nums">{numero(preview.contactos_activos)}</p>
              </div>
              <div className="rounded-lg bg-muted/60 p-2.5">
                <p className="text-muted-foreground text-xs">Ya en papelera</p>
                <p className="font-bold text-lg tabular-nums">{numero(preview.en_papelera)}</p>
              </div>
            </div>
          ) : (
            <Skeleton className="h-16" />
          )}
          {hayIncompatibles && (
            <p className="rounded-lg bg-amber-500/10 border border-amber-500/40 p-2 text-xs text-amber-700">
              Atención: hay {preview?.campanas_activas ?? 0} campaña(s) activa(s) y{" "}
              {preview?.importaciones_en_curso ?? 0} importación(es) en curso.
              Las campañas ya encoladas seguirán enviando (sus destinatarios ya están
              materializados); valora esperar o cancelarlas primero.
            </p>
          )}

          <p className="text-muted-foreground text-xs">
            El vaciado es un <b>borrado lógico</b>: los contactos pasan a la papelera y
            pueden restaurarse. Supresiones, bajas, consentimientos, mensajes y eventos
            SES se conservan íntegros.
          </p>

          <div className="space-y-1.5">
            <Label htmlFor="frase-vaciar">
              Escribe <b>VACIAR CONTACTOS</b> para habilitar el botón
            </Label>
            <Input id="frase-vaciar" value={frase} onChange={(e) => setFrase(e.target.value)}
              placeholder="VACIAR CONTACTOS" autoComplete="off" />
          </div>
        </div>

        <DialogFooter>
          <Button variant="outline" onClick={onCerrar}>Cancelar</Button>
          <Button className="bg-red-600 hover:bg-red-700"
            disabled={!fraseCorrecta || !preview || activos === 0 || vaciar.isPending}
            onClick={() => vaciar.mutate()}>
            {vaciar.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            <Trash2 className="mr-2 h-4 w-4" /> Vaciar todos los contactos
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

function NuevoContactoDialog({ onCreado }: { onCreado: () => void }) {
  const [abierto, setAbierto] = useState(false);
  const [email, setEmail] = useState("");

  const crear = useMutation({
    mutationFn: () => api.post("/api/v1/contacts", { email }),
    onSuccess: () => {
      toast.success("Contacto agregado");
      setAbierto(false);
      setEmail("");
      onCreado();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const valido = /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email.trim());

  return (
    <Dialog open={abierto} onOpenChange={setAbierto}>
      <Button onClick={() => setAbierto(true)} className="bg-emerald-600 hover:bg-emerald-700">
        <Plus className="mr-2 h-4 w-4" /> Nuevo contacto
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Nuevo contacto</DialogTitle>
          <DialogDescription>Solo necesitas el correo electrónico.</DialogDescription>
        </DialogHeader>
        <div className="space-y-1.5">
          <Label htmlFor="c-email">Correo electrónico *</Label>
          <Input
            id="c-email" type="email" inputMode="email" autoFocus
            placeholder="cliente@dominio.com"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            onKeyDown={(e) => { if (e.key === "Enter" && valido && !crear.isPending) crear.mutate(); }}
          />
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setAbierto(false)}>Cancelar</Button>
          <Button className="bg-emerald-600 hover:bg-emerald-700" disabled={!valido || crear.isPending} onClick={() => crear.mutate()}>
            {crear.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Agregar contacto
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface ImportPreview {
  encabezados: string[];
  codificacion: string;
  filas_validas: number;
  filas_invalidas: number;
  muestra: { email: string }[];
}

const ETIQUETAS_ESTADO_IMPORT: Record<ImportJob["estado"], string> = {
  pendiente: "En cola",
  procesando: "Procesando",
  completado: "Completada",
  fallido: "Fallida",
  cancelado: "Cancelada",
};

const NOTICIAS_ETIQUETA: Record<string, string> = {
  correo_suprimido: "En supresión (dados de baja)",
  existente_estado_dado_baja: "Ya dados de baja",
  existente_estado_rebote: "Con rebote",
  existente_estado_queja: "Con queja",
};

function EstadoIcon({ estado }: { estado: ImportJob["estado"] }) {
  if (estado === "completado") return <CheckCircle2 className="h-4 w-4 text-emerald-600" />;
  if (estado === "fallido") return <XCircle className="h-4 w-4 text-red-600" />;
  if (estado === "cancelado") return <Ban className="h-4 w-4 text-amber-600" />;
  if (estado === "procesando") return <Loader2 className="h-4 w-4 animate-spin text-emerald-600" />;
  return <Clock className="h-4 w-4 text-muted-foreground" />;
}

function PanelProgreso({ job, onCancelar }: { job: ImportJob; onCancelar: (jobId: string) => void }) {
  const activo = job.estado === "pendiente" || job.estado === "procesando";
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-sm">
        <EstadoIcon estado={job.estado} />
        <span className="font-medium">{ETIQUETAS_ESTADO_IMPORT[job.estado]}</span>
        <span className="text-muted-foreground ml-auto font-mono text-xs">{job.filename}</span>
      </div>
      <Progress value={job.progreso_pct} />
      <p className="text-xs text-muted-foreground">
        {numero(job.filas_procesadas)} de {numero(job.total_filas)} filas ({job.progreso_pct}%)
        {activo && " · el proceso continúa en segundo plano, puedes cerrar esta ventana"}
      </p>
      <div className="grid grid-cols-2 gap-2 text-sm">
        {([
          ["Creados", job.creados],
          ["Ya existían (actualizados)", job.actualizados],
          ["Inválidos en el archivo", job.filas_invalidas],
          ["Duplicados descartados", job.duplicados_en_archivo],
          ["Errores de fila", job.errores],
        ] as [string, number][]).map(([k, v]) => (
          <div key={k} className="rounded-lg bg-muted/60 p-2.5">
            <p className="text-muted-foreground text-xs">{k}</p>
            <p className="font-bold text-base">{numero(v)}</p>
          </div>
        ))}
        {Object.entries(job.notices ?? {})
          .filter(([k]) => NOTICIAS_ETIQUETA[k])
          .map(([k, v]) => (
            <div key={k} className="rounded-lg bg-amber-500/10 p-2.5">
              <p className="text-xs text-amber-700">{NOTICIAS_ETIQUETA[k]}</p>
              <p className="font-bold text-base">{numero(v)}</p>
            </div>
          ))}
      </div>
      {job.errores > 0 && job.error_samples?.length > 0 && (
        <div className="rounded-lg border border-red-200 bg-red-500/5 p-2 text-xs space-y-1">
          <p className="font-medium text-red-700">Primeras filas con error</p>
          {job.error_samples.slice(0, 5).map((s, i) => (
            <p key={i} className="font-mono break-all text-red-600">
              {s.line ? `línea ${s.line}: ` : ""}{s.error}
            </p>
          ))}
        </div>
      )}
      {job.estado === "fallido" && job.last_error && (
        <p className="rounded-lg bg-red-500/10 p-2 text-xs text-red-700">{job.last_error}</p>
      )}
      {activo && (
        <Button variant="outline" size="sm" className="w-full" onClick={() => onCancelar(job.job_id)}>
          <Ban className="mr-2 h-4 w-4" /> Cancelar importación
        </Button>
      )}
    </div>
  );
}

function ImportarDialog({ onImportado }: { onImportado: () => void }) {
  const [abierto, setAbierto] = useState(false);
  const [archivo, setArchivo] = useState<File | null>(null);
  const [listId, setListId] = useState("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const qc = useQueryClient();
  const { data: listas } = useQuery({
    queryKey: ["listas"],
    queryFn: () => api.get<ContactList[]>("/api/v1/lists"),
  });

  // Progreso del trabajo activo: sondeo mientras vive; se detiene al terminar
  const { data: job } = useQuery({
    queryKey: ["import-job", jobId],
    queryFn: () => api.get<ImportJob>(`/api/v1/contacts/import/${jobId}`),
    enabled: !!jobId,
    refetchInterval: (q) =>
      q.state.data && (q.state.data.estado === "pendiente" || q.state.data.estado === "procesando")
        ? 1500 : false,
  });

  // Al terminar el trabajo: refresca contactos e historial de importaciones
  const jobTerminado = !!job && job.estado !== "pendiente" && job.estado !== "procesando";
  useEffect(() => {
    if (jobTerminado) {
      qc.invalidateQueries({ queryKey: ["contactos"] });
      qc.invalidateQueries({ queryKey: ["import-jobs"] });
    }
  }, [jobTerminado, qc]);

  // Últimas importaciones (para consultar progreso sin re-subir)
  const { data: ultimas } = useQuery({
    queryKey: ["import-jobs"],
    queryFn: () => api.get<ImportJob[]>("/api/v1/contacts/import/jobs?limit=5"),
    enabled: abierto,
    refetchInterval: (q) =>
      q.state.data?.some((j) => j.estado === "pendiente" || j.estado === "procesando") ? 3000 : false,
  });

  const previewMut = useMutation({
    mutationFn: async () => {
      const fd = new FormData();
      fd.append("archivo", archivo!);
      // IMPORTANTE: postForm (no post). api.post serializaría el FormData como
      // JSON "{}" y la API respondería 422 "Field required" (campo 'archivo').
      return api.postForm<ImportPreview>("/api/v1/contacts/import/preview", fd);
    },
    onSuccess: setPreview,
    onError: (e: Error) => toast.error(e.message),
  });

  const cancelar = useMutation({
    mutationFn: (jid: string) => api.post<ImportJob>(`/api/v1/contacts/import/${jid}/cancel`),
    onSuccess: () => toast.info("Cancelación solicitada"),
    onError: (e: Error) => toast.error(e.message),
  });

  const importarDirecto = useMutation({
    mutationFn: async () => {
      const fd = new FormData();
      fd.append("archivo", archivo!);
      fd.append("consent_source", "importacion_csv");
      fd.append("list_ids", listId && listId !== "sin" ? JSON.stringify([listId]) : "[]");
      // postForm: multipart correcto (ver comentario en previewMut)
      return api.postForm<{ job_id: string }>("/api/v1/contacts/import", fd);
    },
    onSuccess: (res) => {
      setJobId(res.job_id);
      toast.success("Importación en cola: procesando en segundo plano");
      qc.invalidateQueries({ queryKey: ["import-jobs"] });
      onImportado();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const reiniciar = () => {
    setArchivo(null); setPreview(null); setJobId(null);
  };

  return (
    <Dialog open={abierto} onOpenChange={(v) => { setAbierto(v); if (!v) reiniciar(); }}>
      <Button variant="outline" onClick={() => setAbierto(true)}>
        <Upload className="mr-2 h-4 w-4" /> Importar
      </Button>
      <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Importar contactos</DialogTitle>
          <DialogDescription>
            CSV (columna <code>email</code>) o TXT (un correo por línea). Máximo 5 MB.
            El proceso corre en segundo plano: puedes cerrar esta ventana y el
            progreso queda registrado aquí.
          </DialogDescription>
        </DialogHeader>

        {jobId && job ? (
          <div className="space-y-3">
            <PanelProgreso job={job} onCancelar={(jid) => cancelar.mutate(jid)} />
            <DialogFooter>
              <Button className="bg-emerald-600 hover:bg-emerald-700"
                onClick={() => { setAbierto(false); reiniciar(); }}>
                Cerrar
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="csv-archivo">Archivo CSV o TXT *</Label>
              <Input
                id="csv-archivo" type="file" accept=".csv,.txt,text/csv,text/plain"
                onChange={(e) => { setArchivo(e.target.files?.[0] ?? null); setPreview(null); }}
              />
            </div>
            <div className="space-y-1.5">
              <Label>Añadir a la lista (opcional)</Label>
              <Select value={listId} onValueChange={setListId}>
                <SelectTrigger><SelectValue placeholder="Sin lista" /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="sin">Sin lista</SelectItem>
                  {listas?.map((l) => <SelectItem key={l.id} value={l.id}>{l.name}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            {archivo && !preview && (
              <Button variant="outline" size="sm" className="w-full" disabled={previewMut.isPending}
                onClick={() => previewMut.mutate()}>
                {previewMut.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FileSpreadsheet className="mr-2 h-4 w-4" />}
                Analizar archivo
              </Button>
            )}
            {preview && (
              <div className="rounded-lg border p-3 text-sm space-y-1">
                <p className="font-medium">Vista previa</p>
                <p className="text-muted-foreground">
                  Codificación: {preview.codificacion} · Correos válidos: <b>{preview.filas_validas}</b> ·
                  Inválidos: <b>{preview.filas_invalidas}</b>
                </p>
                <div className="mt-1 text-xs font-mono bg-muted rounded p-2 max-h-24 overflow-y-auto break-all">
                  {preview.muestra.map((m) => m.email).join(" · ")}
                </div>
              </div>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => setAbierto(false)}>Cancelar</Button>
              <Button
                className="bg-emerald-600 hover:bg-emerald-700"
                disabled={!archivo || !preview || importarDirecto.isPending}
                onClick={() => importarDirecto.mutate()}
              >
                {importarDirecto.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Importar {preview ? `(${numero(preview.filas_validas)})` : ""}
              </Button>
            </DialogFooter>

            {ultimas && ultimas.length > 0 && (
              <div className="space-y-1.5 border-t pt-3">
                <p className="text-sm font-medium">Últimas importaciones</p>
                <div className="space-y-1">
                  {ultimas.map((j) => (
                    <button
                      key={j.job_id}
                      type="button"
                      className="w-full rounded-lg border px-2.5 py-1.5 text-left text-xs hover:bg-muted/60"
                      onClick={() => { setJobId(j.job_id); }}
                    >
                      <span className="flex items-center gap-2">
                        <EstadoIcon estado={j.estado} />
                        <span className="font-medium">{ETIQUETAS_ESTADO_IMPORT[j.estado]}</span>
                        <span className="text-muted-foreground truncate">{j.filename}</span>
                        <span className="ml-auto text-muted-foreground">
                          {j.estado === "completado"
                            ? `${numero(j.creados)} creados`
                            : `${j.progreso_pct}%`}
                        </span>
                      </span>
                    </button>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
