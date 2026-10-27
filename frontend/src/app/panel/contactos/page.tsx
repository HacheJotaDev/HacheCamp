"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Users, Plus, Upload, Download, Search, Loader2, FileSpreadsheet, Trash2, Eye,
  ListX,
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
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { api, Contact, ContactList, Paged } from "@/lib/api";
import { EmptyState, StatusBadge, fecha, numero } from "@/components/shared";

export default function ContactosPage() {
  const [q, setQ] = useState("");
  const [estado, setEstado] = useState("todos");
  const [listId, setListId] = useState("todas");
  const [page, setPage] = useState(1);
  const [seleccion, setSeleccion] = useState<Set<string>>(new Set());
  // ids a eliminar en el diálogo de confirmación (selección múltiple o fila individual)
  const [idsAEliminar, setIdsAEliminar] = useState<string[] | null>(null);
  const qc = useQueryClient();

  const filtros = useMemo(() => {
    const p = new URLSearchParams({ page: String(page), per_page: "25" });
    if (q) p.set("q", q);
    if (estado !== "todos") p.set("estado", estado);
    if (listId !== "todas") p.set("list_id", listId);
    return p.toString();
  }, [q, estado, listId, page]);

  const { data, isLoading } = useQuery({
    queryKey: ["contactos", filtros],
    queryFn: () => api.get<Paged<Contact>>(`/api/v1/contacts?${filtros}`),
  });

  const { data: listas } = useQuery({
    queryKey: ["listas"],
    queryFn: () => api.get<ContactList[]>("/api/v1/lists"),
  });

  const invalidar = () => {
    qc.invalidateQueries({ queryKey: ["contactos"] });
    setSeleccion(new Set());
  };

  const borrarMasivo = useMutation({
    mutationFn: (modo: "eliminar" | "dar_baja") =>
      api.post<{ procesados: number }>("/api/v1/contacts/bulk-delete", {
        ids: idsAEliminar ?? [], modo, confirmar: true,
      }),
    onSuccess: (res, modo) => {
      toast.success(modo === "eliminar"
        ? `${res.procesados} contacto(s) eliminados`
        : `${res.procesados} contacto(s) dados de baja y suprimidos`);
      setIdsAEliminar(null);
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
          <h1 className="text-2xl font-bold tracking-tight">Contactos</h1>
          <p className="text-sm text-muted-foreground">
            {data ? `${numero(data.total)} contacto(s) en total` : "Suscriptores de tus campañas"}
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

      {seleccion.size > 0 && (
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
            <Select value={listId} onValueChange={(v) => { setListId(v); setPage(1); }}>
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
              icon={Users}
              titulo={q || estado !== "todos" ? "Sin resultados" : "Aún no hay contactos"}
              descripcion={q || estado !== "todos"
                ? "Prueba con otros criterios de búsqueda."
                : "Importa tu lista CSV/TXT o crea un contacto escribiendo solo su correo."}
            />
          ) : (
            <>
              <div className="overflow-x-auto -mx-4 px-4">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead className="w-10">
                        <Checkbox
                          aria-label="Seleccionar todos"
                          checked={todosSeleccionados}
                          onCheckedChange={alternarTodos}
                        />
                      </TableHead>
                      <TableHead>Correo</TableHead>
                      <TableHead>Estado</TableHead>
                      <TableHead className="hidden md:table-cell">Listas</TableHead>
                      <TableHead className="hidden md:table-cell">Alta</TableHead>
                      <TableHead className="text-right">Acciones</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.items.map((c) => (
                      <TableRow key={c.id} className={seleccion.has(c.id) ? "bg-emerald-500/5" : ""}>
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
                        <TableCell>
                          <Link href={`/panel/contactos/${c.id}`} className="font-medium hover:text-emerald-600 hover:underline break-all">
                            {c.email}
                          </Link>
                        </TableCell>
                        <TableCell><StatusBadge estado={c.status} /></TableCell>
                        <TableCell className="hidden md:table-cell text-sm text-muted-foreground">
                          {c.lists?.map((l) => l.name).join(", ") || "—"}
                        </TableCell>
                        <TableCell className="hidden md:table-cell text-sm text-muted-foreground">{fecha(c.created_at)}</TableCell>
                        <TableCell className="text-right">
                          <div className="flex items-center justify-end gap-0.5">
                            <Button variant="ghost" size="icon" asChild aria-label="Ver detalle">
                              <Link href={`/panel/contactos/${c.id}`}><Eye className="h-4 w-4" /></Link>
                            </Button>
                            <Button
                              variant="ghost" size="icon" aria-label={`Eliminar ${c.email}`}
                              className="text-red-600 hover:text-red-700"
                              onClick={() => setIdsAEliminar([c.id])}
                            >
                              <Trash2 className="h-4 w-4" />
                            </Button>
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
              Puedes <b>eliminarlos definitivamente</b> (borra también su historial de envíos;
              las supresiones y la auditoría se conservan)
              o <b>darlos de baja</b> (quedan en supresión y nunca recibirán campañas).
              Esta acción no se puede deshacer.
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
              <Trash2 className="mr-1.5 h-4 w-4" /> Eliminar definitivamente
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
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

function ImportarDialog({ onImportado }: { onImportado: () => void }) {
  const [abierto, setAbierto] = useState(false);
  const [archivo, setArchivo] = useState<File | null>(null);
  const [listId, setListId] = useState("");
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [resultado, setResultado] = useState<Record<string, unknown> | null>(null);
  const { data: listas } = useQuery({
    queryKey: ["listas"],
    queryFn: () => api.get<ContactList[]>("/api/v1/lists"),
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

  const importar = useMutation({
    mutationFn: async () => {
      const fd = new FormData();
      fd.append("archivo", archivo!);
      fd.append("consent_source", "importacion_csv");
      fd.append("list_ids", listId && listId !== "sin" ? JSON.stringify([listId]) : "[]");
      fd.append("dry_run", "false");
      return api.postForm<Record<string, unknown>>("/api/v1/contacts/import", fd);
    },
    onSuccess: (res) => {
      setResultado(res);
      toast.success(`Importados: ${res.creados} nuevos, ${res.actualizados} existentes`);
      onImportado();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const reiniciar = () => {
    setArchivo(null); setPreview(null); setResultado(null);
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
            Los duplicados se detectan y descartan automáticamente.
          </DialogDescription>
        </DialogHeader>

        {!resultado ? (
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
                disabled={!archivo || !preview || importar.isPending}
                onClick={() => importar.mutate()}
              >
                {importar.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Importar {preview ? `(${preview.filas_validas})` : ""}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2 text-sm">
              {[
                ["Correos importados", Number(resultado.creados) || 0],
                ["Ya existían", Number(resultado.actualizados) || 0],
                ["Duplicados descartados", Number(resultado.duplicados_en_archivo) || 0],
                ["Inválidos", Number(resultado.filas_invalidas) || 0],
              ].map(([k, v]) => (
                <div key={String(k)} className="rounded-lg bg-muted/60 p-2.5">
                  <p className="text-muted-foreground text-xs">{k}</p>
                  <p className="font-bold text-base">{String(v)}</p>
                </div>
              ))}
            </div>
            <DialogFooter>
              <Button className="bg-emerald-600 hover:bg-emerald-700" onClick={() => { setAbierto(false); reiniciar(); }}>
                Cerrar
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
