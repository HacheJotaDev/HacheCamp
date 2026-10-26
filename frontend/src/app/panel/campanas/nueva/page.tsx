"use client";

import { useState } from "react";
import Link from "next/link";
import { useMutation, useQuery } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Loader2, ArrowRight, CheckCircle2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, Campaign, ContactList, Segment, Template } from "@/lib/api";
import { cn } from "@/lib/utils";

export default function NuevaCampanaPage() {
  const [paso, setPaso] = useState(1);
  const [form, setForm] = useState({
    name: "", subject: "", from_name: "HJ Leisure Parts",
    from_email: "notificaciones@leisureparts4u.store", reply_to: "",
    template_id: "", list_id: "", segment_id: "",
    category: "promocional", scheduled_at: "", timezone: "UTC",
  });
  const [modo, setModo] = useState<"bloques" | "html">("bloques");
  const [bloques, setBloques] = useState<any[]>([]);
  const [html, setHtml] = useState("");

  const { data: plantillas } = useQuery({
    queryKey: ["plantillas"], queryFn: () => api.get<Template[]>("/api/v1/templates"),
  });
  const { data: listas } = useQuery({
    queryKey: ["listas"], queryFn: () => api.get<ContactList[]>("/api/v1/lists"),
  });
  const { data: segmentos } = useQuery({
    queryKey: ["segmentos"], queryFn: () => api.get<Segment[]>("/api/v1/segments"),
  });

  // Carga el contenido de la plantilla elegida (al seleccionar, no en efecto)
  const elegirPlantilla = (id: string) => {
    setForm((f) => ({ ...f, template_id: id }));
    const t = plantillas?.find((p) => p.id === id);
    if (t) {
      setModo(t.mode);
      setBloques(t.blocks ?? []);
      setHtml(t.html ?? "");
    }
  };

  const { data: estimacion } = useQuery({
    queryKey: ["estimacion", form.list_id, form.segment_id],
    queryFn: () => {
      const qs = form.list_id ? `list_id=${form.list_id}` : `segment_id=${form.segment_id}`;
      // La estimación se calcula en el servidor tras crear la campaña;
      // aquí mostramos advertencia si no hay destinatarios
      return Promise.resolve({ nota: qs });
    },
    enabled: false,
  });

  const crear = useMutation({
    mutationFn: () => api.post<Campaign>("/api/v1/campaigns", {
      name: form.name,
      subject: form.subject,
      from_name: form.from_name,
      from_email: form.from_email,
      reply_to: form.reply_to || null,
      template_id: form.template_id || null,
      mode: modo,
      blocks: modo === "bloques" ? bloques : null,
      html: modo === "html" ? html : null,
      category: form.category,
      list_id: form.list_id || null,
      segment_id: form.segment_id || null,
      scheduled_at: form.scheduled_at ? new Date(form.scheduled_at).toISOString() : null,
      timezone: form.timezone,
    }),
    onSuccess: (c) => {
      toast.success("Campaña creada en borrador");
      window.location.href = `/panel/campanas/${c.id}`;
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const paso1Valido = form.name.trim().length >= 2 && form.subject.trim() && form.from_email.includes("@");
  const paso2Valido = modo === "bloques" ? bloques.length > 0 : html.trim().length > 0;
  const paso3Valido = Boolean(form.list_id || form.segment_id);

  return (
    <div className="space-y-4 max-w-3xl">
      <div className="flex items-center gap-3">
        <Button variant="ghost" size="icon" asChild aria-label="Volver">
          <Link href="/panel/campanas"><ArrowLeft className="h-4 w-4" /></Link>
        </Button>
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Nueva campaña</h1>
          <p className="text-sm text-muted-foreground">Se creará en estado borrador; requiere aprobación administrativa antes del envío.</p>
        </div>
      </div>

      {/* Indicador de pasos */}
      <div className="flex items-center gap-2 text-sm">
        {["Datos", "Contenido", "Destinatarios", "Revisar"].map((l, i) => (
          <div key={l} className="flex items-center gap-2">
            <div className={cn(
              "h-7 w-7 rounded-full flex items-center justify-center text-xs font-bold border-2",
              paso > i + 1 ? "bg-emerald-600 border-emerald-600 text-white"
                : paso === i + 1 ? "border-emerald-600 text-emerald-600" : "border-muted text-muted-foreground")}>
              {paso > i + 1 ? <CheckCircle2 className="h-4 w-4" /> : i + 1}
            </div>
            <span className={cn(paso === i + 1 ? "font-semibold" : "text-muted-foreground", "hidden sm:block")}>{l}</span>
            {i < 3 && <div className="h-px w-6 sm:w-10 bg-border" />}
          </div>
        ))}
      </div>

      {paso === 1 && (
        <Card>
          <CardContent className="p-4 space-y-3">
            <div className="space-y-1.5">
              <Label>Nombre interno *</Label>
              <Input value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })}
                placeholder="p. ej. Promoción diciembre 2026" />
            </div>
            <div className="space-y-1.5">
              <Label>Asunto (admite variables) *</Label>
              <Input value={form.subject} onChange={(e) => setForm({ ...form, subject: e.target.value })}
                placeholder="Hola {{contact.first_name}}, esto te interesa" />
            </div>
            <div className="grid sm:grid-cols-2 gap-3">
              <div className="space-y-1.5">
                <Label>Nombre del remitente</Label>
                <Input value={form.from_name} onChange={(e) => setForm({ ...form, from_name: e.target.value })} />
              </div>
              <div className="space-y-1.5">
                <Label>Correo del remitente (verificado en SES)</Label>
                <Input type="email" value={form.from_email} onChange={(e) => setForm({ ...form, from_email: e.target.value })} />
              </div>
            </div>
            <div className="space-y-1.5">
              <Label>Responder a (opcional)</Label>
              <Input type="email" value={form.reply_to} onChange={(e) => setForm({ ...form, reply_to: e.target.value })}
                placeholder="soporte@leisureparts4u.store" />
            </div>
            <div className="space-y-1.5">
              <Label>Categoría</Label>
              <Select value={form.category} onValueChange={(v) => setForm({ ...form, category: v })}>
                <SelectTrigger><SelectValue /></SelectTrigger>
                <SelectContent>
                  <SelectItem value="promocional">Promocional (exige consentimiento + baja funcional)</SelectItem>
                  <SelectItem value="transaccional">Transaccional (avisos de servicio, confirmaciones)</SelectItem>
                </SelectContent>
              </Select>
            </div>
            <div className="flex justify-end pt-2">
              <Button disabled={!paso1Valido} onClick={() => setPaso(2)} className="bg-emerald-600 hover:bg-emerald-700">
                Siguiente <ArrowRight className="ml-1.5 h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {paso === 2 && (
        <Card>
          <CardContent className="p-4 space-y-3">
            <div className="space-y-1.5">
              <Label>Plantilla base</Label>
              <Select value={form.template_id} onValueChange={elegirPlantilla}>
                <SelectTrigger><SelectValue placeholder="Elige una plantilla" /></SelectTrigger>
                <SelectContent>
                  {plantillas?.map((t) => (
                    <SelectItem key={t.id} value={t.id}>{t.name} ({t.mode === "bloques" ? "Bloques" : "HTML"})</SelectItem>
                  ))}
                </SelectContent>
              </Select>
              <p className="text-xs text-muted-foreground">
                El contenido se copia a la campaña; la plantilla original no se modifica. Puedes editar bloques después en la campaña.
              </p>
            </div>
            {modo === "bloques" && bloques.length > 0 && (
              <div className="rounded-lg border p-3 text-sm space-y-1">
                <p className="font-medium">Contenido cargado: {bloques.length} bloques</p>
                <p className="text-xs text-muted-foreground">{bloques.map((b) => b.type).join(" · ")}</p>
              </div>
            )}
            {modo === "html" && (
              <div className="space-y-1.5">
                <Label>HTML fuente</Label>
                <textarea
                  className="w-full min-h-[200px] rounded-md border bg-background p-2 font-mono text-xs"
                  value={html} onChange={(e) => setHtml(e.target.value)}
                  placeholder="<html>…</html>"
                />
              </div>
            )}
            <div className="flex justify-between pt-2">
              <Button variant="outline" onClick={() => setPaso(1)}>Atrás</Button>
              <Button disabled={!paso2Valido} onClick={() => setPaso(3)} className="bg-emerald-600 hover:bg-emerald-700">
                Siguiente <ArrowRight className="ml-1.5 h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {paso === 3 && (
        <Card>
          <CardContent className="p-4 space-y-3">
            <div className="space-y-1.5">
              <Label>Lista de destinatarios</Label>
              <Select value={form.list_id} onValueChange={(v) => setForm({ ...form, list_id: v, segment_id: "" })}>
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
              <Select value={form.segment_id} onValueChange={(v) => setForm({ ...form, segment_id: v, list_id: "" })}>
                <SelectTrigger><SelectValue placeholder="Elige un segmento" /></SelectTrigger>
                <SelectContent>
                  {segmentos?.map((s) => (
                    <SelectItem key={s.id} value={s.id}>{s.name}</SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
            {form.category === "promocional" && (
              <p className="text-xs text-amber-600 bg-amber-500/10 rounded-md p-2.5">
                Solo se enviará a contactos con estado «suscripto» y consentimiento válido.
                Los suprimidos, rebotes y bajas se excluyen automáticamente.
              </p>
            )}
            <div className="flex justify-between pt-2">
              <Button variant="outline" onClick={() => setPaso(2)}>Atrás</Button>
              <Button disabled={!paso3Valido} onClick={() => setPaso(4)} className="bg-emerald-600 hover:bg-emerald-700">
                Siguiente <ArrowRight className="ml-1.5 h-4 w-4" />
              </Button>
            </div>
          </CardContent>
        </Card>
      )}

      {paso === 4 && (
        <Card>
          <CardContent className="p-4 space-y-3 text-sm">
            <Resumen etiqueta="Nombre" valor={form.name} />
            <Resumen etiqueta="Asunto" valor={form.subject} />
            <Resumen etiqueta="Remitente" valor={`${form.from_name} <${form.from_email}>`} />
            <Resumen etiqueta="Categoría" valor={form.category} />
            <Resumen etiqueta="Contenido" valor={`${modo === "bloques" ? `${bloques.length} bloques` : "HTML fuente"} (${plantillas?.find((t) => t.id === form.template_id)?.name ?? "personalizado"})`} />
            <Resumen etiqueta="Destinatarios" valor={
              form.list_id ? listas?.find((l) => l.id === form.list_id)?.name ?? "—" : segmentos?.find((s) => s.id === form.segment_id)?.name ?? "—"} />
            <div className="flex justify-between pt-3 border-t">
              <Button variant="outline" onClick={() => setPaso(3)}>Atrás</Button>
              <Button className="bg-emerald-600 hover:bg-emerald-700"
                disabled={crear.isPending} onClick={() => crear.mutate()}>
                {crear.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                Crear campaña
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
