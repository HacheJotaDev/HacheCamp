"use client";

import { useEffect } from "react";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import {
  LayoutDashboard, Users, Mail, FileText, Ban, Settings, Loader2,
  Sun, Moon, LogOut,
} from "lucide-react";
import { useTheme } from "next-themes";
import { Button } from "@/components/ui/button";
import { Avatar, AvatarFallback } from "@/components/ui/avatar";
import {
  DropdownMenu, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel,
  DropdownMenuSeparator, DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Badge } from "@/components/ui/badge";
import { cn } from "@/lib/utils";
import { useAuth } from "@/lib/providers";

const NAV = [
  { href: "/panel", label: "Panel", icon: LayoutDashboard, exact: true },
  { href: "/panel/contactos", label: "Contactos", icon: Users },
  { href: "/panel/plantillas", label: "Plantillas", icon: FileText },
  { href: "/panel/campanas", label: "Campañas", icon: Mail },
  { href: "/panel/supresion", label: "Supresión", icon: Ban },
  { href: "/panel/ajustes", label: "Ajustes", icon: Settings },
];

export default function PanelLayout({ children }: { children: React.ReactNode }) {
  const { user, cargando, salir } = useAuth();
  const router = useRouter();
  const pathname = usePathname();
  const { theme, setTheme } = useTheme();

  useEffect(() => {
    if (!cargando && !user) router.replace("/");
  }, [cargando, user, router]);

  if (cargando || !user) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-background">
        <Loader2 className="h-8 w-8 animate-spin text-muted-foreground" />
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-background flex">
      {/* Barra lateral */}
      <aside className="hidden md:flex flex-col w-60 border-r bg-card/50 sticky top-0 h-screen">
        <div className="flex items-center gap-2.5 px-5 h-16 border-b">
          <div className="h-8 w-8 rounded-lg bg-emerald-600 flex items-center justify-center">
            <Mail className="h-4 w-4 text-white" />
          </div>
          <div className="leading-tight">
            <p className="font-bold text-sm">HJ Campaigns</p>
            <p className="text-[11px] text-muted-foreground">Amazon SES</p>
          </div>
        </div>
        <nav className="flex-1 p-3 space-y-1 overflow-y-auto">
          {NAV.map((item) => {
            const activo = item.exact ? pathname === item.href : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors min-h-[44px]",
                  activo
                    ? "bg-emerald-600 text-white shadow-sm"
                    : "text-muted-foreground hover:bg-muted hover:text-foreground"
                )}
              >
                <item.icon className="h-4 w-4 shrink-0" />
                {item.label}
              </Link>
            );
          })}
        </nav>
        <div className="p-3 border-t">
          <p className="text-[11px] text-muted-foreground px-2">
            Sesión segura · Argon2id + TOTP
          </p>
        </div>
      </aside>

      {/* Contenido */}
      <div className="flex-1 flex flex-col min-w-0">
        <header className="h-16 border-b bg-card/50 backdrop-blur sticky top-0 z-20 flex items-center justify-between px-4 md:px-6">
          <div className="md:hidden flex items-center gap-2">
            <div className="h-7 w-7 rounded-md bg-emerald-600 flex items-center justify-center">
              <Mail className="h-3.5 w-3.5 text-white" />
            </div>
            <span className="font-bold text-sm">HJ Campaigns</span>
          </div>
          <div className="hidden md:block" />
          <div className="flex items-center gap-2">
            <Button
              variant="ghost" size="icon" aria-label="Cambiar tema"
              onClick={() => setTheme(theme === "dark" ? "light" : "dark")}
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </Button>
            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <button className="flex items-center gap-2 rounded-full outline-none focus-visible:ring-2 focus-visible:ring-emerald-600">
                  <Avatar className="h-8 w-8">
                    <AvatarFallback className="bg-emerald-600/15 text-emerald-700 dark:text-emerald-400 text-xs font-bold">
                      {user.name.split(" ").map((p) => p[0]).slice(0, 2).join("").toUpperCase()}
                    </AvatarFallback>
                  </Avatar>
                  <span className="hidden sm:block text-sm font-medium max-w-[140px] truncate">{user.name}</span>
                  <Badge variant="outline" className="hidden lg:inline-flex text-[10px] uppercase">
                    {user.role}
                  </Badge>
                </button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end" className="w-56">
                <DropdownMenuLabel>
                  <p className="text-sm font-medium truncate">{user.email}</p>
                  <p className="text-xs text-muted-foreground">
                    MFA: {user.totp_enabled ? "activado" : "no configurado"}
                  </p>
                </DropdownMenuLabel>
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild>
                  <Link href="/panel/ajustes"><Settings className="mr-2 h-4 w-4" /> Ajustes y MFA</Link>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem onClick={() => salir()} className="text-red-600 focus:text-red-600">
                  <LogOut className="mr-2 h-4 w-4" /> Cerrar sesión
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </header>

        {/* Navegación móvil inferior */}
        <nav className="md:hidden fixed bottom-0 inset-x-0 z-30 border-t bg-card/95 backdrop-blur flex justify-around py-1.5">
          {NAV.slice(0, 5).map((item) => {
            const activo = item.exact ? pathname === item.href : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex flex-col items-center gap-0.5 px-3 py-1.5 text-[10px] rounded-lg min-w-[52px]",
                  activo ? "text-emerald-600 font-semibold" : "text-muted-foreground"
                )}
              >
                <item.icon className="h-5 w-5" />
                {item.label}
              </Link>
            );
          })}
        </nav>

        <main className="flex-1 p-4 md:p-6 pb-24 md:pb-6 max-w-7xl w-full mx-auto">{children}</main>
      </div>
    </div>
  );
}
