"use client";

import { useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  ArrowLeft, Loader2, ArrowRight, CheckCircle2, Send, CalendarClock, Users,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { RadioGroup, RadioGroupItem } from "@/components/ui/radio-group";
import { api, ContactList, Segment, Template } from "@/lib/api";
import { cn } from "@/lib/utils";
import { numero } from "@/components/shared";

const ZONAS = [
  "America/Lima", "America/Bogota", "America/Mexico_City", "America/Santiago",
  "America/Buenos_Aires", "America/Madrid", "UTC",
];

const PASOS = ["Destinatarios", "Asunto", "Contenido", "Envío", "Confirmar"];

export default function NuevaCampanaPage() {
  const router = useRouter();
  const qc = useQueryClient();
  const [paso, setPaso] = useState(1);
  const [nombre, setNombre] = useState("");
  const [asunto, setAsunto] = useState("");
  const [categoria, setCategoria] = useState("promocional");
  const [listId, setListId] = useState("");
  const [segmentId, setSegmentId] = useState("");
  const [modo, setModo] = useState<"bloques" | "html">("html");
  const [templateId, setTemplateId] = useState("");
  const [html, setHtml] = useState("");
  const [bloques, setBloques] = useState<Template["blocks"]>([]);
  const [momento, setMomento] = useState<"ahora" | "programar">("ahora");
  const [fechaHora, setFechaHora] = useState("");
  const [zona, setZona] = useState("America/Lima");

  const { data: plantillas } = useQuery({
    queryKey: ["plantillas"], queryFn: () => api.get<Template[]>("/api/v1/templates"),
  });
  const { data: listas } = useQuery({
    queryKey: ["listas"], queryFn: () => api.get<ContactList[]>("/api/v1/lists"),
  });
  const { data: segmentos } = useQuery({
    queryKey: ["segmentos"], queryFn: () => api.get<Segment[]>("/api/v1/segments"),
  });

  const { data: estimacion, isFetching: contando } = useQuery({
    queryKey: ["preview-destinatarios", listId, segmentId, categoria],
    queryFn: () =>
      api.post<{ destinatarios: number }>("/api/v1/campaigns/recipients-preview", {
        list_id: listId || null, segment_id: segmentId || null, category: categoria,
      }),
    enabled: Boolean(listId || segmentId),
  });

  const elegirPlantilla = (id: string) => {
    setTemplateId(id);
    const t = plantillas?.find((p) => p.id === id);
    if (t) {
      setModo(t.mode);
      setBloques(t.blocks ?? []);
      setHtml(t.html ?? "");
    }
  };

  const fechaIsoUtc = (): string | null => {
    if (momento !== "programar" || !fechaHora) return null;
    // fechaHora es hora local en la zona elegida -> convertimos a UTC ISO
    try {
      const dt = new Date(fechaHora);
      // Si el navegador está en otra zona, usamos la conversión explícita vía Intl
      const offsetZona = obtenerOffset(fechaHora, zona);
      return new Date(dt.getTime() - offsetZona).toISOString();
    } catch {
      return null;
    }
  };

  function obtenerOffset(fechaLocal: string, zonaHoraria: string): number {
    // Calcula el offset en ms de la zona horaria para esa fecha
    const dt = new Date(fechaLocal);
    const utc = new Date(dt.toLocaleString("en-US", { timeZone: "UTC" }));
    const enZona = new Date(dt.toLocaleString("en-US", { timeZone: zonaHoraria }));
    return enZona.getTime() - utc.getTime();
  }

  const crearYEnviar = useMutation({
    mutationFn: async () => {
      const campana = await api.post<{ id: string }>("/api/v1/campaigns", {
        name: nombre,
        subject: asunto,
        template_id: templateId || null,
        mode: modo,
        blocks: modo === "bloques" ? bloques : null,
        html: modo === "html" ? html : null,
        category: categoria,
        list_id: listId || null,
        segment_id: segmentId || null,
        scheduled_at: null,
        timezone: zona,
      });
      // Aprobación (el backend la exige; el flujo la solicita aquí mismo)
      await api.post(`/api/v1/campaigns/${campana.id}/approve`);
      if (momento === "ahora") {
        const res = await api.post<{ destinatarios: number }>(`/api/v1/campaigns/${campana.id}/send-now`);
        return { id: campana.id, encolados: res.destinatarios, programada: false };
      }
      const iso = fechaIsoUtc();
      await api.post(`/api/v1/campaigns/${campana.id}/schedule`, {
        scheduled_at: iso, timezone: zona,
      });
      return { id: campana.id, encolados: 0, programada: true };
    },
    onSuccess: (res) => {
      qc.invalidateQueries({ queryKey: ["campanas"] });
      toast.success(res.programada
        ? "Campaña programada correctamente"
        : `Envío iniciado: ${numero(res.encolados)} correos en cola`);
      router.push(res.programada ? "/panel/programadas" : `/panel/cola`);
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const paso1Valido = Boolean(listId || segmentId);
  const paso2Valido = asunto.trim().length > 0;
  const paso3Valido = modo === "bloques" ? (bloques?.length ?? 0) > 0 : html.trim().length > 0;
  const paso4Valido = momento === "ahora" || (fechaHora && fechaIsoUtc());
  const nombreCampana = nombre.trim() || asunto.trim().slice(0, 40) || "Campaña";

  return (
    <div className="space-y-4 max-w-3xl">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" asChild aria-label="Volver">
          <Link href="/panel/campanas"><ArrowLeft className="h-4 w-4" /></Link>
        </Button>
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Nueva campaña</h1>
          <p className="text-sm text-muted-foreground">Cinco pasos sencillos. El remitente se toma de la configuración.</p>
        </div>
      </div>

      {/* Indicador de pasos */}
      <div className="flex items-center gap-1.5 sm:gap-2 text-sm overflow-x-auto pb-1">
        {PASOS.map((l, i) => (
          <div key={l} className="flex items-center gap-1.5 sm:gap-2 shrink-0">
            <div className={cn(
              "h-7 w-7 rounded-full flex items-center justify-center text-xs font-bold border-2",
              paso > i + 1 ? "bg-emerald-600 border-emerald-600 text-white"
                : paso === i + 1 ? "border-emerald-600 text-emerald-600" : "border-muted text-muted-foreground")}>
              {paso > i + 1 ? <CheckCircle2 className="h-4 w-4" /> : i + 1}
            </div>
            <span className={cn(paso === i + 1 ? "font-semibold" : "text-muted-foreground", "hidden sm:block")}>{l}</span>
            {i < 4 && <div className="h-px w-4 sm:w-8 bg-border" />}
          </div>
        ))}
      </div>

      {/* PASO 1: Destinatarios */}
      {paso === 1 && (
        <Card>
          <CardContent className="p-4 space-y-4">
            <div className="space-y-1.5">
              <Label>Lista de destinatarios</Label>
              <Select value={listId} onValueChange={(v) => { setListId(v); setSegmentId(""); }}>
                <SelectTrigger><SelectValue placeholder="Elige una lista" /></SelectTrigger>
                <SelectContent>
                  {listas?.map((l) => (
                    <SelectItem key={l.id} value={l.id}>{l.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <p className="text-center text-xs text-muted-foreground">— o —</p>
            <div className="space-y-1.5">
              <Label>Segmento</Label>
              <Select value={segmentId} onValueChange={(v) => { setSegmentId(v); setListId(""); }}>
                <SelectTrigger><SelectValue placeholder="Elige un segmento" /></SelectTrigger>
                <SelectContent>
                  {segmentos?.map((s) => (
                    <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            <div className="space-y-1.5">
              <Label>Tipo de envío</Label>
              <Select value={categoria} onValueChange={setCategoria}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="promocional">Promocional</SelectItem>
                  <SelectItem value="transaccional">Transaccional</SelectItem>
                </SelectContent>
              </Select>
            </div>
            {(listId || segmentId) && (
              <div className="flex items-center gap-2 rounded-lg bg-emerald-500/10 border border-emerald-500/30 p-3 text-sm">
                <Users className="h-4 w-4 text-emerald-600 shrink-0" />
                <span>
                  {contando ? "Contando destinatarios elegibles…"
                    : <>Destinatarios elegibles: <b>{numero(estimacion?.destinatarios ?? 0)}</b>
                      {categoria === "promocional" && " (excluye bajas, rebotes y suprimidos)"}</>}
                </span>
              </div>
            )}
            <div className="flex justify-end pt-1">
              <Button disabled={!paso1Valido} onClick={() => setPaso(2)} className="bg-emerald-600 hover:bg-emerald-700">
                Siguiente <ArrowRight className="ml-1.5 h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* PASO 2: Asunto */}
      {paso === 2 && (
        <Card>
          <CardContent className="p-4 space-y-3">
            <div className="space-y-1.5">
              <Label>Asunto del correo *</Label>
              <Input value={asunto} onChange={(e) => setAsunto(e.target.value)}
                placeholder="Oficina especial de diciembre para ti"
                onBlur={() => !nombre && setNombre(asunto.trim().slice(0, 60))} />
            </div>
            <div className="space-y-1.5">
              <Label>Nombre interno (opcional)</Label>
              <Input value={nombre} onChange={(e) => setNombre(e.target.value)}
                placeholder="Solo para identificarla en el panel" />
            </div>
            <div className="flex justify-between pt-1">
              <Button variant="outline" onClick={() => setPaso(1)}>Atrás</Button>
              <Button disabled={!paso2Valido} onClick={() => setPaso(3)} className="bg-emerald-600 hover:bg-emerald-700">
                Siguiente <ArrowRight className="ml-1.5 h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* PASO 3: Contenido */}
      {paso === 3 && (
        <Card>
          <CardContent className="p-4 space-y-3">
            <RadioGroup value={modo} onValueChange={(v) => setModo(v as "bloques" | "html")}
              className="flex gap-4">
              <div className="flex items-center space-x-2">
                <RadioGroupItem value="html" id="m-html" />
                <Label htmlFor="m-html" className="font-normal">Pegar HTML</Label>
              </div>
              <div className="flex items-center space-x-2">
                <RadioGroupItem value="bloques" id="m-bloques" disabled={!plantillas?.some((t) => t.mode === "bloques")} />
                <Label htmlFor="m-bloques" className="font-normal">Usar plantilla del editor</Label>
              </div>
            </RadioGroup>

            {modo === "html" ? (
              <div className="space-y-1.5">
                <Label>HTML (los estilos de &lt;style&gt; se convierten automáticamente a inline)</Label>
                <Textarea
                  className="min-h-[220px] font-mono text-xs"
                  value={html} onChange={(e) => setHtml(e.target.value)}
                  placeholder={"<html><head><style>.titulo{color:#e11d48}</style></head><body>…</body></html>"}
                />
                <p className="text-xs text-muted-foreground">
                  Compatible con CSS en &lt;style&gt;: se protege el diseño y se inlinea para clientes de correo.
                  El JavaScript se elimina siempre.
                </p>
              </div>
            ) : (
              <div className="space-y-3">
                <div className="space-y-1.5">
                  <Label>Plantilla</Label>
                  <Select value={templateId} onValueChange={elegirPlantilla}>
                    <SelectTrigger><SelectValue placeholder="Elige una plantilla" /></SelectTrigger>
                    <SelectContent>
                      {plantillas?.filter((t) => t.mode === "bloques").map((t) => (
                        <SelectItem key={t.id} value={t.id}>{t.name}</SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                </div>
                {bloques && bloques.length > 0 && (
                  <div className="rounded-lg border p-3 text-sm">
                    Contenido cargado: <b>{bloques.length} bloques</b>
                    <p className="text-xs text-muted-foreground mt-1">
                      {bloques.map((b) => (b as { type: string }).type).join(" · ")}
                    </p>
                  </div>
                )}
              </div>
            )}
            <div className="flex justify-between pt-1">
              <Button variant="outline" onClick={() => setPaso(2)}>Atrás</Button>
              <Button disabled={!paso3Valido} onClick={() => setPaso(4)} className="bg-emerald-600 hover:bg-emerald-700">
                Siguiente <ArrowRight className="ml-1.5 h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* PASO 4: Envío ahora o programar */}
      {paso === 4 && (
        <Card>
          <CardContent className="p-4 space-y-4">
            <RadioGroup value={momento} onValueChange={(v) => setMomento(v as "ahora" | "programar")}
              className="space-y-3">
              <label className={cn("flex items-start gap-3 rounded-lg border p-3 cursor-pointer",
                momento === "ahora" && "border-emerald-600 bg-emerald-500/5")}>
                <RadioGroupItem value="ahora" id="env-ahora" className="mt-0.5" />
                <div>
                  <Label htmlFor="env-ahora" className="font-medium flex items-center gap-1.5">
                    <Send className="h-4 w-4" /> Enviar ahora
                  </Label>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    La cola procesará los envíos respetando la velocidad de SES.
                  </p>
                </div>
              </label>
              <label className={cn("flex items-start gap-3 rounded-lg border p-3 cursor-pointer",
                momento === "programar" && "border-emerald-600 bg-emerald-500/5")}>
                <RadioGroupItem value="programar" id="env-programar" className="mt-0.5" />
                <div>
                  <Label htmlFor="env-programar" className="font-medium flex items-center gap-1.5">
                    <CalendarClock className="h-4 w-4" /> Programar para más tarde
                  </Label>
                  <p className="text-xs text-muted-foreground mt-0.5">
                    La programación persiste aunque el servidor se reinicie.
                  </p>
                </div>
              </label>
            </RadioGroup>

            {momento === "programar" && (
              <div className="grid sm:grid-cols-2 gap-3">
                <div className="space-y-1.5">
                  <Label>Fecha y hora *</Label>
                  <Input type="datetime-local" value={fechaHora}
                    onChange={(e) => setFechaHora(e.target.value)} />
                </div>
                <div className="space-y-1.5">
                  <Label>Zona horaria</Label>
                  <Select value={zona} onValueChange={setZona}>
                    <SelectTrigger><SelectValue /></SelectTrigger>
                    <SelectContent>
                      {ZONAS.map((z) => <SelectItem key={z} value={z}>{z}</SelectItem>)}
                    </SelectContent>
                  </Select>
                </div>
              </div>
            )}
            <div className="flex justify-between pt-1">
              <Button variant="outline" onClick={() => setPaso(3)}>Atrás</Button>
              <Button disabled={!paso4Valido} onClick={() => setPaso(5)} className="bg-emerald-600 hover:bg-emerald-700">
                Revisar <ArrowRight className="ml-1.5 h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {/* PASO 5: Confirmar */}
      {paso === 5 && (
        <Card>
          <CardContent className="p-4 space-y-3 text-sm">
            <Resumen etiqueta="Asunto" valor={asunto} />
            <Resumen etiqueta="Remitente" valor="Configurado por defecto (notificaciones del sistema)" />
            <Resumen etiqueta="Destinatarios" valor={
              `${listId
                ? listas?.find((l) => l.id === listId)?.name ?? "lista"
                : segmentos?.find((s) => s.id === segmentId)?.name ?? "segmento"
              } · ${numero(estimacion?.destinatarios ?? 0)} elegibles`} />
            <Resumen etiqueta="Contenido" valor={modo === "bloques"
              ? `Plantilla (${bloques?.length ?? 0} bloques)`
              : "HTML propio con CSS inlineado"} />
            <Resumen etiqueta="Envío" valor={momento === "ahora"
              ? "Inmediato"
              : `${fechaHora} (${zona})`} />
            <div className="flex items-center gap-2 rounded-lg bg-emerald-500/10 border border-emerald-500/30 p-3">
              <Users className="h-4 w-4 text-emerald-600 shrink-0" />
              <span>
                Se enviarán <b>{numero(estimacion?.destinatarios ?? 0)}</b> correos
                {momento === "ahora" ? " en cuanto confirmes." : " en la fecha programada."}
              </span>
            </div>
            <div className="flex justify-between pt-3 border-t">
              <Button variant="outline" onClick={() => setPaso(4)} disabled={crearYEnviar.isPending}>Atrás</Button>
              <Button className="bg-emerald-600 hover:bg-emerald-700"
                disabled={crearYEnviar.isPending || !paso4Valido}
                onClick={() => crearYEnviar.mutate()}>
                {crearYEnviar.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {momento === "ahora" ? <Send className="mr-1.5 h-4 w-4" /> : <CalendarClock className="mr-1.5 h-4 w-4" />}
                {momento === "ahora" ? "Confirmar y enviar" : "Confirmar y programar"}
              </Button>
            </div>
          </CardContent>
        </Card>
      )}
    </div>
  );
}

function Resumen({ etiqueta, valor }: { etiqueta: string; valor: string }) {
  return (
    <div className="flex flex-col sm:flex-row justify-between gap-1 border-b pb-2 last:border-0">
      <span className="text-muted-foreground">{etiqueta}</span>
      <span className="font-medium text-right">{valor}</span>
    </div>
  );
}
