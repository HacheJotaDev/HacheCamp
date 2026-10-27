import * as React from "react"

const MOBILE_BREAKPOINT = 768

function suscribir(callback: () => void) {
  const mql = window.matchMedia(`(max-width: ${MOBILE_BREAKPOINT - 1}px)`)
  mql.addEventListener("change", callback)
  return () => mql.removeEventListener("change", callback)
}

/**
 * true si el viewport es menor que el breakpoint móvil.
 *
 * Implementado con useSyncExternalStore (patrón recomendado por React para
 * valores externos como matchMedia): sin setState dentro de un efecto, sin
 * parpadeo inicial y con valor correcto en SSR (false).
 */
export function useIsMobile() {
  return React.useSyncExternalStore(
    suscribir,
    () => window.innerWidth < MOBILE_BREAKPOINT,
    () => false,
  )
}
