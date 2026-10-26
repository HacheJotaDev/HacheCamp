"use client";

import { use } from "react";
import Link from "next/link";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";
import { ArrowLeft, Trash2, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "@/components/ui/table";
import {
  AlertDialog, AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription,
  AlertDialogFooter, AlertDialogHeader, AlertDialogTitle, AlertDialogTrigger,
} from "@/components/ui/alert-dialog";
import { api, Contact } from "@/lib/api";
import { EmptyState, StatusBadge, fecha } from "@/components/shared";
import { ConsentEventOut } from "@/lib/api";

export default function ContactoDetallePage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = use(params);
  const qc = useQueryClient();

  const { data: contacto, isLoading } = useQuery({
    queryKey: ["contacto", id],
    queryFn: () => api.get<Contact>(`/api/v1/contacts/${id}`),
  });

  const { data: consent } = useQuery({
    queryKey: ["consent", id],
    queryFn: () => api.get<ConsentEventOut[]>(`/api/v1/contacts/${id}/consent`),
  });

  const darBaja = useMutation({
    mutationFn: () => api.delete(`/api/v1/contacts/${id}`),
    onSuccess: () => {
      toast.success("Contacto dado de baja y suprimido globalmente");
      qc.invalidateQueries({ queryKey: ["contacto", id] });
      qc.invalidateQueries({ queryKey: ["contactos"] });
    },
    onError: (e: Error) => toast.error(e.message),
  });

  if (isLoading) {
    return <div className="flex justify-center py-20"><Loader2 className="h-8 w-8 animate-spin text-muted-foreground" /></div>;
  }
  if (!contacto) {
    return <EmptyState icon={Trash2} titulo="Contacto no encontrado" descripcion="Puede haber sido eliminado." />;
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <div className="flex items-center gap-3 min-w-0">
          <Button variant="ghost" size="icon" asChild aria-label="Volver">
            <Link href="/panel/contactos"><ArrowLeft className="h-4 w-4" /></Link>
          </Button>
          <div className="min-w-0">
            <h1 className="text-xl md:text-2xl font-bold tracking-tight truncate">{contacto.email}</h1>
            <p className="text-sm text-muted-foreground">
              {[contacto.first_name, contacto.last_name].filter(Boolean).join(" ") || "Sin nombre"}
            </p>
          </div>
        </div>
        <div className="flex items-center gap-2">
          <StatusBadge estado={contacto.status} />
          {contacto.status === "suscripto" && (
            <AlertDialog>
              <AlertDialogTrigger asChild>
                <Button variant="destructive" size="sm"><Trash2 className="mr-2 h-4 w-4" /> Dar de baja</Button>
              </AlertDialogTrigger>
              <AlertDialogContent>
                <AlertDialogHeader>
                  <AlertDialogTitle>¿Dar de baja este contacto?</AlertDialogTitle>
                  <AlertDialogDescription>
                    Se añadirá a la lista de supresión global: nunca volverá a recibir campañas,
                    ni siquiera si se reimporta el CSV. La acción queda registrada en la auditoría.
                  </AlertDialogDescription>
                </AlertDialogHeader>
                <AlertDialogFooter>
                  <AlertDialogCancel>Cancelar</AlertDialogCancel>
                  <AlertDialogAction className="bg-red-600 hover:bg-red-700" onClick={() => darBaja.mutate()}>
                    Confirmar baja
                  </AlertDialogAction>
                </AlertDialogFooter>
              </AlertDialogContent>
            </AlertDialog>
          )}
        </div>
      </div>

      <div className="grid gap-4 lg:grid-cols-2">
        <Card>
          <CardHeader><CardTitle>Datos y consentimiento</CardTitle></CardHeader>
          <CardContent className="space-y-2.5 text-sm">
            <Dato etiqueta="Correo" valor={contacto.email} />
            <Dato etiqueta="Estado" valor={<StatusBadge estado={contacto.status} />} />
            <Dato etiqueta="Fuente" valor={contacto.source || "—"} />
            <Dato etiqueta="Origen del consentimiento" valor={contacto.consent_source || "—"} />
            <Dato etiqueta="Fecha del consentimiento" valor={fecha(contacto.consent_at)} />
            {contacto.consent_text && <Dato etiqueta="Texto aceptado" valor={contacto.consent_text} />}
            <Dato etiqueta="Dado de baja" valor={fecha(contacto.unsubscribed_at)} />
            <Dato etiqueta="Alta en el sistema" valor={fecha(contacto.created_at)} />
            {contacto.lists && contacto.lists.length > 0 && (
              <Dato etiqueta="Listas" valor={contacto.lists.map((l) => l.name).join(", ")} />
            )}
            {contacto.tags && contacto.tags.length > 0 && (
              <Dato etiqueta="Etiquetas" valor={contacto.tags.map((t) => t.name).join(", ")} />
            )}
          </CardContent>
        </Card>

        <Card>
          <CardHeader><CardTitle>Historial de consentimiento</CardTitle></CardHeader>
          <CardContent>
            {!consent || consent.length === 0 ? (
              <EmptyState icon={Trash2} titulo="Sin eventos" descripcion="No hay registros de consentimiento." />
            ) : (
              <div className="space-y-2.5">
                {consent.map((e) => (
                  <div key={e.id} className="flex items-start gap-3 text-sm border-b pb-2.5 last:border-0">
                    <div className="mt-1 h-2 w-2 rounded-full bg-emerald-600 shrink-0" />
                    <div className="min-w-0">
                      <p className="font-medium capitalize">{e.event.replace("_", " ")}</p>
                      <p className="text-xs text-muted-foreground">
                        {fecha(e.created_at)} · {e.source || "sistema"}
                      </p>
                      {e.detail && Object.keys(e.detail).length > 0 && (
                        <p className="text-xs text-muted-foreground mt-0.5 break-all">
                          {JSON.stringify(e.detail)}
                        </p>
                      )}
                    </div>
                  </div>
                ))}
              </div>
            )}
          </CardContent>
        </Card>
      </div>
    </div>
  );
}

function Dato({ etiqueta, valor }: { etiqueta: string; valor: React.ReactNode }) {
  return (
    <div className="flex flex-col sm:flex-row sm:items-start justify-between gap-1 border-b pb-2 last:border-0">
      <span className="text-muted-foreground">{etiqueta}</span>
      <span className="font-medium text-right sm:max-w-[60%] break-words">{valor}</span>
    </div>
  );
}
