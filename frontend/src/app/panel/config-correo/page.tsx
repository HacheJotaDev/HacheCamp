"use client";

/**
 * Configuración de correo: remitente y asunto predeterminados del panel.
 *
 * - Los valores se guardan en el backend (app_settings) y se cargan al abrir.
 * - Las campañas nuevas y los envíos de prueba usan estos valores automáticamente.
 * - Solo los administradores pueden guardar (el backend lo exige con 403).
 */

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { AtSign, Eye, Loader2, Lock, Save } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { api, MailConfig } from "@/lib/api";
import { useAuth } from "@/lib/providers";

// Validación espejo del backend (app/services/mail_config.py):
// dominio exclusivo del proyecto y sin saltos de línea (anti inyección de cabeceras).
export function validarConfigCorreo(cfg: { from_name: string; from_email: string; subject: string }): string | null {
  const nombre = cfg.from_name.trim();
  const correo = cfg.from_email.trim().toLowerCase();
  const asunto = cfg.subject.trim();
  const campos = [["nombre del remitente", nombre], ["correo del remitente", correo], ["asunto", asunto]] as const;
  for (const [campo, valor] of campos) {
    if (/[\r\n\u0000-\u001f\u007f]/.test(valor)) {
      return `El campo '${campo}' contiene saltos de línea o caracteres no permitidos`;
    }
  }
  if (nombre.length < 2 || nombre.length > 120) {
    return "El nombre del remitente debe tener entre 2 y 120 caracteres";
  }
  if (!/^[a-z0-9._%+\-]+@leisureparts4u\.store$/i.test(correo)) {
    return "El correo del remitente debe pertenecer al dominio @leisureparts4u.store (verificado en Amazon SES)";
  }
  if (asunto.length < 1 || asunto.length > 255) {
    return "El asunto debe tener entre 1 y 255 caracteres";
  }
  return null;
}

