"use client";

import { QueryClient, QueryClientProvider, useQuery } from "@tanstack/react-query";
import { ThemeProvider } from "next-themes";
import { createContext, useContext, useEffect, useState } from "react";
import { Toaster } from "@/components/ui/sonner";
import { api, setCsrfToken, setUnauthorizedHandler, User } from "@/lib/api";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: { retry: 1, refetchOnWindowFocus: false, staleTime: 10_000 },
  },
});

interface AuthCtx {
  user: User | null;
  cargando: boolean;
  refrescar: () => void;
  salir: () => Promise<void>;
}

const AuthContext = createContext<AuthCtx>({
  user: null,
  cargando: true,
  refrescar: () => {},
  salir: async () => {},
});

export function useAuth() {
  return useContext(AuthContext);
}

function AuthGate({ children }: { children: React.ReactNode }) {
  const { data, isLoading, isError, refetch } = useQuery({
    queryKey: ["me"],
    queryFn: async () => {
      try {
        const res = await api.get<{ csrf_token: string; user: User }>("/api/v1/auth/me");
        setCsrfToken(res.csrf_token);
        return res.user;
      } catch {
        setCsrfToken(null);
        return null;
      }
    },
    staleTime: 60_000,
  });

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setCsrfToken(null);
      queryClient.setQueryData(["me"], null);
    });
  }, []);

  const value: AuthCtx = {
    user: data ?? null,
    cargando: isLoading,
    refrescar: () => refetch(),
    salir: async () => {
      try {
        await api.post("/api/v1/auth/logout");
      } catch {
        /* la sesión ya no existe */
      }
      setCsrfToken(null);
      queryClient.setQueryData(["me"], null);
      queryClient.clear();
      window.location.href = "/";
    },
  };

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function Providers({ children }: { children: React.ReactNode }) {
  return (
    <QueryClientProvider client={queryClient}>
      <ThemeProvider attribute="class" defaultTheme="dark" enableSystem={false}>
        <AuthGate>{children}</AuthGate>
        <Toaster richColors position="top-right" closeButton />
      </ThemeProvider>
    </QueryClientProvider>
  );
}
