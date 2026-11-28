import "katex/dist/katex.min.css";

import ReactMarkdown from "react-markdown";
import rehypeKatex from "rehype-katex";
import remarkGfm from "remark-gfm";
import remarkMath from "remark-math";

import { cn } from "@/lib/cn";

/**
 * Safe Markdown + LaTeX rendering ($x$ inline, $$x$$ block).
 * react-markdown never renders raw HTML, so model or user text cannot inject scripts.
 */
export function Markdown({ children, className }: { children: string; className?: string }) {
  return (
    <div className={cn("ensora-prose", className)}>
      <ReactMarkdown
        remarkPlugins={[remarkGfm, remarkMath]}
        rehypePlugins={[[rehypeKatex, { throwOnError: false, strict: false }]]}
        components={{
          // Links from content open in a new tab and never pass the referrer.
          a: ({ href, children: label }) => (
            <a href={href} target="_blank" rel="noreferrer noopener" className="text-accent underline">
              {label}
            </a>
          ),
        }}
      >
        {children}
      </ReactMarkdown>
    </div>
  );
}
