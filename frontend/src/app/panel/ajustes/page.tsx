"use client";

import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Settings as SettingsIcon, Users, ShieldCheck, Server, Loader2, KeyRound, UserPlus,
  ClipboardCopy, Webhook, CloudCog, Activity, Stethoscope,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Badge } from "@/components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Switch } from "@/components/ui/switch";
import { InputOTP, InputOTPGroup, InputOTPSlot } from "@/components/ui/input-otp";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, User } from "@/lib/api";
import { useAuth } from "@/lib/providers";
import { fecha } from "@/components/shared";

export default function AjustesPage() {
  const { user: yo, refrescar } = useAuth();
  const esAdmin = yo?.role === "admin";
  const qc = useQueryClient();

  // ---- MFA ----
  const [setup, setSetup] = useState<{ secret: string; otpauth_url: string } | null>(null);
  const [codigoMfa, setCodigoMfa] = useState("");
  const [codigosRecuperacion, setCodigosRecuperacion] = useState<string[] | null>(null);

  const iniciarMfa = useMutation({
    mutationFn: () => api.post<{ secret: string; otpauth_url: string }>("/api/v1/auth/mfa/setup"),
    onSuccess: setSetup,
    onError: (e: Error) => toast.error(e.message),
  });

  const activarMfa = useMutation({
    mutationFn: () => api.post<{ codigos_recuperacion: string[] }>("/api/v1/auth/mfa/enable", {
      secret: setup!.secret, code: codigoMfa,
    }),
    onSuccess: (res) => {
      setSetup(null);
      setCodigoMfa("");
      setCodigosRecuperacion(res.codigos_recuperacion);
      refrescar();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  // ---- Contraseña ----
  const [passActual, setPassActual] = useState("");
  const [passNueva, setPassNueva] = useState("");
  const cambiarPass = useMutation({
    mutationFn: () => api.post("/api/v1/auth/password", {
      current_password: passActual, new_password: passNueva,
    }),
    onSuccess: () => {
      toast.success("Contraseña cambiada. Vuelve a iniciar sesión.");
      setTimeout(() => { window.location.href = "/"; }, 1200);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  // ---- SES ----
  const { data: ses } = useQuery({
    queryKey: ["ses-status"],
    queryFn: () => api.get<{
      region: string; cuota: Record<string, number | string | boolean>;
      identidades: { identity: string; tipo: string; verificado: boolean }[];
      error_identidades: string | null;
      remitente_inicial: string;
    }>("/api/v1/system/ses/status"),
  });

  // Métricas reales de rendimiento (se actualizan solas cada 10 s)
  const { data: rend } = useQuery({
    queryKey: ["ses-rendimiento"],
    queryFn: () => api.get<SesRendimiento>("/api/v1/system/ses/rendimiento"),
    refetchInterval: 10000,
  });

  // Diagnóstico del pipeline de eventos (solo admin, bajo demanda)
  const { data: diag, isFetching: diagCargando, refetch: refrescarDiag } = useQuery({
    queryKey: ["ses-diagnostico"],
    queryFn: () => api.get<SesDiagnostico>("/api/v1/system/ses/diagnostico"),
    enabled: esAdmin,
  });

  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-2xl font-bold tracking-tight">Ajustes</h1>
        <p className="text-sm text-muted-foreground">Cuenta, seguridad MFA, integración SES y usuarios</p>
      </div>

      <Tabs defaultValue="cuenta">
        <TabsList className="flex-wrap h-auto">
          <TabsTrigger value="cuenta"><SettingsIcon className="h-3.5 w-3.5 mr-1.5" /> Cuenta</TabsTrigger>
          <TabsTrigger value="ses"><CloudCog className="h-3.5 w-3.5 mr-1.5" /> Amazon SES</TabsTrigger>
          {esAdmin && <TabsTrigger value="usuarios"><Users className="h-3.5 w-3.5 mr-1.5" /> Usuarios</TabsTrigger>}
        </TabsList>

        {/* ---------- Cuenta ---------- */}
        <TabsContent value="cuenta" className="mt-2 space-y-4">
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <ShieldCheck className="h-4.5 w-4.5 text-emerald-600" /> Autenticación en dos pasos (TOTP)
              </CardTitle>
              <CardDescription>
                {yo?.totp_enabled
                  ? "MFA activado. Cada inicio de sesión pedirá un código de tu app autenticadora."
                  : "Protege tu cuenta con un código de un solo uso. Obligatorio para administradores."}
              </CardDescription>
            </CardHeader>
            <CardContent className="space-y-3">
              {yo?.totp_enabled ? (
                <Badge variant="outline" className="text-emerald-600 border-emerald-500/40">MFA activo</Badge>
              ) : !setup ? (
                <Button onClick={() => iniciarMfa.mutate()} disabled={iniciarMfa.isPending} className="bg-emerald-600 hover:bg-emerald-700">
                  {iniciarMfa.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Configurar MFA
                </Button>
              ) : (
                <div className="space-y-3">
                  <p className="text-sm">
                    1. Añade esta clave en tu app autenticadora (Google Authenticator, Authy, Aegis…):{" "}
                    <button className="font-mono text-emerald-600 inline-flex items-center gap-1"
                      onClick={() => { navigator.clipboard.writeText(setup.secret); toast.success("Clave copiada"); }}>
                      {setup.secret.slice(0, 8)}…{setup.secret.slice(-4)} <ClipboardCopy className="h-3 w-3" />
                    </button>
                  </p>
                  <p className="text-xs text-muted-foreground break-all">
                    URL otpauth: {setup.otpauth_url}
                  </p>
                  <div>
                    <p className="text-sm mb-2">2. Introduce el código de 6 dígitos:</p>
                    <InputOTP maxLength={6} value={codigoMfa} onChange={setCodigoMfa}>
                      <InputOTPGroup>
                        {Array.from({ length: 6 }).map((_, i) => <InputOTPSlot key={i} index={i} />)}
                      </InputOTPGroup>
                    </InputOTP>
                  </div>
                  <Button className="bg-emerald-600 hover:bg-emerald-700" disabled={codigoMfa.length < 6 || activarMfa.isPending}
                    onClick={() => activarMfa.mutate()}>
                    {activarMfa.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    Activar MFA
                  </Button>
                </div>
              )}

              {codigosRecuperacion && (
                <div className="rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 space-y-2">
                  <p className="text-sm font-medium text-amber-700 dark:text-amber-400">
                    Guarda estos códigos de recuperación AHORA (se muestran una sola vez):
                  </p>
                  <div className="grid grid-cols-2 sm:grid-cols-4 gap-1.5 font-mono text-xs">
                    {codigosRecuperacion.map((c) => <span key={c} className="bg-background rounded px-2 py-1">{c}</span>)}
                  </div>
                  <Button size="sm" variant="outline" onClick={() => { navigator.clipboard.writeText(codigosRecuperacion.join("\n")); toast.success("Códigos copiados"); }}>
                    Copiar todos
                  </Button>
                </div>
              )}
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><KeyRound className="h-4.5 w-4.5 text-emerald-600" /> Cambiar contraseña</CardTitle>
              <CardDescription>Mínimo 12 caracteres con números y letras. Se revocarán todas tus sesiones.</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 max-w-md">
              <div className="space-y-1.5">
                <Label>Contraseña actual</Label>
                <Input type="password" value={passActual} onChange={(e) => setPassActual(e.target.value)} />
              </div>
              <div className="space-y-1.5">
                <Label>Nueva contraseña</Label>
                <Input type="password" value={passNueva} onChange={(e) => setPassNueva(e.target.value)} />
              </div>
              <Button className="bg-emerald-600 hover:bg-emerald-700"
                disabled={!passActual || passNueva.length < 12 || cambiarPass.isPending} onClick={() => cambiarPass.mutate()}>
                {cambiarPass.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Cambiar contraseña
              </Button>
            </CardContent>
          </Card>

          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2"><Webhook className="h-4.5 w-4.5 text-emerald-600" /> Webhook de eventos SES</CardTitle>
              <CardDescription>
                Configura el tópico SNS para que apunte a <code className="text-xs">/api/v1/webhooks/sns?token=WEBHOOK_TOKEN</code>.
                El token se define en la variable WEBHOOK_TOKEN del .env del servidor.
              </CardDescription>
            </CardHeader>
          </Card>
        </TabsContent>

        {/* ---------- SES ---------- */}
        <TabsContent value="ses" className="mt-2">
          <Card>
            <CardHeader>
              <CardTitle>Estado de Amazon SES</CardTitle>
              <CardDescription>Información leída directamente de la API de SES (sin exponer credenciales)</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="flex items-center justify-between border-b pb-2">
                <span className="text-muted-foreground">Región</span>
                <Badge variant="outline">{ses?.region || "us-east-2"}</Badge>
              </div>
              {ses?.cuota && !("error" in ses.cuota) ? (
                <>
                  <div className="flex items-center justify-between border-b pb-2">
                    <span className="text-muted-foreground">Cuota 24 h</span>
                    <span className="font-medium tabular-nums">
                      {Number(ses.cuota.sent_last_24h || 0).toLocaleString("es")} / {Number(ses.cuota.max_24h || 0).toLocaleString("es")}
                    </span>
                  </div>
                  <div className="flex items-center justify-between border-b pb-2">
                    <span className="text-muted-foreground">Máximo por segundo</span>
                    <span className="font-medium tabular-nums">{String(ses.cuota.max_per_second)}</span>
                  </div>
                </>
              ) : (
                <p className="text-muted-foreground">
                  Cuota no disponible: el backend no puede consultar SES ahora mismo
                  (¿credenciales IAM configuradas en el servidor?).
                </p>
              )}
              <div className="flex items-center justify-between border-b pb-2">
                <span className="text-muted-foreground">Remitente inicial</span>
                <span className="font-medium">{ses?.remitente_inicial}</span>
              </div>
              {ses?.identidades && ses.identidades.length > 0 ? (
                <div>
                  <p className="font-medium mb-1.5">Identidades verificadas</p>
                  <div className="space-y-1">
                    {ses.identidades.map((i) => (
                      <div key={i.identity} className="flex items-center justify-between rounded bg-muted/50 px-2.5 py-1.5">
                        <span className="font-mono text-xs">{i.identity}</span>
                        <Badge variant="outline" className={i.verificado ? "text-emerald-600 border-emerald-500/40" : "text-amber-600"}>
                          {i.verificado ? "verificada" : "pendiente"}
                        </Badge>
                      </div>
                    ))}
                  </div>
                </div>
              ) : (
                <p className="text-xs text-muted-foreground">
                  Identidades no consultables en este entorno ({ses?.error_identidades || "sin acceso a SES"}).
                </p>
              )}
              <p className="text-xs text-muted-foreground border-t pt-2">
                Nota: una aceptación de SES no equivale a una entrega confirmada al destinatario,
                y ninguna métrica garantiza llegada a Recibidos.
              </p>
            </CardContent>
          </Card>

          {/* ---- Rendimiento real de envío ---- */}
          <Card>
            <CardHeader>
              <CardTitle className="flex items-center gap-2">
                <Activity className="h-4.5 w-4.5 text-emerald-600" /> Rendimiento de envío
              </CardTitle>
              <CardDescription>Métricas reales medidas por la aplicación (se actualizan cada 10 s)</CardDescription>
            </CardHeader>
            <CardContent className="space-y-3 text-sm">
              <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
                <Metrica etiqueta="Por segundo (60s)" valor={rend ? `${rend.por_segundo.toFixed(1)}` : "…"} />
                <Metrica etiqueta="Por minuto (60m)" valor={rend ? rend.por_minuto.toLocaleString("es") : "…"} />
                <Metrica etiqueta="Entregas (60m)" valor={rend ? rend.entregados_60m.toLocaleString("es") : "…"} />
                <Metrica etiqueta="Enviados 24h (SES)" valor={rend ? Number(rend.cuota_24h?.sent_last_24h ?? 0).toLocaleString("es") : "…"} />
              </div>
              <div className="flex items-center justify-between border-b pb-2">
                <span className="text-muted-foreground">Velocidad efectiva / configurada</span>
                <span className="font-medium tabular-nums">
                  {rend ? `${rend.velocidad.efectiva ?? "?"} / ${rend.velocidad.configurada} msg/s` : "…"}
                </span>
              </div>
              <div className="flex items-center justify-between border-b pb-2">
                <span className="text-muted-foreground">Cuota disponible 24 h</span>
                <span className="font-medium tabular-nums">
                  {rend?.cuota_24h && rend.cuota_24h.restante_24h != null
                    ? `${Number(rend.cuota_24h.restante_24h).toLocaleString("es")} (${rend.cuota_24h.usado_pct_24h ?? 0}% usado)`
                    : "…"}
                </span>
              </div>
              <div className="flex items-center justify-between border-b pb-2">
                <span className="text-muted-foreground">Pendientes (cola / reintentos / BD)</span>
                <span className="font-medium tabular-nums">
                  {rend ? `${rend.pendientes.cola_redis} / ${rend.pendientes.reintentos_redis} / ${rend.pendientes.base_datos}` : "…"}
                </span>
              </div>
              <div className="flex items-center justify-between border-b pb-2">
                <span className="text-muted-foreground">Errores (24 h) · Inciertos</span>
                <span className="font-medium tabular-nums">
                  {rend ? `${rend.reintentos_y_errores.errores_24h} · ${rend.reintentos_y_errores.incierto_total}` : "…"}
                </span>
              </div>
              {rend?.campanas_activas && rend.campanas_activas.length > 0 && (
                <div>
                  <p className="font-medium mb-1.5">Campañas en curso</p>
                  <div className="space-y-1.5">
                    {rend.campanas_activas.map((c) => (
                      <div key={c.id} className="rounded bg-muted/50 px-2.5 py-1.5 text-xs">
                        <div className="flex items-center justify-between">
                          <span className="font-medium truncate">{c.nombre}</span>
                          <span className="tabular-nums">{c.progreso_pct}% · {c.aceptados}/{c.total} aceptados</span>
                        </div>
                        <div className="text-muted-foreground mt-0.5">
                          pendientes {c.pendientes} · entregados {c.entregados} · errores {c.errores} · reintentos {c.reintentos}
                        </div>
                      </div>
                    ))}
                  </div>
                </div>
              )}
            </CardContent>
          </Card>

          {/* ---- Diagnóstico del pipeline de eventos (admin) ---- */}
          {esAdmin && (
            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <Stethoscope className="h-4.5 w-4.5 text-emerald-600" /> Diagnóstico de eventos SES
                </CardTitle>
                <CardDescription>
                  Por qué los aceptados no cuadran con las entregas: configuration set, consumidor SQS, cola y descartes
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-3 text-sm">
                <div className="flex items-center justify-between gap-2 border-b pb-2">
                  <span className="text-muted-foreground">Configuration set aplicado a los envíos</span>
                  {diag ? (
                    <Badge variant="outline" className={diag.configuration_set_aplicado
                      ? "text-emerald-600 border-emerald-500/40" : "text-amber-600 border-amber-500/40"}>
                      {diag.configuration_set || "NO CONFIGURADO (SES no emite eventos)"}
                    </Badge>
                  ) : "…"}
                </div>
                <div className="flex items-center justify-between border-b pb-2">
                  <span className="text-muted-foreground">Consumidor SQS</span>
                  {diag ? (
                    <span className="flex items-center gap-2">
                      <Badge variant="outline" className={diag.consumidor.vivo
                        ? "text-emerald-600 border-emerald-500/40" : "text-red-600 border-red-500/40"}>
                        {diag.consumidor.vivo ? "vivo" : "sin latido"}
                      </Badge>
                      {diag.consumidor.edad_ultimo_latido_s != null && (
                        <span className="text-xs tabular-nums">hace {diag.consumidor.edad_ultimo_latido_s}s</span>
                      )}
                    </span>
                  ) : "…"}
                </div>
                {diag?.cola_sqs?.habilitado && (
                  <div className="flex items-center justify-between border-b pb-2">
                    <span className="text-muted-foreground">Cola SQS ({diag.cola_sqs.cola})</span>
                    <span className="font-medium tabular-nums">
                      visibles {diag.cola_sqs.mensajes_visibles ?? "—"} · en proceso {diag.cola_sqs.mensajes_en_proceso ?? "—"}
                      {diag.cola_sqs.dlq != null && ` · DLQ ${diag.cola_sqs.dlq_mensajes ?? "—"}`}
                    </span>
                  </div>
                )}
                {diag?.eventos && (
                  <div className="flex items-center justify-between border-b pb-2">
                    <span className="text-muted-foreground">Último evento recibido</span>
                    <span className="font-medium">
                      {diag.eventos.ultimo_evento ? fecha(diag.eventos.ultimo_evento) : "nunca"}
                    </span>
                  </div>
                )}
                {diag?.eventos && Object.keys(diag.eventos.por_tipo_24h).length > 0 && (
                  <div className="flex items-center justify-between border-b pb-2">
                    <span className="text-muted-foreground">Eventos 24 h</span>
                    <span className="font-medium tabular-nums text-xs">
                      {Object.entries(diag.eventos.por_tipo_24h).map(([t, n]) => `${t}: ${n}`).join(" · ")}
                    </span>
                  </div>
                )}
                {diag && Object.keys(diag.mensajes_por_estado).length > 0 && (
                  <div className="flex items-center justify-between border-b pb-2">
                    <span className="text-muted-foreground">Mensajes por estado</span>
                    <span className="font-medium tabular-nums text-xs">
                      {Object.entries(diag.mensajes_por_estado).map(([e, n]) => `${e}: ${Number(n).toLocaleString("es")}`).join(" · ")}
                    </span>
                  </div>
                )}
                {diag?.eventos?.descartes_recientes && diag.eventos.descartes_recientes.length > 0 && (
                  <div>
                    <p className="font-medium mb-1.5">Últimos descartes ({diag.eventos.descartes_recientes.length})</p>
                    <div className="space-y-1">
                      {diag.eventos.descartes_recientes.slice(0, 5).map((d, i) => (
                        <div key={i} className="flex items-center justify-between rounded bg-muted/50 px-2.5 py-1.5 text-xs">
                          <span className="font-mono">{d.motivo || "desconocido"}</span>
                          <span className="text-muted-foreground">{fecha(d.fecha)}</span>
                        </div>
                      ))}
                    </div>
                  </div>
                )}
                {diag?.nota && (
                  <p className="text-xs text-amber-600 border border-amber-500/40 bg-amber-500/10 rounded-lg p-2.5">{diag.nota}</p>
                )}
                <Button variant="outline" size="sm" onClick={() => refrescarDiag()} disabled={diagCargando}>
                  {diagCargando ? <Loader2 className="mr-1.5 h-3.5 w-3.5 animate-spin" /> : null}
                  Actualizar diagnóstico
                </Button>
              </CardContent>
            </Card>
          )}
        </TabsContent>

        {/* ---------- Usuarios (solo admin) ---------- */}
        {esAdmin && <TabsContent value="usuarios" className="mt-2"><UsuariosAdmin /></TabsContent>}
      </Tabs>
    </div>
  );
}

function UsuariosAdmin() {
  const qc = useQueryClient();
  const [nuevo, setNuevo] = useState({ email: "", name: "", password: "", role: "operador" });
  const [crearAbierto, setCrearAbierto] = useState(false);

  const { data: usuarios, isLoading } = useQuery({
    queryKey: ["usuarios"],
    queryFn: () => api.get<User[]>("/api/v1/users"),
  });

  const crear = useMutation({
    mutationFn: () => api.post("/api/v1/users", nuevo),
    onSuccess: () => {
      toast.success("Usuario creado");
      setCrearAbierto(false);
      setNuevo({ email: "", name: "", password: "", role: "operador" });
      qc.invalidateQueries({ queryKey: ["usuarios"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const cambiarEstado = useMutation({
    mutationFn: ({ id, is_active }: { id: string; is_active: boolean }) =>
      api.patch(`/api/v1/users/${id}`, { is_active }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["usuarios"] }),
    onError: (e: Error) => toast.error(e.message),
  });

  const cambiarRol = useMutation({
    mutationFn: ({ id, role }: { id: string; role: string }) =>
      api.patch(`/api/v1/users/${id}`, { role }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["usuarios"] }),
    onError: (e: Error) => toast.error(e.message),
  });

  return (
    <Card>
      <CardHeader>
        <div className="flex items-center justify-between">
          <div>
            <CardTitle>Usuarios del panel</CardTitle>
            <CardDescription>Administradores y operadores con permisos por rol</CardDescription>
          </div>
          <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700" onClick={() => setCrearAbierto(true)}>
            <UserPlus className="mr-1.5 h-4 w-4" /> Nuevo
          </Button>
        </div>
      </CardHeader>
      <CardContent className="p-0">
        {isLoading ? (
          <div className="flex justify-center py-10"><Loader2 className="h-7 w-7 animate-spin text-muted-foreground" /></div>
        ) : (
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Usuario</TableHead>
                <TableHead>Rol</TableHead>
                <TableHead>MFA</TableHead>
                <TableHead>Activo</TableHead>
                <TableHead className="hidden md:table-cell">Último acceso</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {usuarios?.map((u) => (
                <TableRow key={u.id}>
                  <TableCell>
                    <p className="font-medium">{u.name}</p>
                    <p className="text-xs text-muted-foreground">{u.email}</p>
                  </TableCell>
                  <TableCell>
                    <Select value={u.role} onValueChange={(v) => cambiarRol.mutate({ id: u.id, role: v })}>
                      <SelectTrigger className="w-28 h-8 text-xs"><SelectValue /></SelectTrigger>
                      <SelectContent>
                        <SelectItem value="admin">Admin</SelectItem>
                        <SelectItem value="operador">Operador</SelectItem>
                      </SelectContent>
                    </Select>
                  </TableCell>
                  <TableCell>
                    <Badge variant={u.totp_enabled ? "outline" : "secondary"}
                      className={u.totp_enabled ? "text-emerald-600 border-emerald-500/40" : ""}>
                      {u.totp_enabled ? "activo" : "sin MFA"}
                    </Badge>
                  </TableCell>
                  <TableCell>
                    <Switch checked={u.is_active} onCheckedChange={(v) => cambiarEstado.mutate({ id: u.id, is_active: v })} />
                  </TableCell>
                  <TableCell className="hidden md:table-cell text-sm text-muted-foreground">{fecha(u.last_login_at)}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        )}
      </CardContent>

      <Dialog open={crearAbierto} onOpenChange={setCrearAbierto}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Nuevo usuario</DialogTitle>
            <DialogDescription>Mínimo 12 caracteres con números y letras.</DialogDescription>
          </DialogHeader>
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label>Nombre</Label>
              <Input value={nuevo.name} onChange={(e) => setNuevo({ ...nuevo, name: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>Correo</Label>
              <Input type="email" value={nuevo.email} onChange={(e) => setNuevo({ ...nuevo, email: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>Contraseña inicial</Label>
              <Input type="password" value={nuevo.password} onChange={(e) => setNuevo({ ...nuevo, password: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label>Rol</Label>
              <Select value={nuevo.role} onValueChange={(v) => setNuevo({ ...nuevo, role: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="operador">Operador</SelectItem>
                  <SelectItem value="admin">Administrador</SelectItem>
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCrearAbierto(false)}>Cancelar</Button>
            <Button className="bg-emerald-600 hover:bg-emerald-700"
              disabled={!nuevo.email.includes("@") || nuevo.name.length < 2 || nuevo.password.length < 12 || crear.isPending}
              onClick={() => crear.mutate()}>
              {crear.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
              Crear usuario
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </Card>
  );
}

// --------------------------------------------------------------------------- //
// Tipos y piezas auxiliares de las métricas/diagnóstico SES
// --------------------------------------------------------------------------- //

type SesRendimiento = {
  aceptados_60s: number;
  por_segundo: number;
  por_minuto: number;
  entregados_60m: number;
  velocidad: { configurada: number; limite_ses: number | null; efectiva: number | null };
  cuota_24h: { max_24h?: number; sent_last_24h?: number; restante_24h?: number; usado_pct_24h?: number | null; error?: string };
  pendientes: { cola_redis: number; reintentos_redis: number; base_datos: number };
  reintentos_y_errores: { reintentos_programados: number; errores_24h: number; incierto_total: number };
  campanas_activas: {
    id: string; nombre: string; estado: string; total: number; pendientes: number;
    aceptados: number; entregados: number; errores: number; reintentos: number; progreso_pct: number;
  }[];
};

type SesDiagnostico = {
  configuration_set: string | null;
  configuration_set_aplicado: boolean;
  consumidor: { vivo: boolean; edad_ultimo_latido_s: number | null; ultimo_lote: Record<string, unknown> };
  cola_sqs: {
    habilitado: boolean; cola: string | null; error?: string;
    mensajes_visibles?: number; mensajes_en_proceso?: number; mensajes_retrasados?: number;
    dlq?: string; dlq_mensajes?: number | null;
  };
  eventos: {
    por_tipo_24h: Record<string, number>;
    por_tipo_7d: Record<string, number>;
    ultimo_evento: string | null;
    descartes_recientes: { motivo: string | null; lecturas: number | null; fecha: string }[];
  };
  mensajes_por_estado: Record<string, number>;
  nota: string | null;
};

function Metrica({ etiqueta, valor }: { etiqueta: string; valor: string }) {
  return (
    <div className="rounded-lg border p-2.5">
      <p className="text-xs text-muted-foreground">{etiqueta}</p>
      <p className="text-lg font-semibold tabular-nums">{valor}</p>
    </div>
  );
}
