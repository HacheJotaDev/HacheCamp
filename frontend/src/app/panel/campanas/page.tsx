"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Archive, ArchiveRestore, Mail, MoreHorizontal, Plus, Search, Trash2, Undo2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";
import { Input } from "@/components/ui/input";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";
import { api, Campaign, Paged } from "@/lib/api";
import { useAuth } from "@/lib/providers";
import { EmptyState, StatusBadge, fecha, numero } from "@/components/shared";

const ESTADOS = [
  { v: "todos", l: "Todos los estados" },
  { v: "borrador", l: "Borrador" },
  { v: "en_revision", l: "En revisión" },
  { v: "programada", l: "Programada" },
  { v: "en_cola", l: "En cola" },
  { v: "enviando", l: "Enviando" },
  { v: "pausada", l: "Pausada" },
  { v: "completada", l: "Completada" },
  { v: "cancelada", l: "Cancelada" },
  { v: "fallida", l: "Fallida" },
];

export default function CampanasPage() {
  const [estado, setEstado] = useState("todos");
  const [q, setQ] = useState("");
  const [papelera, setPapelera] = useState(false);
  const [aEliminar, setAEliminar] = useState<Campaign | null>(null);
  const qc = useQueryClient();
  const { user } = useAuth();
  const esAdmin = user?.role === "admin";

  const { data, isLoading } = useQuery({
    queryKey: ["campanas", estado, q, papelera],
    queryFn: () => {
      const params = new URLSearchParams({ per_page: "50" });
      if (estado !== "todos") params.set("estado", estado);
      if (q.trim()) params.set("q", q.trim());
      if (papelera) params.set("papelera", "true");
      return api.get<Paged<Campaign>>(`/api/v1/campaigns?${params.toString()}`);
    },
    refetchInterval: 15_000,
  });

  const invalidar = () => {
    qc.invalidateQueries({ queryKey: ["campanas"] });
    qc.invalidateQueries({ queryKey: ["campanas-papelera"] });
  };

  const archivar = useMutation({
    mutationFn: (c: Campaign) =>
      api.post(`/api/v1/campaigns/${c.id}/${c.archived_at ? "unarchive" : "archive"}`),
    onSuccess: (_d, c) => {
      toast.success(c.archived_at ? "Campaña desarchivada" : "Campaña archivada");
      invalidar();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const eliminar = useMutation({
    mutationFn: (c: Campaign) =>
      api.delete<{ estado: string; mensajes_cancelados: number }>(
        `/api/v1/campaigns/${c.id}?confirmar=true`),
    onSuccess: (d) => {
      toast.success(
        d.mensajes_cancelados > 0
          ? `Campaña en la papelera (${d.mensajes_cancelados} envíos pendientes cancelados)`
          : "Campaña en la papelera");
      invalidar();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const restaurar = useMutation({
    mutationFn: (c: Campaign) => api.post(`/api/v1/campaigns/${c.id}/restore`),
    onSuccess: () => {
      toast.success("Campaña restaurada");
      invalidar();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">
            Campañas {papelera && <span className="text-muted-foreground">· Papelera</span>}
          </h1>
          <p className="text-sm text-muted-foreground">
            Creación, aprobación, programación y seguimiento en tiempo real
          </p>
        </div>
        {!papelera && (
          <Button asChild className="bg-emerald-600 hover:bg-emerald-700">
            <Link href="/panel/campanas/nueva"><Plus className="mr-2 h-4 w-4" /> Nueva campaña</Link>
          </Button>
        )}
      </div>

      <Card>
        <CardContent className="p-4 space-y-4">
          <div className="flex gap-2 flex-wrap">
            <div className="relative flex-1 min-w-[200px]">
              <Search className="absolute left-2.5 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                value={q}
                onChange={(e) => setQ(e.target.value)}
                placeholder="Buscar por nombre o asunto…"
                className="pl-8"
              />
            </div>
            <Select value={estado} onValueChange={setEstado}>
              <SelectTrigger className="w-full sm:w-52"><SelectValue /></SelectTrigger>
              <SelectContent>
                {ESTADOS.map((e) => <SelectItem key={e.v} value={e.v}>{e.l}</SelectItem>)}
              </SelectContent>
            </Select>
            <Button
              variant={papelera ? "default" : "outline"}
              onClick={() => setPapelera(!papelera)}
              title="Papelera de campañas"
            >
              <Trash2 className="mr-2 h-4 w-4" /> Papelera
            </Button>
          </div>

          {isLoading ? (
            <div className="space-y-2">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-14" />)}</div>
          ) : !data || data.items.length === 0 ? (
            papelera ? (
              <EmptyState icon={Trash2} titulo="Papelera vacía"
                descripcion="Las campañas eliminadas aparecen aquí y pueden restaurarse con todo su historial." />
            ) : (
              <EmptyState icon={Mail} titulo="Sin campañas"
                descripcion="Crea tu primera campaña a partir de una plantilla, selecciona destinatarios con consentimiento y envíala tras su aprobación."
                accion={<Button asChild variant="outline" className="mt-1"><Link href="/panel/campanas/nueva">Crear campaña</Link></Button>} />
            )
          ) : (
            <div className="overflow-x-auto -mx-4 px-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Campaña</TableHead>
                    <TableHead>Estado</TableHead>
                    <TableHead className="hidden md:table-cell">Progreso</TableHead>
                    <TableHead className="hidden lg:table-cell">Programada</TableHead>
                    <TableHead className="hidden sm:table-cell text-right">Creada</TableHead>
                    <TableHead className="w-10" />
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {data.items.map((c) => {
                    const pct = c.progreso && c.progreso.total > 0
                      ? (c.progreso.procesados / c.progreso.total) * 100 : 0;
                    return (
                      <TableRow key={c.id}>
                        <TableCell>
                          <Link href={`/panel/campanas/${c.id}`} className="font-medium hover:text-emerald-600 hover:underline">
                            {c.name}
                          </Link>
                          <p className="text-xs text-muted-foreground truncate max-w-[260px]">{c.subject}</p>
                        </TableCell>
                        <TableCell>
                          <div className="flex items-center gap-1.5 flex-wrap">
                            <StatusBadge estado={c.status} />
                            {c.archived_at && !c.deleted_at && <Badge variant="outline">Archivada</Badge>}
                          </div>
                        </TableCell>
                        <TableCell className="hidden md:table-cell w-36">
                          {c.progreso && c.progreso.total > 0 ? (
                            <div>
                              <p className="text-xs tabular-nums mb-1">{numero(c.progreso.procesados)}/{numero(c.progreso.total)}</p>
                              <Progress value={pct} className="h-1.5" />
                            </div>
                          ) : "—"}
                        </TableCell>
                        <TableCell className="hidden lg:table-cell text-sm text-muted-foreground">
                          {c.scheduled_at ? fecha(c.scheduled_at) : "—"}
                        </TableCell>
                        <TableCell className="hidden sm:table-cell text-right text-sm text-muted-foreground">{fecha(c.created_at)}</TableCell>
                        <TableCell className="text-right">
                          {papelera ? (
                            esAdmin ? (
                              <Button size="icon" variant="ghost" title="Restaurar campaña"
                                onClick={() => restaurar.mutate(c)}>
                                <Undo2 className="h-4 w-4" />
                              </Button>
                            ) : null
                          ) : (
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button size="icon" variant="ghost" aria-label="Acciones de campaña">
                                  <MoreHorizontal className="h-4 w-4" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end">
                                <DropdownMenuItem asChild>
                                  <Link href={`/panel/campanas/${c.id}`}>Abrir</Link>
                                </DropdownMenuItem>
                                <DropdownMenuItem onClick={() => duplicar(c)}>
                                  Duplicar
                                </DropdownMenuItem>
                                <DropdownMenuItem onClick={() => archivar.mutate(c)}>
                                  {c.archived_at
                                    ? <><ArchiveRestore className="mr-2 h-4 w-4" /> Desarchivar</>
                                    : <><Archive className="mr-2 h-4 w-4" /> Archivar</>}
                                </DropdownMenuItem>
                                {esAdmin && (
                                  <>
                                    <DropdownMenuSeparator />
                                    <DropdownMenuItem
                                      className="text-red-600 focus:text-red-600"
                                      onClick={() => setAEliminar(c)}
                                    >
                                      <Trash2 className="mr-2 h-4 w-4" /> Eliminar
                                    </DropdownMenuItem>
                                  </>
                                )}
                              </DropdownMenuContent>
                            </DropdownMenu>
                          )}
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>

      <AlertDialog open={!!aEliminar} onOpenChange={(v) => !v && setAEliminar(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>¿Eliminar «{aEliminar?.name}»?</AlertDialogTitle>
            <AlertDialogDescription>
              La campaña pasará a la <b>papelera</b> (borrado lógico): su historial,
              mensajes y eventos SES se conservan y un administrador podrá restaurarla.
              {aEliminar && ["en_cola", "enviando", "pausada", "programada"].includes(aEliminar.status) && (
                <> Además se <b>cancelará</b>: los envíos pendientes se detienen y
                ningún mensaje aceptado por SES se reenvía.</>
              )}
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel>Cancelar</AlertDialogCancel>
            <AlertDialogAction
              className="bg-red-600 hover:bg-red-700"
              onClick={() => aEliminar && eliminar.mutate(aEliminar)}
            >
              Eliminar
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>
    </div>
  );

  function duplicar(c: Campaign) {
    api.post(`/api/v1/campaigns/${c.id}/duplicate`)
      .then(() => { toast.success("Campaña duplicada"); invalidar(); })
      .catch((e: Error) => toast.error(e.message));
  }
}