export default function ConfigCorreoPage() {
  const { user } = useAuth();
  const esAdmin = user?.role === "admin";
  const qc = useQueryClient();

  const { data, isLoading, error: errorCarga } = useQuery({
    queryKey: ["mail-config"],
    queryFn: () => api.get<MailConfig>("/api/v1/system/mail-config"),
  });

  const [borrador, setBorrador] = useState<Partial<Pick<MailConfig, "from_name" | "from_email" | "subject">>>({});

  // Estado derivado: el borrador del usuario tiene prioridad sobre los valores
  // guardados; al abrir el apartado se muestran los valores persistidos.
  const form = {
    from_name: borrador.from_name ?? data?.from_name ?? "",
    from_email: borrador.from_email ?? data?.from_email ?? "",
    subject: borrador.subject ?? data?.subject ?? "",
  };
  const setCampo = (campo: keyof typeof form, valor: string) =>
    setBorrador((b) => ({ ...b, [campo]: valor }));

  const guardar = useMutation({
    mutationFn: () =>
      api.put<{ detalle: string; config: MailConfig }>("/api/v1/system/mail-config", {
        from_name: form.from_name.trim(),
        from_email: form.from_email.trim().toLowerCase(),
        subject: form.subject.trim(),
      }),
    onSuccess: () => {
      toast.success("Configuración de correo guardada");
      qc.invalidateQueries({ queryKey: ["mail-config"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const errorValidacion = validarConfigCorreo(form);
  const sinCambios = !data || (
    form.from_name.trim() === data.from_name &&
    form.from_email.trim().toLowerCase() === data.from_email &&
    form.subject.trim() === data.subject
  );

  if (isLoading) {
    return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-muted-foreground" /></div>;
  }
  if (errorCarga) {
    return (
      <div className="space-y-4">
        <Titulo />
        <Card><CardContent className="p-6 text-sm text-muted-foreground">
          No se pudo cargar la configuración: {(errorCarga as Error).message}
        </CardContent></Card>
      </div>
    );
  }

  return (
    <div className="space-y-4 max-w-2xl">
      <Titulo />

      {!esAdmin && (
        <div className="flex items-start gap-2 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm">
          <Lock className="h-4 w-4 text-amber-600 shrink-0 mt-0.5" />
          <span>
            Solo un usuario con rol <b>administrador</b> puede modificar la configuración.
            Puedes consultar los valores en uso.
          </span>
        </div>
      )}

      <Card>
        <CardHeader>
          <CardTitle className="flex items-center gap-2">
            <AtSign className="h-4.5 w-4.5 text-emerald-600" /> Remitente y asunto predeterminados
          </CardTitle>
          <CardDescription>
            Las campañas nuevas y los envíos de prueba usarán estos valores automáticamente.
            Si una campaña ya tiene remitente o asunto propio, no se sobrescriben.
          </CardDescription>
        </CardHeader>
        <CardContent className="space-y-4">
          <div className="space-y-1.5">
            <Label htmlFor="from-name">Nombre del remitente</Label>
            <Input id="from-name" value={form.from_name}
              onChange={(e) => setCampo("from_name", e.target.value)}
              disabled={!esAdmin} placeholder="HJ Campaigns" maxLength={120} />
            <p className="text-xs text-muted-foreground">
              Es lo que verán los destinatarios en Gmail (ej.: “HJ Campaigns”). Sin saltos de línea.
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="from-email">Correo del remitente</Label>
            <Input id="from-email" type="email" value={form.from_email}
              onChange={(e) => setCampo("from_email", e.target.value)}
              disabled={!esAdmin} placeholder={`notificaciones@${data?.dominio ?? "leisureparts4u.store"}`} />
            <p className="text-xs text-muted-foreground">
              Debe pertenecer al dominio verificado en Amazon SES:{" "}
              <Badge variant="outline" className="font-mono text-[10px]">@{data?.dominio ?? "leisureparts4u.store"}</Badge>
            </p>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="subject">Asunto predeterminado</Label>
            <Input id="subject" value={form.subject}
              onChange={(e) => setCampo("subject", e.target.value)}
              disabled={!esAdmin} placeholder="Novedades especiales para ti" maxLength={255} />
            <p className="text-xs text-muted-foreground">
              Se precarga al crear una campaña; podrás editarlo en cada campaña sin problema.
            </p>
          </div>

          {/* Previsualización de la cabecera "De:" tal como la muestra el cliente de correo */}
          <div className="rounded-lg border bg-muted/30 p-3.5 space-y-1">
            <p className="flex items-center gap-1.5 text-xs font-medium text-muted-foreground">
              <Eye className="h-3.5 w-3.5" /> Previsualización (cabecera From)
            </p>
            <p className="text-sm">
              <span className="text-muted-foreground">De:</span>{" "}
              <b>{form.from_name.trim() || "—"}</b>{" "}
              <span className="text-muted-foreground">&lt;{form.from_email.trim() || "—"}&gt;</span>
            </p>
            <p className="text-sm">
              <span className="text-muted-foreground">Asunto:</span> {form.subject.trim() || "—"}
            </p>
          </div>

          {errorValidacion && (
            <p className="text-sm text-red-600" role="alert">{errorValidacion}</p>
          )}

          <div className="flex justify-end pt-1">
            <Button className="bg-emerald-600 hover:bg-emerald-700"
              disabled={!esAdmin || Boolean(errorValidacion) || sinCambios || guardar.isPending}
              onClick={() => guardar.mutate()}>
              {guardar.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <Save className="mr-1.5 h-4 w-4" />}
              Guardar configuración
            </Button>
          </div>
        </CardContent>
      </Card>
    </div>
  );
}

function Titulo() {
  return (
    <div>
      <h1 className="text-2xl font-bold tracking-tight">Configuración de correo</h1>
      <p className="text-sm text-muted-foreground">
        Remitente y asunto que se usan por defecto en campañas y pruebas
      </p>
    </div>
  );
}
