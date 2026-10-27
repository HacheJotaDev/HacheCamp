"use client";

import { useQuery } from "@tanstack/react-query";
import {
  Inbox, HelpCircle, CheckCircle2, AlertTriangle, ServerOff, ShieldAlert,
  CircleHelp, Info, Ban,
} from "lucide-react";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Skeleton } from "@/components/ui/skeleton";
import { Badge } from "@/components/ui/badge";
import { api } from "@/lib/api";
import { numero } from "@/components/shared";

interface DeliverabilityData {
  aceptados_por_ses: number;
  entregado_servidor_receptor: number;
  rebote_permanente: number;
  rebote_temporal: number;
  rechazados: number;
  quejas_spam: number;
  ubicacion_desconocida: number;
  inbox_confirmado: number | null;
  spam_confirmado: number | null;
  inbox_testing: { disponible: boolean; motivo: string };
  nota: string;
}

export default function EntregabilidadPage() {
  const { data, isLoading } = useQuery({
    queryKey: ["entregabilidad"],
    queryFn: () => api.get<DeliverabilityData>("/api/v1/stats/deliverability"),
    refetchInterval: 60_000,
  });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Entregabilidad</h1>
        <p className="text-sm text-muted-foreground">
          Ubicación de los correos según lo que Amazon SES realmente confirma
        </p>
      </div>

      {isLoading || !data ? (
        <div className="grid gap-3 grid-cols-2 lg:grid-cols-3">
          {Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-28" />)}
        </div>
      ) : (
        <>
          <div className="grid gap-3 grid-cols-2 lg:grid-cols-3">
            {/* Inbox/Spam confirmado: no disponible honestamente */}
            <NoDisponible titulo="Inbox confirmado" icon={Inbox}
              motivo={data.inbox_testing.motivo} />
            <NoDisponible titulo="Spam confirmado" icon={ShieldAlert}
              motivo={data.inbox_testing.motivo} />

            <Tarjeta titulo="Entregado al servidor receptor" icon={CheckCircle2}
              valor={data.entregado_servidor_receptor} tono="text-emerald-600"
              detalle="Confirmado por SES (evento delivery)" />
            <Tarjeta titulo="Rebotado" icon={AlertTriangle}
              valor={data.rebote_permanente + data.rebote_temporal} tono="text-amber-600"
              detalle={`${numero(data.rebote_permanente)} permanentes · ${numero(data.rebote_temporal)} temporales`} />
            <Tarjeta titulo="Rechazado" icon={ServerOff}
              valor={data.rechazados} tono="text-red-600"
              detalle="SES rechazó el mensaje al procesarlo" />
            <Tarjeta titulo="Ubicación desconocida" icon={CircleHelp}
              valor={data.ubicacion_desconocida} tono="text-violet-600"
              detalle="Resultado no confirmado tras el envío (requiere reconciliación)" />
          </div>

          <div className="grid gap-3 lg:grid-cols-2">
            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <Ban className="h-4 w-4 text-red-600" /> Quejas de spam
                </CardTitle>
                <CardDescription>
                  Destinatarios que marcaron el correo como spam en su cliente
                </CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-bold tabular-nums">{numero(data.quejas_spam)}</p>
                <p className="text-xs text-muted-foreground mt-1">
                  {data.quejas_spam === 0
                    ? "Sin quejas registradas. Una tasa de quejas alta daña la reputación del remitente."
                    : "Las quejas añadieron la dirección a la lista de supresión automáticamente."}
                </p>
              </CardContent>
            </Card>

            <Card>
              <CardHeader className="pb-2">
                <CardTitle className="text-base flex items-center gap-2">
                  <Info className="h-4 w-4 text-sky-600" /> Aceptados por SES
                </CardTitle>
                <CardDescription>Mensajes que la API de envío aceptó procesar</CardDescription>
              </CardHeader>
              <CardContent>
                <p className="text-3xl font-bold tabular-nums">{numero(data.aceptados_por_ses)}</p>
                <p className="text-xs text-muted-foreground mt-1">
                  «Aceptado» no equivale a «entregado»: SES puede aceptar un mensaje y
                  rebote después al contactar al servidor destino.
                </p>
              </CardContent>
            </Card>
          </div>

          <Card>
            <CardContent className="p-4 flex items-start gap-3 text-sm">
              <HelpCircle className="h-5 w-5 text-muted-foreground shrink-0 mt-0.5" />
              <div className="space-y-1.5">
                <p className="font-medium">¿Por qué Inbox y Spam aparecen como «no disponible»?</p>
                <p className="text-muted-foreground">{data.inbox_testing.motivo}</p>
                <p className="text-muted-foreground">{data.nota}</p>
                <p className="text-xs text-muted-foreground pt-1 border-t">
                  Las quejas (complaints) son la única señal de spam que SES reporta: indican que un
                  receptor clasificó el correo como spam y el destinatario lo marcó.
                </p>
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
}

function Tarjeta({ titulo, valor, icon: Icon, tono, detalle }: {
  titulo: string; valor: number; icon: React.ComponentType<{ className?: string }>;
  tono: string; detalle: string;
}) {
  return (
    <Card>
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-xs font-medium text-muted-foreground">{titulo}</p>
            <p className="text-2xl font-bold mt-1 tabular-nums">{numero(valor)}</p>
            <p className="text-[11px] text-muted-foreground mt-0.5">{detalle}</p>
          </div>
          <div className="h-9 w-9 rounded-lg bg-muted flex items-center justify-center shrink-0">
            <Icon className={`h-4.5 w-4.5 ${tono}`} />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

function NoDisponible({ titulo, icon: Icon, motivo }: {
  titulo: string; icon: React.ComponentType<{ className?: string }>; motivo: string;
}) {
  return (
    <Card className="border-dashed">
      <CardContent className="p-4">
        <div className="flex items-start justify-between gap-2">
          <div className="min-w-0">
            <p className="text-xs font-medium text-muted-foreground flex items-center gap-1.5">
              {titulo}
              <Badge variant="outline" className="text-[10px] text-muted-foreground">No disponible</Badge>
            </p>
            <p className="text-2xl font-bold mt-1 text-muted-foreground/50">—</p>
            <p className="text-[11px] text-muted-foreground mt-0.5 line-clamp-3">{motivo}</p>
          </div>
          <div className="h-9 w-9 rounded-lg bg-muted flex items-center justify-center shrink-0">
            <Icon className="h-4.5 w-4.5 text-muted-foreground" />
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
