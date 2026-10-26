"use client";

import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";

export function StatusBadge({ estado }: { estado: string }) {
  const map: Record<string, { label: string; cls: string }> = {
    borrador: { label: "Borrador", cls: "bg-zinc-500/15 text-zinc-600 dark:text-zinc-300 border-zinc-500/30" },
    en_revision: { label: "En revisión", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30" },
    programada: { label: "Programada", cls: "bg-sky-500/15 text-sky-600 dark:text-sky-400 border-sky-500/30" },
    en_cola: { label: "En cola", cls: "bg-violet-500/15 text-violet-600 dark:text-violet-400 border-violet-500/30" },
    enviando: { label: "Enviando", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30 animate-pulse" },
    pausada: { label: "Pausada", cls: "bg-orange-500/15 text-orange-600 dark:text-orange-400 border-orange-500/30" },
    completada: { label: "Completada", cls: "bg-emerald-500/15 text-emerald-700 dark:text-emerald-300 border-emerald-500/40" },
    cancelada: { label: "Cancelada", cls: "bg-zinc-500/15 text-zinc-500 border-zinc-500/30" },
    fallida: { label: "Fallida", cls: "bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/30" },
    // Contactos
    suscripto: { label: "Suscripto", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30" },
    dado_baja: { label: "Dado de baja", cls: "bg-zinc-500/15 text-zinc-500 border-zinc-500/30" },
    rebote: { label: "Rebote", cls: "bg-orange-500/15 text-orange-600 dark:text-orange-400 border-orange-500/30" },
    queja: { label: "Queja", cls: "bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/30" },
    // Mensajes
    pendiente: { label: "Pendiente", cls: "bg-zinc-500/15 text-zinc-500 border-zinc-500/30" },
    aceptado: { label: "Aceptado por SES", cls: "bg-sky-500/15 text-sky-600 dark:text-sky-400 border-sky-500/30" },
    entregado: { label: "Entregado", cls: "bg-emerald-500/15 text-emerald-600 dark:text-emerald-400 border-emerald-500/30" },
    rebote_permanente: { label: "Rebote permanente", cls: "bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/30" },
    rebote_temporal: { label: "Rebote temporal", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30" },
    error: { label: "Error", cls: "bg-red-500/15 text-red-600 dark:text-red-400 border-red-500/30" },
    cancelado: { label: "Cancelado", cls: "bg-zinc-500/15 text-zinc-500 border-zinc-500/30" },
    incierto: { label: "Incierto", cls: "bg-amber-500/15 text-amber-600 dark:text-amber-400 border-amber-500/30" },
  };
  const info = map[estado] ?? { label: estado, cls: "" };
  return (
    <Badge variant="outline" className={cn("font-medium", info.cls)}>
      {info.label}
    </Badge>
  );
}

export function EmptyState({
  icon: Icon,
  titulo,
  descripcion,
  accion,
}: {
  icon: React.ComponentType<{ className?: string }>;
  titulo: string;
  descripcion: string;
  accion?: React.ReactNode;
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 py-16 text-center">
      <div className="h-14 w-14 rounded-full bg-muted flex items-center justify-center">
        <Icon className="h-7 w-7 text-muted-foreground" />
      </div>
      <div>
        <p className="font-medium">{titulo}</p>
        <p className="text-sm text-muted-foreground max-w-sm mt-1">{descripcion}</p>
      </div>
      {accion}
    </div>
  );
}

export function fecha(iso: string | null | undefined): string {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("es", {
      day: "2-digit", month: "short", year: "numeric",
      hour: "2-digit", minute: "2-digit",
    });
  } catch {
    return iso;
  }
}

export function numero(n: number | undefined | null): string {
  if (n === undefined || n === null) return "—";
  return new Intl.NumberFormat("es").format(n);
}
