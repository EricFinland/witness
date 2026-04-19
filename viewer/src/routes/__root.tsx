import { Outlet, createRootRouteWithContext, Link } from "@tanstack/react-router";
import type { QueryClient } from "@tanstack/react-query";
import { Eye } from "lucide-react";

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: RootLayout,
});

function RootLayout() {
  return (
    <div className="min-h-full flex flex-col">
      <header className="h-12 border-b border-border flex items-center px-5 gap-3 bg-bg">
        <Link to="/" className="flex items-center gap-2 text-fg font-semibold tracking-tight">
          <Eye size={15} className="text-accent" strokeWidth={2.25} />
          <span>Witness</span>
        </Link>
        <span className="text-fg-subtle text-xs font-mono ml-1">v0.0.1</span>
        <div className="flex-1" />
        <a
          href="https://github.com/ericcatalano/witness"
          target="_blank"
          rel="noreferrer"
          className="text-xs text-fg-muted hover:text-fg"
        >
          github
        </a>
      </header>
      <main className="flex-1 min-h-0">
        <Outlet />
      </main>
    </div>
  );
}
