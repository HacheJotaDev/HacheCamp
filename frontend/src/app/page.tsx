"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import { useMutation } from "@tanstack/react-query";
import { toast } from "sonner";
import { Mail, Lock, ShieldCheck, Eye, EyeOff, Loader2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { InputOTP, InputOTPGroup, InputOTPSlot, InputOTPSeparator } from "@/components/ui/input-otp";
import { api, setCsrfToken } from "@/lib/api";
import { useAuth } from "@/lib/providers";

export default function LoginPage() {
  const router = useRouter();
  const { user, cargando } = useAuth();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [verPass, setVerPass] = useState(false);
  const [pasoMfa, setPasoMfa] = useState(false);
  const [mfaToken, setMfaToken] = useState("");
  const [codigo, setCodigo] = useState("");
  const [error, setError] = useState("");

  useEffect(() => {
    if (user && !cargando) router.replace("/panel");
  }, [user, cargando, router]);

  const login = useMutation({
    mutationFn: (vals: { email: string; password: string }) =>
      api.post<{ csrf_token?: string; mfa_required?: boolean; mfa_token?: string }>(
        "/api/v1/auth/login",
        vals
      ),
    onSuccess: (res) => {
      setError("");
      if (res.mfa_required && res.mfa_token) {
        setMfaToken(res.mfa_token);
        setPasoMfa(true);
        return;
      }
      if (res.csrf_token) setCsrfToken(res.csrf_token);
      window.location.href = "/panel";
    },
    onError: (err: Error) => setError(err.message),
  });

  const verificarMfa = useMutation({
    mutationFn: () => api.post<{ csrf_token: string }>("/api/v1/auth/login/mfa", { mfa_token: mfaToken, code: codigo }),
    onSuccess: (res) => {
      setCsrfToken(res.csrf_token);
      window.location.href = "/panel";
    },
    onError: (err: Error) => setError(err.message),
  });

  if (cargando || user) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center bg-gradient-to-b from-background to-emerald-950/10 p-4">
      <div className="w-full max-w-sm">
        <div className="flex flex-col items-center gap-2 mb-6">
          <div className="h-14 w-14 rounded-2xl bg-emerald-600 flex items-center justify-center shadow-lg shadow-emerald-600/20">
            <Mail className="h-7 w-7 text-white" />
          </div>
          <h1 className="text-2xl font-bold tracking-tight">HJ Campaigns</h1>
          <p className="text-sm text-muted-foreground text-center">
            Plataforma profesional de email marketing con Amazon SES
          </p>
        </div>

        <Card>
          {!pasoMfa ? (
            <>
              <CardHeader>
                <CardTitle>Iniciar sesión</CardTitle>
                <CardDescription>Introduce tus credenciales de acceso</CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <form
                  className="space-y-4"
                  onSubmit={(e) => {
                    e.preventDefault();
                    login.mutate({ email, password });
                  }}
                >
                  <div className="space-y-2">
                    <Label htmlFor="email">Correo electrónico</Label>
                    <div className="relative">
                      <Mail className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                      <Input
                        id="email"
                        type="email"
                        autoComplete="username"
                        required
                        className="pl-9"
                        placeholder="tu@dominio.com"
                        value={email}
                        onChange={(e) => setEmail(e.target.value)}
                      />
                    </div>
                  </div>
                  <div className="space-y-2">
                    <Label htmlFor="password">Contraseña</Label>
                    <div className="relative">
                      <Lock className="absolute left-3 top-2.5 h-4 w-4 text-muted-foreground" />
                      <Input
                        id="password"
                        type={verPass ? "text" : "password"}
                        autoComplete="current-password"
                        required
                        className="pl-9 pr-9"
                        placeholder="••••••••••••"
                        value={password}
                        onChange={(e) => setPassword(e.target.value)}
                      />
                      <button
                        type="button"
                        aria-label={verPass ? "Ocultar contraseña" : "Mostrar contraseña"}
                        className="absolute right-3 top-2.5 text-muted-foreground hover:text-foreground"
                        onClick={() => setVerPass((v) => !v)}
                      >
                        {verPass ? <EyeOff className="h-4 w-4" /> : <Eye className="h-4 w-4" />}
                      </button>
                    </div>
                  </div>
                  {error && (
                    <p className="text-sm text-destructive bg-destructive/10 rounded-md px-3 py-2">{error}</p>
                  )}
                  <Button type="submit" className="w-full bg-emerald-600 hover:bg-emerald-700" disabled={login.isPending}>
                    {login.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                    Entrar
                  </Button>
                </form>
              </CardContent>
            </>
          ) : (
            <>
              <CardHeader>
                <CardTitle className="flex items-center gap-2">
                  <ShieldCheck className="h-5 w-5 text-emerald-600" /> Verificación en dos pasos
                </CardTitle>
                <CardDescription>
                  Introduce el código de tu aplicación autenticadora (o un código de recuperación)
                </CardDescription>
              </CardHeader>
              <CardContent className="space-y-4">
                <div className="flex justify-center py-2">
                  <InputOTP maxLength={6} value={codigo} onChange={setCodigo}>
                    <InputOTPGroup>
                      <InputOTPSlot index={0} />
                      <InputOTPSlot index={1} />
                      <InputOTPSlot index={2} />
                    </InputOTPGroup>
                    <InputOTPSeparator />
                    <InputOTPGroup>
                      <InputOTPSlot index={3} />
                      <InputOTPSlot index={4} />
                      <InputOTPSlot index={5} />
                    </InputOTPGroup>
                  </InputOTP>
                </div>
                {error && (
                  <p className="text-sm text-destructive bg-destructive/10 rounded-md px-3 py-2">{error}</p>
                )}
                <Button
                  className="w-full bg-emerald-600 hover:bg-emerald-700"
                  disabled={codigo.length < 6 || verificarMfa.isPending}
                  onClick={() => verificarMfa.mutate()}
                >
                  {verificarMfa.isPending && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                  Verificar
                </Button>
                <Button variant="ghost" className="w-full" onClick={() => { setPasoMfa(false); setCodigo(""); setError(""); }}>
                  Volver
                </Button>
              </CardContent>
            </>
          )}
        </Card>

        <p className="text-xs text-muted-foreground text-center mt-6">
          Acceso restringido a personal autorizado. Los intentos de acceso se registran y auditan.
        </p>
      </div>
    </div>
  );
}
