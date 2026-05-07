import * as React from "react";

import { cn } from "@/lib/utils";

function Badge({
  className,
  children,
}: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div
      className={cn(
        "inline-flex items-center rounded-lg border border-slate-200 bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-700",
        className,
      )}
    >
      {children}
    </div>
  );
}

export { Badge };
