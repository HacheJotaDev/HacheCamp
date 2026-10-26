"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { Ban, Loader2, Plus, Trash2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Skeleton } from "@/components/ui/skeleton";
import { api, Suppression } from "@/lib/api";
import { EmptyState, fecha } from "@/components/shared";

const MOTIVOS: Record<string, string> = {
  rebote_permanente: "Rebote permanente",
  queja: "Queja de spam",
  baja: "Baja",
  manual: "Manual",
};

export default function SupresionPage() {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [motivo, setMotivo] = useState("todos");
  const [nuevaAbierta, setNuevaAbierta] = useState(false);
  const [form, setForm] = useState({ email: "", reason: "manual", detail: "" });

  const filtros = new URLSearchParams({ per_page: "50" });
  if (q) filtros.set("q", q);
  if (motivo !== "todos") filtros.set("motivo", motivo);

  const { data, isLoading } = useQuery({
    queryKey: ["supresion", filtros.toString()],
    queryFn: () => api.get<{ total: number; por_motivo: Record<string, number>; items: Suppression[] }>(`/api/v1/suppression?${filtros}`),
  });

  const agregar = useMutation({
    mutationFn: () => api.post("/api/v1/suppression", {
      email: form.email, reason: form.reason, detail: form.detail || null,
    }),
    onSuccess: () => {
      toast.success("Dirección suprimida: no recibirá más campañas");
      setNuevaAbierta(false);
      setForm({ email: "", reason: "manual", detail: "" });
      qc.invalidateQueries({ queryKey: ["supresion"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const quitar = useMutation({
    mutationFn: (email: string) => api.delete(`/api/v1/suppression/${encodeURIComponent(email)}`),
    onSuccess: () => {
      toast.success("Supresión eliminada");
      qc.invalidateQueries({ queryKey: ["supresion"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Lista de supresión global</h1>
          <p className="text-sm text-muted-foreground">
            Nunca se envía a estas direcciones, aunque pertenezcan a una lista válida
          </p>
        </div>
        <Button onClick={() => setNuevaAbierta(true)} className="bg-emerald-600 hover:bg-emerald-700">
          <Plus className="mr-2 h-4 w-4" /> Añadir supresión
        </Button>
      </div>

      {data?.por_motivo && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          {Object.entries(MOTIVOS).map(([k, l]) => (
            <Card key={k}><CardContent className="p-3 text-center">
              <p className="text-xl font-bold tabular-nums">{data.por_motivo[k] || 0}</p>
              <p className="text-xs text-muted-foreground">{l}</p>
            </CardContent></Card>
          ))}
        </div>
      )}

      <Card>
        <CardContent className="p-4 space-y-3">
          <div className="flex flex-col sm:flex-row gap-2">
            <Input placeholder="Buscar correo…" value={q} onChange={(e) => setQ(e.target.value)} />
            <Select value={motivo} onValueChange={setMotivo}>
              <SelectTrigger className="w-full sm:w-48"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="todos">Todos los motivos</SelectItem>
                {Object.entries(MOTIVOS).map(([k, l]) => <SelectItem key={k} value={k}>{l}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>

          {isLoading ? (
            <div className="space-y-2">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
          ) : !data || data.items.length === 0 ? (
            <EmptyState icon={Ban} titulo="Supresión vacía"
              descripcion="Las bajas, rebotes permanentes y quejas se añaden aquí automáticamente. También puedes añadir direcciones manualmente." />
          ) : (
            <div className="overflow-x-auto -mx-4 px-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Correo</TableHead>
                    <TableHead>Motivo</TableHead>
                    <TableHead className="hidden md:table-cell">Origen</TableHead>
                    <TableHead className="hidden md:table-cell">Desde</TableHead>
                    <TableHead className="text-right">Acciones</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((s) => (
                    <TableRow key={s.id}>
                      <TableCell className="font-medium">{s.email}</TableCell>
                      <TableCell><span className="text-sm">{MOTIVOS[s.reason] || s.reason}</span></TableCell>
                      <TableCell className="hidden md:table-cell text-sm text-muted-foreground">{s.source || "—"}</TableCell>
                      <TableCell className="hidden md:table-cell text-sm text-muted-foreground">{fecha(s.created_at)}</TableCell>
                      <TableCell className="text-right">
                        <Button variant="ghost" size="icon" aria-label="Quitar supresión" className="text-red-600"
                          onClick={() => { if (confirm(`¿Quitar ${s.email} de la supresión? Podría volver a recibir correos.`)) quitar.mutate(s.email); }}>
                          <Trash2 className="h-4 w-4" />
                        </Button>
                      </TableCell>
                    </TableRow>
                  ))}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <Dialog open={nuevaAbierta} onOpenChange={setNuevaAbierta}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Añadir a supresión</DialogTitle>
            <DialogDescription>
              La dirección quedará bloqueada para todo envío. Úsala con criterio: los rebotes y
              quejas se añaden solos.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>Correo *</Label>
              <Input type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>Motivo</Label>
              <Select value={form.reason} onValueChange={(v) => setForm({ ...form, reason: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  {Object.entries(MOTIVOS).map(([k, l]) => <SelectItem key={k} value={k}>{l}</SelectItem>)}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Detalle</Label>
              <Input value={form.detail} onChange={(e) => setForm({ ...form, detail: e.target.value })} />
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setNuevaAbierta(false)}>Cancelar</Button>
            <Button className="bg-emerald-600 hover:bg-emerald-700"
              disabled={!form.email.includes("@") || agregar.isPending} onClick={() => agregar.mutate()}>
              {agregar.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Suprimir
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}
