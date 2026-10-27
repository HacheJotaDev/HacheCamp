"use client";

/**
 * Editor de bloques para correos HTML.
 * - Paleta de bloques (clic para añadir, arrastrar para reordenar).
 * - Panel de propiedades del bloque seleccionado.
 * - Vista previa escritorio/móvil vía iframe aislado (sanitizada por la API).
 * - Modo HTML fuente, guardado automático, historial de versiones, envío de prueba.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { use } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  DndContext, closestCenter, useSensor, useSensors, PointerSensor,
} from "@dnd-kit/core";
import {
  SortableContext, useSortable, verticalListSortingStrategy, arrayMove,
} from "@dnd-kit/sortable";
import { CSS } from "@dnd-kit/utilities";
import {
  ArrowLeft, Loader2, Eye, Code2, Send, History, GripVertical, Trash2,
  Type, Heading1, Image as ImageIcon, MousePointerClick, Minus, Rows3,
  Copyright, AlignLeft, Smartphone, Monitor, Plus,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Badge } from "@/components/ui/badge";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Separator } from "@/components/ui/separator";
import { cn } from "@/lib/utils";
import { api, EditorBlock, MailConfig, Template, TemplateVersion } from "@/lib/api";

// --------------------------------------------------------------------------- //
// Catálogo de bloques
// --------------------------------------------------------------------------- //

const PALETA: { tipo: string; nombre: string; icon: React.ComponentType<{ className?: string }>; bloque: EditorBlock }[] = [
  { tipo: "encabezado", nombre: "Encabezado", icon: AlignLeft, bloque: { type: "encabezado", props: { text: "Novedades de esta semana", style: "font-size:20px;font-weight:bold;color:#111827;" } } },
  { tipo: "titulo", nombre: "Título", icon: Heading1, bloque: { type: "titulo", props: { text: "Un titular atractivo", level: 2 } } },
  { tipo: "texto", nombre: "Texto", icon: Type, bloque: { type: "texto", props: { content: "<p>Escribe aquí tu contenido. Puedes usar variables como {{contact.first_name}}.</p>" } } },
  { tipo: "imagen", nombre: "Imagen", icon: ImageIcon, bloque: { type: "imagen", props: { src: "https://placehold.co/600x280/e5e7eb/6b7280?text=Imagen", alt: "Imagen" } } },
  { tipo: "boton", nombre: "Botón", icon: MousePointerClick, bloque: { type: "boton", props: { text: "Ver más", href: "https://", bg_color: "#10b981", text_color: "#ffffff", radius: "8px", align: "center" } } },
  { tipo: "columnas", nombre: "Columnas", icon: Rows3, bloque: { type: "columnas", props: { columns: [ { blocks: [{ type: "texto", props: { content: "<p>Columna 1</p>" } }] }, { blocks: [{ type: "texto", props: { content: "<p>Columna 2</p>" } }] } ] } } },
  { tipo: "separador", nombre: "Separador", icon: Minus, bloque: { type: "separador", props: { color: "#e5e7eb" } } },
  { tipo: "espaciador", nombre: "Espacio", icon: Plus, bloque: { type: "espaciador", props: { height: 24 } } },
  { tipo: "pie", nombre: "Pie con baja", icon: Copyright, bloque: { type: "pie", props: { text: "Recibes este correo por tu suscripción.", company: "HJ Campaigns" } } },
];

const VARIABLES = [
  "{{contact.first_name}}", "{{contact.last_name}}", "{{contact.email}}",
  "{{campaign.name}}", "{{unsubscribe_url}}",
];

const ETIQUETAS: Record<string, string> = {
  encabezado: "Encabezado", titulo: "Título", texto: "Texto", imagen: "Imagen",
  boton: "Botón", separador: "Separador", espaciador: "Espacio", columnas: "Columnas", pie: "Pie",
};

// --------------------------------------------------------------------------- //
// Editor rico mínimo (negrita, cursiva, enlace) sobre contentEditable
// --------------------------------------------------------------------------- //

function EditorRico({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (ref.current && ref.current.innerHTML !== value) ref.current.innerHTML = value;
  }, [value]);

  const comando = (cmd: string, arg?: string) => {
    document.execCommand(cmd, false, arg);
    if (ref.current) onChange(ref.current.innerHTML);
  };

  return (
    <div className="rounded-md border overflow-hidden">
      <div className="flex gap-0.5 border-b bg-muted/40 p-1">
        <Button type="button" variant="ghost" size="sm" className="h-7 font-bold" onClick={() => comando("bold")}>B</Button>
        <Button type="button" variant="ghost" size="sm" className="h-7 italic" onClick={() => comando("italic")}>I</Button>
        <Button type="button" variant="ghost" size="sm" className="h-7 underline" onClick={() => comando("underline")}>U</Button>
        <Button type="button" variant="ghost" size="sm" className="h-7 text-xs"
          onClick={() => {
            const url = prompt("URL del enlace (https://…)");
            if (url && /^https?:\/\//i.test(url)) comando("createLink", url);
            else if (url) toast.error("Solo se permiten enlaces http(s)://");
          }}>
          Enlace
        </Button>
        <Button type="button" variant="ghost" size="sm" className="h-7 text-xs" onClick={() => comando("removeFormat")}>Limpiar</Button>
      </div>
      <div
        ref={ref}
        contentEditable
        suppressContentEditableWarning
        className="min-h-[100px] p-2.5 text-sm outline-none focus:ring-1 focus:ring-emerald-600/50"
        onInput={(e) => onChange((e.target as HTMLDivElement).innerHTML)}
      />
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Bloque ordenable
// --------------------------------------------------------------------------- //

function BloqueOrdenable({
  bloque, seleccionado, onSeleccion, onEliminar, index,
}: {
  bloque: EditorBlock; seleccionado: boolean; onSeleccion: () => void;
  onEliminar: () => void; index: number;
}) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({
    id: `bloque-${index}`,
  });
  const style = { transform: CSS.Transform.toString(transform), transition };
  const resumen = (() => {
    const p = bloque.props || {};
    const texto = p.text || p.content || p.alt || p.company || "";
    return String(texto).replace(/<[^>]*>/g, "").slice(0, 60) || ETIQUETAS[bloque.type] || bloque.type;
  })();

  return (
    <div
      ref={setNodeRef}
      style={style}
      className={cn(
        "group flex items-center gap-2 rounded-lg border bg-card px-3 py-2.5 transition-colors cursor-grab active:cursor-grabbing",
        seleccionado ? "border-emerald-600 ring-1 ring-emerald-600/40" : "hover:border-emerald-600/40",
        isDragging && "opacity-50"
      )}
      onClick={onSeleccion}
    >
      <button className="touch-none text-muted-foreground hover:text-foreground" {...attributes} {...listeners} aria-label="Reordenar">
        <GripVertical className="h-4 w-4" />
      </button>
      <div className="flex-1 min-w-0">
        <p className="text-xs font-semibold text-emerald-600">{ETIQUETAS[bloque.type] || bloque.type}</p>
        <p className="text-xs text-muted-foreground truncate">{resumen}</p>
      </div>
      <button
        className="opacity-0 group-hover:opacity-100 text-red-500 hover:text-red-600 p-1"
        onClick={(e) => { e.stopPropagation(); onEliminar(); }}
        aria-label="Eliminar bloque"
      >
        <Trash2 className="h-3.5 w-3.5" />
      </button>
    </div>
  );
}

// --------------------------------------------------------------------------- //
// Página del editor
// --------------------------------------------------------------------------- //

export default function EditorPlantillaPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const qc = useQueryClient();

  const { data: plantilla } = useQuery({
    queryKey: ["plantilla", id],
    queryFn: () => api.get<Template>(`/api/v1/templates/${id}`),
  });

  const [nombre, setNombre] = useState("");
  const [modo, setModo] = useState<"bloques" | "html">("bloques");
  const [bloques, setBloques] = useState<EditorBlock[]>([]);
  const [html, setHtml] = useState("");
  const [seleccion, setSeleccion] = useState<number | null>(null);
  const [sucio, setSucio] = useState(false);
  const [previsualizar, setPrevisualizar] = useState(false);
  const [dispositivo, setDispositivo] = useState<"escritorio" | "movil">("escritorio");
  const [pruebaAbierta, setPruebaAbierta] = useState(false);
  const [versionesAbiertas, setVersionesAbiertas] = useState(false);

  useEffect(() => {
    if (plantilla) {
      setNombre(plantilla.name);
      setModo(plantilla.mode);
      setBloques(plantilla.blocks ?? []);
      setHtml(plantilla.html ?? "");
    }
  }, [plantilla]);

  // Guardado automático (2.5 s tras cambios)
  const guardar = useMutation({
    mutationFn: () => api.put<Template>(`/api/v1/templates/${id}`, {
      name: nombre, mode: modo,
      blocks: modo === "bloques" ? bloques : undefined,
      html: modo === "html" ? html : undefined,
    }),
    onSuccess: (t) => {
      setSucio(false);
      qc.setQueryData(["plantilla", id], t);
      qc.invalidateQueries({ queryKey: ["plantillas"] });
    },
    onError: (e: Error) => toast.error(`Error al guardar: ${e.message}`),
  });

  useEffect(() => {
    if (!sucio || !plantilla || guardar.isPending) return;
    const t = setTimeout(() => guardar.mutate(), 2500);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sucio, bloques, html, nombre, modo]);

  const marcarSucio = useCallback(() => setSucio(true), []);

  const { data: preview } = useQuery({
    queryKey: ["preview", id, modo, bloques, html, dispositivo],
    queryFn: () => api.post<{ html: string }>("/api/v1/templates/preview", {
      mode: modo, blocks: modo === "bloques" ? bloques : undefined, html: modo === "html" ? html : undefined,
    }),
    enabled: previsualizar,
    staleTime: 0,
  });

  const { data: versiones } = useQuery({
    queryKey: ["versiones", id],
    queryFn: () => api.get<TemplateVersion[]>(`/api/v1/templates/${id}/versions`),
    enabled: versionesAbiertas,
  });

  const restaurar = useMutation({
    mutationFn: (v: number) => api.post(`/api/v1/templates/${id}/restore/${v}`),
    onSuccess: () => {
      toast.success("Versión restaurada");
      setVersionesAbiertas(false);
      qc.invalidateQueries({ queryKey: ["plantilla", id] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const enviarPrueba = useMutation({
    // Remitente y asunto los resuelve el backend desde "Configuración de
    // correo" (única fuente de verdad); solo se envía el destinatario.
    mutationFn: (vals: { to_email: string }) =>
      api.post<{ detalle: string }>("/api/v1/templates/test-send", {
        to_email: vals.to_email,
        mode: modo, blocks: modo === "bloques" ? bloques : undefined,
        html: modo === "html" ? html : undefined,
      }),
    onSuccess: (r) => { toast.success(r.detalle); setPruebaAbierta(false); },
    onError: (e: Error) => toast.error(e.message),
  });

  // Reordenar bloques
  const sensors = useSensors(useSensor(PointerSensor, { activationConstraint: { distance: 6 } }));
  const onDragEnd = (ev: any) => {
    const { active, over } = ev;
    if (over && active.id !== over.id) {
      const viejo = Number(String(active.id).replace("bloque-", ""));
      const nuevo = Number(String(over.id).replace("bloque-", ""));
      setBloques((b) => arrayMove(b, viejo, nuevo));
      setSeleccion(null);
      marcarSucio();
    }
  };

  const actualizarBloque = (idx: number, props: Record<string, any>) => {
    setBloques((b) => b.map((bl, i) => (i === idx ? { ...bl, props: { ...bl.props, ...props } } : bl)));
    marcarSucio();
  };

  const insertarVariable = (v: string) => {
    if (seleccion === null) {
      toast.info("Selecciona un bloque de texto o título primero");
      return;
    }
    const bl = bloques[seleccion];
    if (bl.type === "texto") actualizarBloque(seleccion, { content: (bl.props.content || "") + v });
    else if (bl.type === "titulo") actualizarBloque(seleccion, { text: (bl.props.text || "") + v });
    else if (bl.type === "pie") actualizarBloque(seleccion, { text: (bl.props.text || "") + v });
    else toast.info("Ese bloque no admite variables");
  };

  if (!plantilla) {
    return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-muted-foreground" /></div>;
  }

  const sel = seleccion !== null ? bloques[seleccion] : null;

  return (
    <div className="space-y-4">
      {/* Barra superior */}
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="icon" asChild aria-label="Volver">
          <Link href="/panel/plantillas"><ArrowLeft className="h-4 w-4" /></Link>
        </Button>
        <Input
          className="max-w-xs font-semibold" value={nombre}
          onChange={(e) => { setNombre(e.target.value); marcarSucio(); }}
        />
        {sucio ? (
          <Badge variant="outline" className="text-amber-600 border-amber-500/40">Guardando…</Badge>
        ) : (
          <Badge variant="outline" className="text-emerald-600 border-emerald-500/40">Guardado</Badge>
        )}
        <div className="flex-1" />
        <Button variant="outline" size="sm" onClick={() => setModo(modo === "bloques" ? "html" : "bloques")}>
          {modo === "bloques" ? <><Code2 className="mr-1.5 h-4 w-4" /> HTML fuente</> : <><AlignLeft className="mr-1.5 h-4 w-4" /> Editor visual</>}
        </Button>
        <Button variant="outline" size="sm" onClick={() => setVersionesAbiertas(true)}>
          <History className="mr-1.5 h-4 w-4" /> Versiones ({plantilla.version})
        </Button>
        <Button variant="outline" size="sm" onClick={() => setPruebaAbierta(true)}>
          <Send className="mr-1.5 h-4 w-4" /> Prueba
        </Button>
        <Button size="sm" className="bg-emerald-600 hover:bg-emerald-700" onClick={() => setPrevisualizar(true)}>
          <Eye className="mr-1.5 h-4 w-4" /> Vista previa
        </Button>
      </div>

      {modo === "bloques" ? (
        <div className="grid gap-4 lg:grid-cols-[230px_1fr_300px]">
          {/* Paleta */}
          <Card className="p-3 h-fit">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Bloques</p>
            <div className="grid grid-cols-2 lg:grid-cols-1 gap-1.5">
              {PALETA.map((p) => (
                <button
                  key={p.tipo}
                  className="flex items-center gap-2 rounded-lg border px-2.5 py-2 text-xs font-medium hover:border-emerald-600/50 hover:bg-emerald-600/5 transition-colors min-h-[40px]"
                  onClick={() => {
                    setBloques((b) => [...b, JSON.parse(JSON.stringify(p.bloque))]);
                    setSeleccion(bloques.length);
                    marcarSucio();
                  }}
                >
                  <p.icon className="h-3.5 w-3.5 text-emerald-600 shrink-0" />
                  {p.nombre}
                </button>
              ))}
            </div>
            <Separator className="my-3" />
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Variables</p>
            <div className="flex flex-wrap gap-1">
              {VARIABLES.map((v) => (
                <button key={v} className="text-[10px] font-mono rounded bg-muted px-1.5 py-1 hover:bg-emerald-600/15"
                  onClick={() => insertarVariable(v)}>
                  {v}
                </button>
              ))}
            </div>
          </Card>

          {/* Estructura del correo */}
          <Card className="p-3">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">
              Estructura ({bloques.length} bloques)
            </p>
            {bloques.length === 0 ? (
              <div className="rounded-lg border-2 border-dashed p-8 text-center text-sm text-muted-foreground">
                Añade bloques desde la paleta. El correo se construye de arriba hacia abajo.
              </div>
            ) : (
              <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
                <SortableContext items={bloques.map((_, i) => `bloque-${i}`)} strategy={verticalListSortingStrategy}>
                  <div className="space-y-1.5">
                    {bloques.map((b, i) => (
                      <BloqueOrdenable
                        key={`${i}-${b.type}`} index={i} bloque={b}
                        seleccionado={seleccion === i}
                        onSeleccion={() => setSeleccion(i)}
                        onEliminar={() => {
                          setBloques((bs) => bs.filter((_, j) => j !== i));
                          setSeleccion(null);
                          marcarSucio();
                        }}
                      />
                    ))}
                  </div>
                </SortableContext>
              </DndContext>
            )}
          </Card>

          {/* Propiedades */}
          <Card className="p-3 h-fit">
            <p className="text-xs font-semibold text-muted-foreground uppercase tracking-wide mb-2">Propiedades</p>
            {!sel ? (
              <p className="text-sm text-muted-foreground">Selecciona un bloque para editar sus propiedades.</p>
            ) : (
              <PropiedadesBloque bloque={sel} onChange={(props) => actualizarBloque(seleccion!, props)} />
            )}
          </Card>
        </div>
      ) : (
        <Card className="p-4">
          <p className="text-xs text-muted-foreground mb-2">
            HTML fuente. Se valida la sintaxis Jinja2 y se sanitiza al guardar (sin JavaScript).
            Variables: {VARIABLES.join(" · ")}
          </p>
          <Textarea
            className="font-mono text-xs min-h-[480px]"
            value={html}
            onChange={(e) => { setHtml(e.target.value); marcarSucio(); }}
            spellCheck={false}
          />
        </Card>
      )}

      {/* Vista previa */}
      <Dialog open={previsualizar} onOpenChange={setPrevisualizar}>
        <DialogContent className="max-w-4xl max-h-[92vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle className="flex items-center gap-2">
              Vista previa
              <div className="flex gap-1 ml-2">
                <Button variant={dispositivo === "escritorio" ? "secondary" : "ghost"} size="sm" onClick={() => setDispositivo("escritorio")}>
                  <Monitor className="h-4 w-4" />
                </Button>
                <Button variant={dispositivo === "movil" ? "secondary" : "ghost"} size="sm" onClick={() => setDispositivo("movil")}>
                  <Smartphone className="h-4 w-4" />
                </Button>
              </div>
            </DialogTitle>
            <DialogDescription>
              Renderizado por la API con datos de ejemplo y sanitización (sin JavaScript). Se abre en iframe aislado.
            </DialogDescription>
          </DialogHeader>
          <div className={cn("mx-auto bg-white", dispositivo === "movil" ? "w-[375px] max-w-full" : "w-full max-w-[640px]")}>
            {preview ? (
              <iframe
                title="Vista previa del correo"
                sandbox=""
                srcDoc={preview.html}
                className="w-full h-[520px] border rounded-lg bg-white"
              />
            ) : (
              <div className="flex justify-center py-20"><Loader2 className="h-7 w-7 animate-spin text-muted-foreground" /></div>
            )}
          </div>
        </DialogContent>
      </Dialog>

      {/* Envío de prueba */}
      <DialogoPrueba
        abierto={pruebaAbierta} onCerrar={() => setPruebaAbierta(false)}
        onEnviar={(v) => enviarPrueba.mutate(v)} pendiente={enviarPrueba.isPending}
      />

      {/* Versiones */}
      <Dialog open={versionesAbiertas} onOpenChange={setVersionesAbiertas}>
        <DialogContent className="max-w-md max-h-[85vh] overflow-y-auto">
          <DialogHeader>
            <DialogTitle>Historial de versiones</DialogTitle>
            <DialogDescription>Cada guardado con cambios de contenido crea una versión restaurable.</DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            {versiones?.map((v) => (
              <div key={v.id} className="flex items-center justify-between rounded-lg border p-2.5 text-sm">
                <div>
                  <p className="font-medium">Versión {v.version}</p>
                  <p className="text-xs text-muted-foreground">{new Date(v.created_at).toLocaleString("es")}</p>
                </div>
                <Button variant="outline" size="sm" onClick={() => restaurar.mutate(v.version)}>Restaurar</Button>
              </div>
            ))}
          </div>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function DialogoPrueba({
  abierto, onCerrar, onEnviar, pendiente,
}: {
  abierto: boolean; onCerrar: () => void;
  onEnviar: (v: { to_email: string }) => void;
  pendiente: boolean;
}) {
  const [to, setTo] = useState("");
  // Remitente y asunto: únicos y globales (solo lectura), desde el backend.
  const { data: correoConfig } = useQuery({
    queryKey: ["mail-config"],
    queryFn: () => api.get<MailConfig>("/api/v1/system/mail-config"),
    staleTime: 0, refetchOnMount: "always", enabled: abierto,
  });
  const valido = to.includes("@") && /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(to);
  return (
    <Dialog open={abierto} onOpenChange={(v) => !v && onCerrar()}>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Enviar prueba</DialogTitle>
          <DialogDescription>
            Se envía realmente mediante Amazon SES. Usa direcciones autorizadas si la cuenta
            está en entorno sandbox.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label>Destinatario *</Label>
            <Input type="email" value={to} onChange={(e) => setTo(e.target.value)} placeholder="tu@correo.com" />
            <p className="text-xs text-muted-foreground">El destinatario es independiente y editable.</p>
          </div>
          <div className="rounded-lg border bg-muted/30 p-3 text-sm space-y-1">
            <p>
              <span className="text-muted-foreground">De:</span>{" "}
              <b>{correoConfig?.from_name ?? "…"}</b>{" "}
              <span className="text-muted-foreground">&lt;{correoConfig?.from_email ?? "…"}&gt;</span>
            </p>
            <p>
              <span className="text-muted-foreground">Asunto:</span> {correoConfig?.subject || "—"}
            </p>
            <p className="text-xs text-muted-foreground">
              Valores globales de Configuración de correo; se aplican en el momento del envío.
            </p>
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={onCerrar}>Cancelar</Button>
          <Button className="bg-emerald-600 hover:bg-emerald-700" disabled={!valido || pendiente} onClick={() => onEnviar({ to_email: to })}>
            {pendiente && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Enviar prueba
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

// --------------------------------------------------------------------------- //
// Panel de propiedades por tipo de bloque
// --------------------------------------------------------------------------- //

function PropiedadesBloque({ bloque, onChange }: { bloque: EditorBlock; onChange: (props: Record<string, any>) => void }) {
  const p = bloque.props || {};
  const set = (k: string) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement>) =>
    onChange({ [k]: e.target.value });

  switch (bloque.type) {
    case "encabezado":
      return (
        <div className="space-y-3">
          <Campo label="Texto"><Input value={p.text || ""} onChange={set("text")} /></Campo>
          <Campo label="Estilo CSS"><Input value={p.style || ""} onChange={set("style")} className="font-mono text-xs" /></Campo>
        </div>
      );
    case "titulo":
      return (
        <div className="space-y-3">
          <Campo label="Texto"><Input value={p.text || ""} onChange={set("text")} /></Campo>
          <Campo label="Nivel">
            <div className="flex gap-1">
              {[1, 2, 3].map((n) => (
                <button key={n} className={cn("px-3 py-1.5 rounded border text-sm min-h-[36px]",
                  (p.level || 2) === n ? "bg-emerald-600 text-white border-emerald-600" : "")}
                  onClick={() => onChange({ level: n })}>H{n}</button>
              ))}
            </div>
          </Campo>
        </div>
      );
    case "texto":
      return <EditorRico value={p.content || ""} onChange={(v) => onChange({ content: v })} />;
    case "imagen":
      return (
        <div className="space-y-3">
          <Campo label="URL de la imagen (https://)"><Input value={p.src || ""} onChange={set("src")} placeholder="https://…" /></Campo>
          <Campo label="Texto alternativo"><Input value={p.alt || ""} onChange={set("alt")} /></Campo>
        </div>
      );
    case "boton":
      return (
        <div className="space-y-3">
          <Campo label="Texto"><Input value={p.text || ""} onChange={set("text")} /></Campo>
          <Campo label="Enlace (https://)"><Input value={p.href || ""} onChange={set("href")} placeholder="https://…" /></Campo>
          <div className="grid grid-cols-2 gap-2">
            <Campo label="Fondo"><Input type="color" value={p.bg_color || "#10b981"} onChange={set("bg_color")} className="h-10" /></Campo>
            <Campo label="Texto"><Input type="color" value={p.text_color || "#ffffff"} onChange={set("text_color")} className="h-10" /></Campo>
          </div>
          <Campo label="Alineación">
            <div className="flex gap-1">
              {["left", "center", "right"].map((a) => (
                <button key={a} className={cn("px-3 py-1.5 rounded border text-xs capitalize min-h-[34px]",
                  (p.align || "center") === a ? "bg-emerald-600 text-white border-emerald-600" : "")}
                  onClick={() => onChange({ align: a })}>{a === "left" ? "Izq." : a === "center" ? "Centro" : "Der."}</button>
              ))}
            </div>
          </Campo>
        </div>
      );
    case "separador":
      return <Campo label="Color"><Input type="color" value={p.color || "#e5e7eb"} onChange={set("color")} className="h-10" /></Campo>;
    case "espaciador":
      return <Campo label="Altura (px)"><Input type="number" min={4} max={200} value={p.height || 24} onChange={(e) => onChange({ height: Number(e.target.value) })} /></Campo>;
    case "pie":
      return (
        <div className="space-y-3">
          <Campo label="Texto">
            <Textarea rows={2} value={p.text || ""} onChange={set("text")} />
          </Campo>
          <Campo label="Empresa"><Input value={p.company || ""} onChange={set("company")} /></Campo>
          <p className="text-xs text-muted-foreground">
            El enlace de cancelación ({"{{unsubscribe_url}}"}) se añade automáticamente.
          </p>
        </div>
      );
    case "columnas":
      return (
        <div className="space-y-2 text-sm text-muted-foreground">
          <p>El bloque de columnas contiene sub-bloques de texto por columna ({p.columns?.length ?? 0} columnas).</p>
          {p.columns?.map((col: any, ci: number) => (
            <div key={ci} className="rounded border p-2">
              <p className="text-xs font-medium text-foreground mb-1">Columna {ci + 1}</p>
              <Textarea rows={2} className="text-xs"
                value={col.blocks?.[0]?.props?.content || ""}
                onChange={(e) => {
                  const columns = JSON.parse(JSON.stringify(p.columns || []));
                  columns[ci].blocks = [{ type: "texto", props: { content: e.target.value } }];
                  onChange({ columns });
                }} />
            </div>
          ))}
        </div>
      );
    default:
      return <p className="text-sm text-muted-foreground">Bloque sin propiedades editables.</p>;
  }
}

function Campo({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="space-y-1.5">
      <Label className="text-xs">{label}</Label>
      {children}
    </div>
  );
}
