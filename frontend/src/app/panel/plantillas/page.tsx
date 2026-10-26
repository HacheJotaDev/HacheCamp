"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { FileText, Plus, Loader2, Copy, Trash2, Pencil } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { api, Template } from "@/lib/api";
import { EmptyState, fecha } from "@/components/shared";

export default function PlantillasPage() {
  const qc = useQueryClient();
  const [nuevaAbierta, setNuevaAbierta] = useState(false);
  const [nombre, setNombre] = useState("");
  const [categoria, setCategoria] = useState("newsletter");

  const { data: plantillas, isLoading } = useQuery({
    queryKey: ["plantillas"],
    queryFn: () => api.get<Template[]>("/api/v1/templates"),
  });

  const crear = useMutation({
    mutationFn: () => api.post<Template>("/api/v1/templates", {
      name: nombre, category: categoria, mode: "bloques",
      blocks: [
        { type: "encabezado", props: { text: "Tu encabezado", style: "font-size:20px;font-weight:bold;color:#111827;" } },
        { type: "texto", props: { content: "<p>Escribe aquí tu mensaje.</p>" } },
        { type: "pie", props: { text: "Recibes este correo por tu suscripción.", company: "HJ Campaigns" } },
      ],
    }),
    onSuccess: (t) => {
      toast.success("Plantilla creada");
      setNuevaAbierta(false);
      setNombre("");
      window.location.href = `/panel/plantillas/${t.id}`;
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const duplicar = useMutation({
    mutationFn: (id: string) => api.post<Template>(`/api/v1/templates/${id}/duplicate`),
    onSuccess: () => { toast.success("Plantilla duplicada"); qc.invalidateQueries({ queryKey: ["plantillas"] }); },
    onError: (e: Error) => toast.error(e.message),
  });

  const eliminar = useMutation({
    mutationFn: (id: string) => api.delete(`/api/v1/templates/${id}`),
    onSuccess: () => { toast.success("Plantilla eliminada"); qc.invalidateQueries({ queryKey: ["plantillas"] }); },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Plantillas</h1>
          <p className="text-sm text-muted-foreground">
            Editor visual con bloques, HTML fuente, versiones y vista previa
          </p>
        </div>
        <Button onClick={() => setNuevaAbierta(true)} className="bg-emerald-600 hover:bg-emerald-700">
          <Plus className="mr-2 h-4 w-4" /> Nueva plantilla
        </Button>
      </div>

      {isLoading ? (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-36" />)}
        </div>
      ) : !plantillas || plantillas.length === 0 ? (
        <EmptyState icon={FileText} titulo="Sin plantillas"
          descripcion="Crea tu primera plantilla con el editor visual de bloques."
          accion={<Button className="mt-1 bg-emerald-600 hover:bg-emerald-700" onClick={() => setNuevaAbierta(true)}>Crear plantilla</Button>} />
      ) : (
        <div className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {plantillas.map((t) => (
            <Card key={t.id} className="group relative overflow-hidden hover:border-emerald-600/40 transition-colors">
              <CardContent className="p-4">
                <div className="flex items-start justify-between gap-2">
                  <div className="min-w-0">
                    <Link href={`/panel/plantillas/${t.id}`} className="font-semibold hover:text-emerald-600 hover:underline block truncate">
                      {t.name}
                    </Link>
                    <p className="text-xs text-muted-foreground line-clamp-2 mt-0.5 min-h-[2rem]">
                      {t.description || "Sin descripción"}
                    </p>
                  </div>
                  {t.is_system && <Badge variant="outline" className="text-[10px] shrink-0">Sistema</Badge>}
                </div>
                <div className="flex items-center justify-between mt-3">
                  <div className="flex items-center gap-1.5">
                    <Badge variant="secondary" className="text-[10px]">{t.category}</Badge>
                    <Badge variant="outline" className="text-[10px]">{t.mode === "bloques" ? "Bloques" : "HTML"}</Badge>
                    <Badge variant="outline" className="text-[10px]">v{t.version}</Badge>
                  </div>
                  <div className="flex items-center gap-0.5 opacity-0 group-hover:opacity-100 transition-opacity">
                    <Button variant="ghost" size="icon" asChild aria-label="Editar">
                      <Link href={`/panel/plantillas/${t.id}`}><Pencil className="h-4 w-4" /></Link>
                    </Button>
                    <Button variant="ghost" size="icon" aria-label="Duplicar"
                      onClick={() => duplicar.mutate(t.id)}>
                      <Copy className="h-4 w-4" />
                    </Button>
                    {!t.is_system && (
                      <Button variant="ghost" size="icon" aria-label="Eliminar" className="text-red-600"
                        onClick={() => { if (confirm(`¿Eliminar la plantilla "${t.name}"?`)) eliminar.mutate(t.id); }}>
                        <Trash2 className="h-4 w-4" />
                      </Button>
                    )}
                  </div>
                </div>
                <p className="text-[11px] text-muted-foreground mt-2">{fecha(t.updated_at)}</p>
              </CardContent>
            </Card>
          ))}
        </div>
      )}

      <Dialog open={nuevaAbierta} onOpenChange={setNuevaAbierta}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Nueva plantilla</DialogTitle>
            <DialogDescription>Se creará con bloques base que podrás editar al instante.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="t-nombre">Nombre *</Label>
              <Input id="t-nombre" value={nombre} onChange={(e) => setNombre(e.target.value)}
                placeholder="p. ej. Promoción de primavera" />
            </div>
            <div className="space-y-1.5">
              <Label>Categoría</Label>
              <div className="flex flex-wrap gap-1.5">
                {["newsletter", "promociones", "bienvenida", "avisos", "confirmaciones"].map((c) => (
                  <button
                    key={c} type="button"
                    className={`px-3 py-1.5 rounded-full text-xs border transition-colors min-h-[32px] ${
                      categoria === c ? "bg-emerald-600 text-white border-emerald-600" : "hover:bg-muted"}`}
                    onClick={() => setCategoria(c)}
                  >
                    {c}
                  </button>
                ))}
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setNuevaAbierta(false)}>Cancelar</Button>
            <Button className="bg-emerald-600 hover:bg-emerald-700" disabled={nombre.trim().length < 2 || crear.isPending}
              onClick={() => crear.mutate()}>
              {crear.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Crear y editar
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
