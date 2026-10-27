"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useRef, useState } from "react";
import { cn } from "@/lib/utils";
import {
  FileText,
  Upload,
  Home,
  Sparkles,
  GitFork,
  ShieldCheck,
  MessageSquare,
  Bot,
  FlaskConical,
  Library,
  ScrollText,
  ClipboardCheck,
  ChevronDown,
} from "lucide-react";

/** Pages of the V2 knowledge workbench merged from upstream (URL-only until now). */
const workbenchItems = [
  { href: "/knowledge", label: "知识库", icon: Library, hint: "标注总表 / 人物 / 机构 / 地点 / 事件" },
  { href: "/research", label: "研究档案", icon: ScrollText, hint: "按档案组织的研究记录" },
  { href: "/reviews", label: "审核队列", icon: ClipboardCheck, hint: "逐项通过 / 修改 / 驳回" },
];

export function Header() {
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const menuRef = useRef<HTMLDivElement | null>(null);

  const navItems = [
    { href: "/", label: "首页", icon: Home },
    { href: "/upload", label: "上传", icon: Upload },
    { href: "/documents", label: "文档列表", icon: FileText },
    { href: "/kg/graph", label: "人物图谱", icon: GitFork },
    { href: "/kg/review", label: "知识审核", icon: ShieldCheck },
    { href: "/kg/workflow", label: "研究流程", icon: FlaskConical },
    { href: "/rag", label: "智能问答", icon: MessageSquare },
    { href: "/agent", label: "AI 研究员", icon: Bot },
  ];

  const workbenchActive = workbenchItems.some((item) => pathname.startsWith(item.href));

  useEffect(() => {
    if (!menuOpen) return;
    const onPointerDown = (event: MouseEvent) => {
      if (menuRef.current && !menuRef.current.contains(event.target as Node)) setMenuOpen(false);
    };
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMenuOpen(false);
    };
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [menuOpen]);

  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  return (
    <header className="sticky top-0 z-50 border-b border-white/60 bg-white/70 backdrop-blur-xl">
      <div className="app-shell flex h-20 items-center gap-4">
        <Link href="/" className="flex shrink-0 items-center gap-3">
          <div className="flex h-11 w-11 items-center justify-center rounded-2xl bg-primary text-primary-foreground shadow-lg shadow-primary/20">
            <Sparkles className="h-5 w-5" />
          </div>
          <div className="hidden whitespace-nowrap sm:block">
            <p className="text-sm font-medium text-primary/80">Digital Humanities OCR</p>
            <p className="text-lg font-semibold tracking-tight text-slate-900">OCR 翻译平台</p>
          </div>
        </Link>

        <nav className="ml-auto flex min-w-0 items-center gap-1 rounded-full border border-white/70 bg-white/75 p-1.5 shadow-sm lg:gap-2">
          {navItems.map((item) => {
            const Icon = item.icon;
            const isActive =
              item.href === "/"
                ? pathname === "/"
                : pathname.startsWith(item.href);
            return (
              <Link
                key={item.href}
                href={item.href}
                className={cn(
                  "flex shrink-0 items-center gap-2 rounded-full px-2.5 py-2 text-sm font-medium transition-all xl:px-4",
                  isActive
                    ? "bg-slate-900 text-white shadow-sm"
                    : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
                )}
              >
                <Icon className="h-4 w-4" />
                <span className="hidden xl:inline">{item.label}</span>
              </Link>
            );
          })}

          <div className="relative shrink-0" ref={menuRef}>
            <button
              type="button"
              onClick={() => setMenuOpen((open) => !open)}
              aria-haspopup="menu"
              aria-expanded={menuOpen}
              className={cn(
                "flex items-center gap-2 rounded-full px-2.5 py-2 text-sm font-medium transition-all xl:px-4",
                workbenchActive
                  ? "bg-slate-900 text-white shadow-sm"
                  : "text-slate-600 hover:bg-slate-100 hover:text-slate-900"
              )}
            >
              <Library className="h-4 w-4" />
              <span className="hidden xl:inline">知识工作台</span>
              <ChevronDown className={cn("h-3.5 w-3.5 transition-transform", menuOpen && "rotate-180")} />
            </button>

            {menuOpen && (
              <div
                role="menu"
                className="absolute right-0 top-[calc(100%+10px)] z-50 w-64 overflow-hidden rounded-2xl border border-slate-200 bg-white/95 p-1.5 shadow-xl shadow-slate-900/10 backdrop-blur"
              >
                {workbenchItems.map((item) => {
                  const Icon = item.icon;
                  return (
                    <Link
                      key={item.href}
                      href={item.href}
                      role="menuitem"
                      className={cn(
                        "flex items-start gap-3 rounded-xl px-3 py-2 transition-colors",
                        pathname.startsWith(item.href)
                          ? "bg-slate-900 text-white"
                          : "text-slate-700 hover:bg-slate-100"
                      )}
                    >
                      <Icon className="mt-0.5 h-4 w-4 shrink-0" />
                      <span className="min-w-0">
                        <span className="block text-sm font-medium">{item.label}</span>
                        <span
                          className={cn(
                            "block text-xs",
                            pathname.startsWith(item.href) ? "text-white/70" : "text-slate-500"
                          )}
                        >
                          {item.hint}
                        </span>
                      </span>
                    </Link>
                  );
                })}
              </div>
            )}
          </div>
        </nav>
      </div>
    </header>
  );
}
