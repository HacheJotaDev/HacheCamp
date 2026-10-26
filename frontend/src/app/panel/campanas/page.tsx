"use client";

import { useState } from "react";
import Link from "next/link";
import { useQuery } from "@tanstack/react-query";
import { Mail, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { Progress } from "@/components/ui/progress";
import { api, Campaign, Paged } from "@/lib/api";
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

  const { data, isLoading } = useQuery({
    queryKey: ["campanas", estado],
    queryFn: () => api.get<Paged<Campaign>>(`/api/v1/campaigns?per_page=50${estado !== "todos" ? `&estado=${estado}` : ""}`),
    refetchInterval: 15_000,
  });

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Campañas</h1>
          <p className="text-sm text-muted-foreground">
            Creación, aprobación, programación y seguimiento en tiempo real
          </p>
        </div>
        <Button asChild className="bg-emerald-600 hover:bg-emerald-700">
          <Link href="/panel/campanas/nueva"><Plus className="mr-2 h-4 w-4" /> Nueva campaña</Link>
        </Button>
      </div>

      <Card>
        <CardContent className="p-4 space-y-4">
          <Select value={estado} onValueChange={setEstado}>
            <SelectTrigger className="w-full sm:w-52"><SelectValue /></SelectTrigger>
            <SelectContent>
              {ESTADOS.map((e) => <SelectItem key={e.v} value={e.v}>{e.l}</SelectItem>)}
            </SelectContent>
          </Select>

          {isLoading ? (
            <div className="space-y-2">{Array.from({ length: 5 }).map((_, i) => <Skeleton key={i} className="h-14" />)}</div>
          ) : !data || data.items.length === 0 ? (
            <EmptyState icon={Mail} titulo="Sin campañas"
              descripcion="Crea tu primera campaña a partir de una plantilla, selecciona destinatarios con consentimiento y envíala tras su aprobación."
              accion={<Button asChild variant="outline" className="mt-1"><Link href="/panel/campanas/nueva">Crear campaña</Link></Button>} />
          ) : (
            <div className="overflow-x-auto -mx-4 px-4">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Campaña</TableHead>
                    <TableHead>Estado</TableHead>
                    <TableHead className="hidden md:table-cell">Progreso</TableHead>
                    <TableHead className="hidden lg:table-cell">Programada</TableHead>
                    <TableHead className="text-right">Creada</TableHead>
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
                        <TableCell><StatusBadge estado={c.status} /></TableCell>
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
                        <TableCell className="text-right text-sm text-muted-foreground">{fecha(c.created_at)}</TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
}
