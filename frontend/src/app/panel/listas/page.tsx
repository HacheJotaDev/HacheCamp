"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Eraser, FolderOpen, Loader2, Pencil, Plus, Search, Trash2, Tags, Undo2, Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import {
  Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle,
} from "@/components/ui/dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { api, ContactList, Segment } from "@/lib/api";
import { useAuth } from "@/lib/providers";
import { EmptyState, fecha, numero } from "@/components/shared";

export default function ListasPage() {
  const qc = useQueryClient();
  const { user } = useAuth();
  const esAdmin = user?.role === "admin";
  const [nombre, setNombre] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [q, setQ] = useState("");
  const [papelera, setPapelera] = useState(false);
  const [editando, setEditando] = useState<ContactList | null>(null);
  const [editNombre, setEditNombre] = useState("");
  const [editDesc, setEditDesc] = useState("");
  const [aVaciar, setAVaciar] = useState<ContactList | null>(null);
  // Conflicto de nombre al restaurar: guarda la lista en espera y el nombre
  // propuesto para el diálogo de renombrado (el nombre es UNIQUE en BD).
  const [conflictoRestaurar, setConflictoRestaurar] = useState<
    { id: string; nombre: string; error?: string } | null
  >(null);
  const [segNombre, setSegNombre] = useState("");
  const [segCampo, setSegCampo] = useState("email");
  const [segOp, setSegOp] = useState("contiene");
  const [segValor, setSegValor] = useState("");

  const { data: listas, isLoading } = useQuery({
    queryKey: ["listas", q, papelera],
    queryFn: () => api.get<ContactList[]>(
      `/api/v1/lists${papelera ? "/papelera" : ""}${q.trim() ? `?q=${encodeURIComponent(q.trim())}` : ""}`),
  });
  const { data: segmentos } = useQuery({
    queryKey: ["segmentos"],
    queryFn: () => api.get<Segment[]>("/api/v1/segments"),
  });

  const invalidar = () => qc.invalidateQueries({ queryKey: ["listas"] });

  const crearLista = useMutation({
    mutationFn: () => api.post("/api/v1/lists", { name: nombre.trim(), description: descripcion.trim() || null }),
    onSuccess: () => {
      toast.success("Lista creada");
      setNombre(""); setDescripcion("");
      invalidar();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const guardarEdicion = useMutation({
    mutationFn: () => api.patch(`/api/v1/lists/${editando?.id}`, {
      name: editNombre.trim(), description: editDesc.trim() || null,
    }),
    onSuccess: () => {
      toast.success("Lista actualizada");
      setEditando(null);
      invalidar();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const vaciarLista = useMutation({
    mutationFn: (l: ContactList) => api.post<{ asociaciones_eliminadas: number }>(
      `/api/v1/lists/${l.id}/empty?confirmar=true`),
    onSuccess: (d) => {
      toast.success(`Lista vaciada: ${numero(d.asociaciones_eliminadas)} asociaciones eliminadas (contactos intactos)`);
      setAVaciar(null);
      invalidar();
      qc.invalidateQueries({ queryKey: ["contactos"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const borrarLista = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/lists/${id}?confirmar=true`),
    onSuccess: () => {
      toast.success("Lista en la papelera (los contactos no se borran)");
      invalidar();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  // Restauración desde la papelera. Si otra lista tomó el nombre mientras
  // estaba eliminada, el API responde 409 y aquí se pide un nombre nuevo
  // (restauración renombrada; la lista y sus contactos nunca se pierden).
  const restaurarLista = useMutation({
    mutationFn: ({ id, nuevoNombre }: { id: string; nuevoNombre?: string }) =>
      api.post<{ nombre: string }>(`/api/v1/lists/${id}/restore`,
        nuevoNombre ? { nuevo_nombre: nuevoNombre } : undefined),
    onSuccess: (d) => {
      toast.success(d?.nombre && d.nombre !== "" ? `Lista restaurada como «${d.nombre}»` : "Lista restaurada");
      setConflictoRestaurar(null);
      invalidar();
    },
    onError: (e: Error) => {
      // 409 por colisión: se abre el diálogo de renombrado (sin toast)
      if (e.message.includes("Indica un nombre nuevo") || e.message.includes("Ya existe una lista")) {
        setConflictoRestaurar((prev) => (prev ? { ...prev, error: e.message } : prev));
        return;
      }
      toast.error(e.message);
    },
  });

  const crearSegmento = useMutation({
    mutationFn: () => api.post("/api/v1/segments", {
      name: segNombre.trim(),
      rules: [{ campo: segCampo, op: segOp, valor: segValor.trim() }],
    }),
    onSuccess: () => {
      toast.success("Segmento creado");
      setSegNombre(""); setSegValor("");
      qc.invalidateQueries({ queryKey: ["segmentos"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const borrarSegmento = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/segments/${id}`),
    onSuccess: () => {
      toast.success("Segmento eliminado");
      qc.invalidateQueries({ queryKey: ["segmentos"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  function empezarEdicion(l: ContactList) {
    setEditando(l);
    setEditNombre(l.name);
    setEditDesc(l.description || "");
  }

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Listas y segmentos</h1>
        <p className="text-sm text-muted-foreground">
          Organiza tus destinatarios. Eliminar una lista no borra los contactos;
          vaciarla solo desvincula.
        </p>
      </div>

      <Tabs defaultValue="listas">
        <TabsList>
          <TabsTrigger value="listas" className="gap-1.5"><FolderOpen className="h-4 w-4" /> Listas</TabsTrigger>
          <TabsTrigger value="segmentos" className="gap-1.5"><Tags className="h-4 w-4" /> Segmentos</TabsTrigger>
        </TabsList>

        <TabsContent value="listas" className="space-y-4 mt-2">
          {!editando && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">Nueva lista</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid sm:grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="l-nombre">Nombre *</Label>
                    <Input id="l-nombre" value={nombre} onChange={(e) => setNombre(e.target.value)}
                      placeholder="p. ej. Clientes activos" />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="l-desc">Descripción</Label>
                    <Input id="l-desc" value={descripcion} onChange={(e) => setDescripcion(e.target.value)}
                      placeholder="Opcional" />
                  </div>
                </div>
                <Button className="bg-emerald-600 hover:bg-emerald-700"
                  disabled={nombre.trim().length < 2 || crearLista.isPending}
                  onClick={() => crearLista.mutate()}>
                  {crearLista.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
                  Crear lista
                </Button>
              </CardContent>
            </Card>
          )}

          {editando && (
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base">Editar «{editando.name}»</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <div className="grid sm:grid-cols-2 gap-3">
                  <div className="space-y-1.5">
                    <Label htmlFor="e-nombre">Nombre *</Label>
                    <Input id="e-nombre" value={editNombre} onChange={(e) => setEditNombre(e.target.value)} />
                  </div>
                  <div className="space-y-1.5">
                    <Label htmlFor="e-desc">Descripción</Label>
                    <Input id="e-desc" value={editDesc} onChange={(e) => setEditDesc(e.target.value)} />
                  </div>
                </div>
                <div className="flex gap-2">
                  <Button className="bg-emerald-600 hover:bg-emerald-700"
                    disabled={editNombre.trim().length < 2 || guardarEdicion.isPending}
                    onClick={() => guardarEdicion.mutate()}>
                    {guardarEdicion.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                    Guardar
                  </Button>
                  <Button variant="outline" onClick={() => setEditando(null)}>Cancelar</Button>
                </div>
              </CardContent>
            </Card>
          )}

          <div className="flex gap-2 flex-wrap">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input value={q} onChange={(e) => setQ(e.target.value)}
                placeholder="Buscar listas por nombre o descripción…" className="pl-8" />
            </div>
            <Button variant={papelera ? "default" : "outline"} onClick={() => setPapelera(!papelera)}>
              <Trash2 className="mr-2 h-4 w-4" /> Papelera
            </Button>
          </div>

          {isLoading ? (
            <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-14" />)}</div>
          ) : !listas || listas.length === 0 ? (
            <Card><CardContent>
              {papelera ? (
                <EmptyState icon={Trash2} titulo="Papelera vacía"
                  descripcion="Las listas eliminadas aparecen aquí y pueden restaurarse." />
              ) : (
                <EmptyState icon={FolderOpen} titulo="Sin listas todavía"
                  descripcion="Crea una lista para agrupar contactos y seleccionarla en tus campañas." />
              )}
            </CardContent></Card>
          ) : (
            <div className="space-y-2">
              {listas.map((l) => (
                <Card key={l.id}>
                  <CardContent className="p-4 flex items-center gap-3">
                    <div className="h-9 w-9 rounded-lg bg-emerald-600/10 flex items-center justify-center shrink-0">
                      <Users className="h-4 w-4 text-emerald-600" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2 flex-wrap">
                        <p className="font-medium truncate">{l.name}</p>
                        <Badge variant="secondary" className="tabular-nums shrink-0">
                          {numero(l.contactos ?? 0)} contactos
                        </Badge>
                      </div>
                      <p className="text-xs text-muted-foreground truncate">
                        {l.description || "Sin descripción"} · creada {fecha(l.created_at)}
                      </p>
                    </div>
                    {papelera ? (
                      esAdmin && (
                        <Button size="icon" variant="ghost" title="Restaurar lista"
                          onClick={() => {
                            // Sembramos el diálogo con el nombre actual: si el
                            // API responde 409 (nombre tomado) se pide uno nuevo.
                            setConflictoRestaurar({ id: l.id, nombre: l.name });
                            restaurarLista.mutate({ id: l.id });
                          }}>
                          <Undo2 className="h-4 w-4" />
                        </Button>
                      )
                    ) : (
                      <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                          <Button size="icon" variant="ghost" aria-label={`Acciones de ${l.name}`}>
                            <Pencil className="h-4 w-4" />
                          </Button>
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end">
                          <DropdownMenuItem onClick={() => empezarEdicion(l)}>
                            <Pencil className="mr-2 h-4 w-4" /> Renombrar / descripción
                          </DropdownMenuItem>
                          <DropdownMenuItem onClick={() => setAVaciar(l)}
                            disabled={(l.contactos ?? 0) === 0}>
                            <Eraser className="mr-2 h-4 w-4" /> Vaciar lista
                          </DropdownMenuItem>
                          {esAdmin && (
                            <>
                              <DropdownMenuSeparator />
                              <DropdownMenuItem asChild>
                                <button
                                  type="button"
                                  className="w-full text-red-600 focus:text-red-600 cursor-pointer"
                                  onClick={(e) => {
                                    e.preventDefault();
                                    borrarLista.mutate(l.id);
                                  }}
                                >
                                  <Trash2 className="mr-2 h-4 w-4" /> Eliminar lista
                                </button>
                              </DropdownMenuItem>
                            </>
                          )}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    )}
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>

        <TabsContent value="segmentos" className="space-y-4 mt-2">
          <Card>
            <CardHeader className="pb-2">
              <CardTitle className="text-base">Nuevo segmento</CardTitle>
            </CardHeader>
            <CardContent className="space-y-3">
              <div className="grid sm:grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label htmlFor="s-nombre">Nombre *</Label>
                  <Input id="s-nombre" value={segNombre} onChange={(e) => setSegNombre(e.target.value)}
                    placeholder="p. ej. Correos Gmail" />
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="s-campo">Campo</Label>
                  <select id="s-campo" value={segCampo} onChange={(e) => setSegCampo(e.target.value)}
                    className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm">
                    <option value="email">Correo</option>
                  </select>
                </div>
              </div>
              <div className="grid sm:grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label>Condición</Label>
                  <select value={segOp} onChange={(e) => setSegOp(e.target.value)}
                    className="flex h-9 w-full rounded-md border border-input bg-background px-3 py-1 text-sm">
                    <option value="contiene">Contiene</option>
                    <option value="empieza">Empieza por</option>
                    <option value="igual">Igual a</option>
                  </select>
                </div>
                <div className="space-y-1.5">
                  <Label htmlFor="s-valor">Valor</Label>
                  <Input id="s-valor" value={segValor} onChange={(e) => setSegValor(e.target.value)}
                    placeholder="p. ej. gmail.com" />
                </div>
              </div>
              <Button className="bg-emerald-600 hover:bg-emerald-700"
                disabled={segNombre.trim().length < 2 || segValor.trim().length < 1 || crearSegmento.isPending}
                onClick={() => crearSegmento.mutate()}>
                {crearSegmento.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Plus className="mr-2 h-4 w-4" />}
                Crear segmento
              </Button>
            </CardContent>
          </Card>

          {!segmentos || segmentos.length === 0 ? (
            <Card><CardContent>
              <EmptyState icon={Tags} titulo="Sin segmentos todavía"
                descripcion="Un segmento filtra contactos por reglas simples (p. ej. correos que contienen gmail.com)." />
            </CardContent></Card>
          ) : (
            <div className="space-y-2">
              {segmentos.map((s) => (
                <Card key={s.id}>
                  <CardContent className="p-4 flex items-center gap-3">
                    <div className="h-9 w-9 rounded-lg bg-sky-600/10 flex items-center justify-center shrink-0">
                      <Tags className="h-4 w-4 text-sky-600" />
                    </div>
                    <div className="min-w-0 flex-1">
                      <p className="font-medium truncate">{s.name}</p>
                      <p className="text-xs text-muted-foreground truncate">
                        {s.rules.map((r) => `${r.campo} ${r.op} «${r.valor}»`).join(" · ")}
                      </p>
                    </div>
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button size="icon" variant="ghost" aria-label={`Eliminar ${s.name}`}
                          className="text-red-600 hover:text-red-700 shrink-0">
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>¿Eliminar el segmento «{s.name}»?</AlertDialogTitle>
                          <AlertDialogDescription>Los contactos no se ven afectados.</AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancelar</AlertDialogCancel>
                          <AlertDialogAction className="bg-red-600 hover:bg-red-700"
                            onClick={() => borrarSegmento.mutate(s.id)}>
                            Eliminar
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
                  </CardContent>
                </Card>
              ))}
            </div>
          )}
        </TabsContent>
      </Tabs>

      <AlertDialog open={!!aVaciar} onOpenChange={(v) => !v && setAVaciar(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>¿Vaciar la lista «{aVaciar?.name}»?</AlertDialogTitle>
            <AlertDialogDescription>
              Se eliminarán las <b>{numero(aVaciar?.contactos ?? 0)} asociaciones</b> de esta lista.
              Los contactos <b>no se borran</b> de la base global y siguen en sus otras listas.
              El número de contactos de la lista quedará en cero.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction className="bg-amber-600 hover:bg-amber-700"
              onClick={() => aVaciar && vaciarLista.mutate(aVaciar)}>
              Vaciar lista
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      {/* Conflicto de nombre al restaurar: la lista se restaura RENOMBRADA.
          Solo se abre con error (409): un clic sin colisión no muestra nada. */}
      <Dialog open={!!conflictoRestaurar?.error}
        onOpenChange={(v) => !v && setConflictoRestaurar(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Nombre en uso: elige otro para restaurar</DialogTitle>
            <DialogDescription>
              {conflictoRestaurar?.error ||
                `Otra lista ya usa el nombre «${conflictoRestaurar?.nombre}». Indica un nombre distinto: la lista se restaurará renombrada, sin perder sus contactos.`}
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-1.5">
            <Label htmlFor="r-nuevo-nombre">Nuevo nombre</Label>
            <Input id="r-nuevo-nombre" autoFocus
              value={conflictoRestaurar?.nombre ?? ""}
              onChange={(e) => conflictoRestaurar &&
                setConflictoRestaurar({ ...conflictoRestaurar, nombre: e.target.value, error: undefined })}
              onKeyDown={(e) => {
                if (e.key === "Enter" && conflictoRestaurar && conflictoRestaurar.nombre.trim().length >= 2) {
                  restaurarLista.mutate({
                    id: conflictoRestaurar.id, nuevoNombre: conflictoRestaurar.nombre.trim(),
                  });
                }
              }}
              placeholder="p. ej. Clientes activos (restaurada)" />
          </div>
          <DialogFooter className="gap-2">
            <Button variant="outline" onClick={() => setConflictoRestaurar(null)}>Cancelar</Button>
            <Button className="bg-emerald-600 hover:bg-emerald-700"
              disabled={!conflictoRestaurar || conflictoRestaurar.nombre.trim().length < 2 || restaurarLista.isPending}
              onClick={() => conflictoRestaurar && restaurarLista.mutate({
                id: conflictoRestaurar.id, nuevoNombre: conflictoRestaurar.nombre.trim(),
              })}>
              {restaurarLista.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Undo2 className="mr-2 h-4 w-4" />}
              Restaurar con este nombre
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
