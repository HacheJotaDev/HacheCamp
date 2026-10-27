"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  FolderOpen, Plus, Loader2, Trash2, Tags, Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Skeleton } from "@/components/ui/skeleton";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { api, ContactList, Segment } from "@/lib/api";
import { EmptyState, fecha } from "@/components/shared";

export default function ListasPage() {
  const qc = useQueryClient();
  const [nombre, setNombre] = useState("");
  const [descripcion, setDescripcion] = useState("");
  const [segNombre, setSegNombre] = useState("");
  const [segCampo, setSegCampo] = useState("email");
  const [segOp, setSegOp] = useState("contiene");
  const [segValor, setSegValor] = useState("");

  const { data: listas, isLoading } = useQuery({
    queryKey: ["listas"],
    queryFn: () => api.get<ContactList[]>("/api/v1/lists"),
  });
  const { data: segmentos } = useQuery({
    queryKey: ["segmentos"],
    queryFn: () => api.get<Segment[]>("/api/v1/segments"),
  });

  const crearLista = useMutation({
    mutationFn: () => api.post("/api/v1/lists", { name: nombre.trim(), description: descripcion.trim() || null }),
    onSuccess: () => {
      toast.success("Lista creada");
      setNombre(""); setDescripcion("");
      qc.invalidateQueries({ queryKey: ["listas"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const borrarLista = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/lists/${id}`),
    onSuccess: () => {
      toast.success("Lista eliminada (los contactos no se borran)");
      qc.invalidateQueries({ queryKey: ["listas"] });
    },
    onError: (e: Error) => toast.error(e.message),
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

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Listas y segmentos</h1>
        <p className="text-sm text-muted-foreground">
          Organiza tus destinatarios. Eliminar una lista no borra los contactos.
        </p>
      </div>

      <Tabs defaultValue="listas">
        <TabsList>
          <TabsTrigger value="listas" className="gap-1.5"><FolderOpen className="h-4 w-4" /> Listas</TabsTrigger>
          <TabsTrigger value="segmentos" className="gap-1.5"><Tags className="h-4 w-4" /> Segmentos</TabsTrigger>
        </TabsList>

        <TabsContent value="listas" className="space-y-4 mt-2">
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

          {isLoading ? (
            <div className="space-y-2">{Array.from({ length: 3 }).map((_, i) => <Skeleton key={i} className="h-14" />)}</div>
          ) : !listas || listas.length === 0 ? (
            <Card><CardContent>
              <EmptyState icon={FolderOpen} titulo="Sin listas todavía"
                descripcion="Crea una lista para agrupar contactos y seleccionarla en tus campañas." />
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
                      <p className="font-medium truncate">{l.name}</p>
                      <p className="text-xs text-muted-foreground truncate">
                        {l.description || "Sin descripción"} · creada {fecha(l.created_at)}
                      </p>
                    </div>
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button size="icon" variant="ghost" aria-label={`Eliminar ${l.name}`}
                          className="text-red-600 hover:text-red-700 shrink-0">
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>¿Eliminar la lista «{l.name}»?</AlertDialogTitle>
                          <AlertDialogDescription>
                            Los contactos no se eliminan: solo se desvinculan de esta lista.
                            Las campañas que la usen dejarán de tener destinatarios asignados.
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancelar</AlertDialogCancel>
                          <AlertDialogAction className="bg-red-600 hover:bg-red-700"
                            onClick={() => borrarLista.mutate(l.id)}>
                            Eliminar lista
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
    </div>
  );
}
