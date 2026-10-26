"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import {
  Users, Plus, Upload, Download, Search, Loader2, FileSpreadsheet, Trash2, Eye,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Textarea } from "@/components/ui/textarea";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Skeleton } from "@/components/ui/skeleton";
import { Checkbox } from "@/components/ui/checkbox";
import { api, Contact, ContactList, Paged, Tag } from "@/lib/api";
import { EmptyState, StatusBadge, fecha } from "@/components/shared";

export default function ContactosPage() {
  const [q, setQ] = useState("");
  const [estado, setEstado] = useState("todos");
  const [listId, setListId] = useState("todas");
  const [page, setPage] = useState(1);
  const qc = useQueryClient();

  const filtros = useMemo(() => {
    const p = new URLSearchParams({ page: String(page), per_page: "25" });
    if (q) p.set("q", q);
    if (estado !== "todos") p.set("estado", estado);
    if (listId !== "todas") p.set("list_id", listId);
    return p.toString();
  }, [q, estado, listId, page]);

  const { data, isLoading } = useQuery({
    queryKey: ["contactos", filtros],
    queryFn: () => api.get<Paged<Contact>>(`/api/v1/contacts?${filtros}`),
  });

  const { data: listas } = useQuery({
    queryKey: ["listas"],
    queryFn: () => api.get<ContactList[]>("/api/v1/lists"),
  });

  return (
    <div className="space-y-4">
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3">
        <div>
          <h1 className="text-2xl font-bold tracking-tight">Contactos</h1>
          <p className="text-sm text-muted-foreground">
            Gestión de suscriptores con consentimiento, listas y etiquetas
          </p>
        </div>
        <div className="flex flex-wrap gap-2">
          <ImportarDialog onImportado={() => qc.invalidateQueries({ queryKey: ["contactos"] })} />
          <Button variant="outline" asChild>
            <a href="/api/v1/contacts/export/csv" download><Download className="mr-2 h-4 w-4" /> Exportar</a>
          </Button>
          <NuevoContactoDialog onCreado={() => qc.invalidateQueries({ queryKey: ["contactos"] })} />
        </div>
      </div>

      <Card>
        <CardContent className="p-4 space-y-4">
          <div className="flex flex-col sm:flex-row gap-2">
            <div className="relative flex-1">
              <Search className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
              <Input
                placeholder="Buscar por correo o nombre…"
                className="pl-9"
                value={q}
                onChange={(e) => { setQ(e.target.value); setPage(1); }}
              />
            </div>
            <Select value={estado} onValueChange={(v) => { setEstado(v); setPage(1); }}>
              <SelectTrigger className="w-full sm:w-44"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="todos">Todos los estados</SelectItem>
                <SelectItem value="suscripto">Suscripto</SelectItem>
                <SelectItem value="dado_baja">Dado de baja</SelectItem>
                <SelectItem value="rebote">Rebote</SelectItem>
                <SelectItem value="queja">Queja</SelectItem>
              </SelectContent>
            </Select>
            <Select value={listId} onValueChange={(v) => { setListId(v); setPage(1); }}>
              <SelectTrigger className="w-full sm:w-44"><SelectValue /></SelectTrigger>
              <SelectContent>
                <SelectItem value="todas">Todas las listas</SelectItem>
                {listas?.map((l) => <SelectItem key={l.id} value={l.id}>{l.name}</SelectItem>)}
              </SelectContent>
            </Select>
          </div>

          {isLoading ? (
            <div className="space-y-2">{Array.from({ length: 6 }).map((_, i) => <Skeleton key={i} className="h-12" />)}</div>
          ) : !data || data.items.length === 0 ? (
            <EmptyState
              icon={Users}
              titulo={q || estado !== "todos" ? "Sin resultados" : "Aún no hay contactos"}
              descripcion={q || estado !== "todos"
                ? "Prueba con otros criterios de búsqueda."
                : "Importa tu primera lista CSV con consentimiento documentado o crea un contacto manualmente."}
            />
          ) : (
            <>
              <div className="overflow-x-auto -mx-4 px-4">
                <Table>
                  <TableHeader>
                    <TableRow>
                      <TableHead>Correo</TableHead>
                      <TableHead className="hidden sm:table-cell">Nombre</TableHead>
                      <TableHead>Estado</TableHead>
                      <TableHead className="hidden md:table-cell">Consentimiento</TableHead>
                      <TableHead className="hidden md:table-cell">Alta</TableHead>
                      <TableHead className="text-right">Acciones</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    {data.items.map((c) => (
                      <TableRow key={c.id}>
                        <TableCell>
                          <Link href={`/panel/contactos/${c.id}`} className="font-medium hover:text-emerald-600 hover:underline">
                            {c.email}
                          </Link>
                        </TableCell>
                        <TableCell className="hidden sm:table-cell text-muted-foreground">
                          {[c.first_name, c.last_name].filter(Boolean).join(" ") || "—"}
                        </TableCell>
                        <TableCell><StatusBadge estado={c.status} /></TableCell>
                        <TableCell className="hidden md:table-cell text-sm text-muted-foreground">
                          {c.consent_source || "—"}
                        </TableCell>
                        <TableCell className="hidden md:table-cell text-sm text-muted-foreground">{fecha(c.created_at)}</TableCell>
                        <TableCell className="text-right">
                          <Button variant="ghost" size="icon" asChild aria-label="Ver detalle">
                            <Link href={`/panel/contactos/${c.id}`}><Eye className="h-4 w-4" /></Link>
                          </Button>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </div>
              <div className="flex items-center justify-between text-sm text-muted-foreground">
                <span>{data.total} contacto(s) en total</span>
                <div className="flex items-center gap-2">
                  <Button variant="outline" size="sm" disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
                    Anterior
                  </Button>
                  <span>Página {data.page}</span>
                  <Button variant="outline" size="sm" disabled={page * data.per_page >= data.total} onClick={() => setPage((p) => p + 1)}>
                    Siguiente
                  </Button>
                </div>
              </div>
            </>
          )}
        </CardContent>
      </Card>
    </div>
  );
}

function NuevoContactoDialog({ onCreado }: { onCreado: () => void }) {
  const [abierto, setAbierto] = useState(false);
  const [form, setForm] = useState({ email: "", first_name: "", last_name: "", consent_source: "", consent_text: "" });

  const crear = useMutation({
    mutationFn: () => api.post("/api/v1/contacts", {
      email: form.email,
      first_name: form.first_name || null,
      last_name: form.last_name || null,
      consent_source: form.consent_source,
      consent_text: form.consent_text || null,
    }),
    onSuccess: () => {
      toast.success("Contacto creado");
      setAbierto(false);
      setForm({ email: "", first_name: "", last_name: "", consent_source: "", consent_text: "" });
      onCreado();
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const valido = form.email.includes("@") && form.consent_source.trim().length >= 2;

  return (
    <Dialog open={abierto} onOpenChange={setAbierto}>
      <Button onClick={() => setAbierto(true)} className="bg-emerald-600 hover:bg-emerald-700">
        <Plus className="mr-2 h-4 w-4" /> Nuevo contacto
      </Button>
      <DialogContent>
        <DialogHeader>
          <DialogTitle>Nuevo contacto</DialogTitle>
          <DialogDescription>
            El consentimiento es obligatorio para enviar campañas promocionales.
          </DialogDescription>
        </DialogHeader>
        <div className="space-y-3">
          <div className="space-y-1.5">
            <Label htmlFor="c-email">Correo electrónico *</Label>
            <Input id="c-email" type="email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="c-nombre">Nombre</Label>
              <Input id="c-nombre" value={form.first_name} onChange={(e) => setForm({ ...form, first_name: e.target.value })} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="c-apellido">Apellido</Label>
              <Input id="c-apellido" value={form.last_name} onChange={(e) => setForm({ ...form, last_name: e.target.value })} />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="c-consent">Fuente del consentimiento *</Label>
            <Input id="c-consent" placeholder="p. ej. formulario web, tienda, presencial"
              value={form.consent_source} onChange={(e) => setForm({ ...form, consent_source: e.target.value })} />
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="c-texto">Texto del consentimiento</Label>
            <Textarea id="c-texto" rows={2} placeholder="Texto exacto que la persona aceptó"
              value={form.consent_text} onChange={(e) => setForm({ ...form, consent_text: e.target.value })} />
          </div>
        </div>
        <DialogFooter>
          <Button variant="outline" onClick={() => setAbierto(false)}>Cancelar</Button>
          <Button className="bg-emerald-600 hover:bg-emerald-700" disabled={!valido || crear.isPending} onClick={() => crear.mutate()}>
            {crear.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            Crear contacto
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  );
}

interface ImportPreview {
  encabezados: string[];
  codificacion: string;
  filas_validas: number;
  filas_invalidas: number;
  muestra: { email: string; first_name?: string; last_name?: string }[];
}

function ImportarDialog({ onImportado }: { onImportado: () => void }) {
  const [abierto, setAbierto] = useState(false);
  const [archivo, setArchivo] = useState<File | null>(null);
  const [consentSource, setConsentSource] = useState("");
  const [consentText, setConsentText] = useState("");
  const [simulacion, setSimulacion] = useState(true);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [resultado, setResultado] = useState<Record<string, unknown> | null>(null);

  const previewMut = useMutation({
    mutationFn: async () => {
      const fd = new FormData();
      fd.append("archivo", archivo!);
      return api.post<ImportPreview>("/api/v1/contacts/import/preview", fd);
    },
    onSuccess: setPreview,
    onError: (e: Error) => toast.error(e.message),
  });

  const importar = useMutation({
    mutationFn: async () => {
      const fd = new FormData();
      fd.append("archivo", archivo!);
      fd.append("consent_source", consentSource);
      fd.append("consent_text", consentText);
      fd.append("dry_run", String(simulacion));
      return api.post<Record<string, unknown>>("/api/v1/contacts/import", fd);
    },
    onSuccess: (res) => {
      setResultado(res);
      if (!simulacion) {
        toast.success(`Importación completada: ${res.creados} creados, ${res.actualizados} actualizados`);
        onImportado();
      }
    },
    onError: (e: Error) => toast.error(e.message),
  });

  const reiniciar = () => {
    setArchivo(null); setPreview(null); setResultado(null); setConsentSource(""); setConsentText(""); setSimulacion(true);
  };

  return (
    <Dialog open={abierto} onOpenChange={(v) => { setAbierto(v); if (!v) reiniciar(); }}>
      <Button variant="outline" onClick={() => setAbierto(true)}>
        <Upload className="mr-2 h-4 w-4" /> Importar CSV
      </Button>
      <DialogContent className="max-w-lg max-h-[90vh] overflow-y-auto">
        <DialogHeader>
          <DialogTitle>Importar contactos desde CSV</DialogTitle>
          <DialogDescription>
            Formatos: UTF-8 o Latin-1. Columna obligatoria: <code>email</code>. Máximo 5 MB.
            Prohibido importar listas compradas o sin autorización.
          </DialogDescription>
        </DialogHeader>

        {!resultado ? (
          <div className="space-y-3">
            <div className="space-y-1.5">
              <Label htmlFor="csv-archivo">Archivo CSV *</Label>
              <Input
                id="csv-archivo" type="file" accept=".csv,text/csv"
                onChange={(e) => { setArchivo(e.target.files?.[0] ?? null); setPreview(null); }}
              />
            </div>
            {archivo && !preview && (
              <Button variant="outline" size="sm" className="w-full" disabled={previewMut.isPending}
                onClick={() => previewMut.mutate()}>
                {previewMut.isPending ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : <FileSpreadsheet className="mr-2 h-4 w-4" />}
                Analizar archivo
              </Button>
            )}
            {preview && (
              <div className="rounded-lg border p-3 text-sm space-y-1">
                <p className="font-medium">Vista previa</p>
                <p className="text-muted-foreground">
                  Codificación: {preview.codificacion} · Filas válidas: <b>{preview.filas_validas}</b> ·
                  Inválidas: <b>{preview.filas_invalidas}</b>
                </p>
                <p className="text-muted-foreground">Columnas detectadas: {preview.encabezados.join(", ")}</p>
                <div className="mt-1 text-xs font-mono bg-muted rounded p-2 max-h-24 overflow-y-auto">
                  {preview.muestra.map((m) => m.email).join(" · ")}
                </div>
              </div>
            )}
            <div className="space-y-1.5">
              <Label htmlFor="csv-consent">Fuente del consentimiento *</Label>
              <Input id="csv-consent" placeholder="p. ej. formulario web 2025"
                value={consentSource} onChange={(e) => setConsentSource(e.target.value)} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="csv-texto">Texto del consentimiento</Label>
              <Textarea id="csv-texto" rows={2} value={consentText} onChange={(e) => setConsentText(e.target.value)} />
            </div>
            <label className="flex items-center gap-2 text-sm">
              <Checkbox checked={simulacion} onCheckedChange={(v) => setSimulacion(Boolean(v))} />
              Simulación (no guarda nada; muestra el resultado esperado)
            </label>
            <DialogFooter>
              <Button variant="outline" onClick={() => setAbierto(false)}>Cancelar</Button>
              <Button
                className="bg-emerald-600 hover:bg-emerald-700"
                disabled={!archivo || !preview || consentSource.trim().length < 2 || importar.isPending}
                onClick={() => importar.mutate()}
              >
                {importar.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                {simulacion ? "Ejecutar simulación" : "Importar de verdad"}
              </Button>
            </DialogFooter>
          </div>
        ) : (
          <div className="space-y-3">
            <div className="grid grid-cols-2 gap-2 text-sm">
              {[
                ["Filas totales", resultado.total_filas],
                ["Válidas", resultado.filas_validas],
                ["Inválidas", resultado.filas_invalidas],
                ["Creados", resultado.creados],
                ["Actualizados", resultado.actualizados],
                ["Duplicados en archivo", resultado.duplicados_en_archivo],
              ].map(([k, v]) => (
                <div key={String(k)} className="rounded-lg bg-muted/60 p-2.5">
                  <p className="text-muted-foreground text-xs">{k}</p>
                  <p className="font-bold text-base">{String(v)}</p>
                </div>
              ))}
            </div>
            {resultado.simulacion && (
              <p className="text-sm text-amber-600 bg-amber-500/10 rounded-md p-2.5">
                Simulación completada. Desmarca &quot;Simulación&quot; y vuelve a ejecutar para importar de verdad.
              </p>
            )}
            <DialogFooter>
              <Button variant="outline" onClick={() => { setResultado(null); setSimulacion(false); }}>
                Importar de verdad
              </Button>
              <Button className="bg-emerald-600 hover:bg-emerald-700" onClick={() => { setAbierto(false); reiniciar(); }}>
                Cerrar
              </Button>
            </DialogFooter>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}
