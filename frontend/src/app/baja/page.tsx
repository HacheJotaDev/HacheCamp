"use client";

import { Suspense, useState } from "react";
import { useSearchParams } from "next/navigation";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { MailCheck, MailX, Loader2, ShieldCheck } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { api } from "@/lib/api";

/**
 * Página pública de baja de suscripción (sin inicio de sesión).
 * Confirmación explícita para evitar bajas accidentales; compatible con RFC 8058.
 */
export default function BajaPage() {
  return (
    <Suspense fallback={
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    }>
      <ContenidoBaja />
    </Suspense>
  );
}

function ContenidoBaja() {
  const params = useSearchParams();
  const token = params.get("token") || "";
  const [confirmada, setConfirmada] = useState(false);

  const info = useQuery({
    queryKey: ["baja-info", token],
    queryFn: () => api.get<{ email_enmascarado: string; campana: string | null; estado: string }>(
      `/api/v1/public/unsubscribe/info?token=${encodeURIComponent(token)}`),
    enabled: Boolean(token),
    retry: false,
  });

  const confirmar = useMutation({
    mutationFn: () => api.post("/api/v1/public/unsubscribe/confirm", { token }),
    onSuccess: () => setConfirmada(true),
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <div className="min-h-screen flex items-center justify-center bg-gradient-to-b from-background to-emerald-950/10 p-4">
      <Card className="w-full max-w-md">
        {!token ? (
          <>
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><MailX className="h-5 w-5 text-red-500" /> Enlace inválido</CardTitle>
              <CardDescription>
                Falta el parámetro de cancelación en la URL.
              </CardDescription>
            </CardHeader>
          </>
        ) : info.isLoading ? (
          <CardContent className="flex justify-center py-14">
            <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
          </CardContent>
        ) : info.isError ? (
          <>
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><MailX className="h-5 w-5 text-red-500" /> Enlace no válido</CardTitle>
              <CardDescription>
                El enlace de baja es incorrecto o ha caducado. Si deseas dejar de recibir
                correos, contacta con el remitente directamente.
              </CardDescription>
            </CardHeader>
          </>
        ) : confirmada || (info.data && info.data.estado === "dado_baja" && confirmada) ? (
          <>
            <CardHeader className="text-center">
              <div className="mx-auto h-14 w-14 rounded-full bg-emerald-600/15 flex items-center justify-center mb-1">
                <MailCheck className="h-7 w-7 text-emerald-600" />
              </div>
              <CardTitle>Suscripción cancelada</CardTitle>
              <CardDescription>
                La dirección <b>{info.data?.email_enmascarado}</b> ha quedado registrada como
                dada de baja y añadida a la lista de supresión global. No volverá a recibir
                campañas promocionales.
              </CardDescription>
            </CardHeader>
            <CardContent className="text-center text-xs text-muted-foreground">
              Los cambios son inmediatos. Este proceso no requiere contraseña.
            </CardContent>
          </>
        ) : (
          <>
            <CardHeader className="text-center">
              <div className="mx-auto h-14 w-14 rounded-full bg-amber-500/15 flex items-center justify-center mb-1">
                <MailX className="h-7 w-7 text-amber-600" />
              </div>
              <CardTitle>¿Quieres cancelar tu suscripción?</CardTitle>
              <CardDescription>
                Confirmamos que la solicitud corresponde a la dirección{" "}
                <b>{info.data?.email_enmascarado}</b>
                {info.data?.campana ? <> (campaña: {info.data.campana})</> : null}. No se revelará
                la dirección completa por privacidad.
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              <Button
                className="w-full bg-emerald-600 hover:bg-emerald-700 min-h-[44px]"
                disabled={confirmar.isPending}
                onClick={() => confirmar.mutate()}
              >
                {confirmar.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Sí, darme de baja
              </Button>
              <p className="text-[11px] text-muted-foreground flex items-center justify-center gap-1">
                <ShieldCheck className="h-3 w-3" /> Tu decisión se procesa de forma inmediata y permanente.
              </p>
            </CardContent>
          </>
        )}
      </Card>
    </div>
  );
}
