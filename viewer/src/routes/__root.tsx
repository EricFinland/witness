import { Outlet, createRootRouteWithContext, Link } from "@tanstack/react-router";
import type { QueryClient } from "@tanstack/react-query";
import { Eye, Github } from "lucide-react";

export const Route = createRootRouteWithContext<{ queryClient: QueryClient }>()({
  component: RootLayout,
});

function RootLayout() {
  return (
    <div className="min-h-full flex flex-col">
      <header className="h-10 border-b border-border flex items-center px-4 gap-3 bg-bg shrink-0">
        <Link to="/" className="flex items-center gap-2 text-fg font-semibold tracking-tight text-[13px]">
          <Eye size={14} className="text-accent" strokeWidth={2.5} />
          <span>Witness</span>
        </Link>
        <span className="text-fg-subtle text-[11px] mono">v0.0.1</span>
        <div className="flex-1" />
        <a
          href="https://github.com/ericcatalano/witness"
          target="_blank"
          rel="noreferrer"
          className="text-fg-muted hover:text-fg rounded-md p-1 transition-colors"
          aria-label="GitHub"
        >
          <Github size={14} />
        </a>
      </header>
      <main className="flex-1 min-h-0">
        <Outlet />
      </main>
    </div>
  );
}
