"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Bell, Sprout } from "lucide-react";
import type { Lang } from "@/i18n/strings";
import { LANG_LABELS, t } from "@/i18n/strings";
import { useLang } from "@/lib/lang-context";
import { fetchAlerts } from "@/lib/api";

const LINKS = [
  { href: "/", key: "navAnalyze" },
  { href: "/agent", key: "navAgent" },
  { href: "/iot", key: "navIot" },
] as const;

export default function SiteHeader() {
  const { lang, setLang } = useLang();
  const pathname = usePathname();
  const [activeAlerts, setActiveAlerts] = useState(0);

  // Site-wide alert badge: light poll, skipped while the tab is hidden.
  // Fetch-then-set only (no sync setState in the effect body — eslint rule).
  useEffect(() => {
    const loadBadge = () => {
      if (document.hidden) return;
      fetchAlerts({ status: "active", limit: 1 })
        .then((res) => setActiveAlerts(res.active_count))
        .catch(() => setActiveAlerts(0));
    };
    loadBadge();
    const id = setInterval(loadBadge, 30000);
    return () => clearInterval(id);
  }, []);

  return (
    <header className="sticky top-0 z-40 border-b border-stone-200/80 bg-white/85 backdrop-blur-sm print:hidden">
      <div className="mx-auto flex max-w-4xl items-center gap-4 px-6 py-3">
        <Link
          href="/"
          className="inline-flex items-center gap-2 font-display text-base font-semibold text-forest transition hover:brightness-110"
        >
          <Sprout size={20} className="shrink-0" />
          <span className="hidden sm:inline">{t(lang, "poweredBy")}</span>
        </Link>

        <nav className="flex items-center gap-1">
          {LINKS.map((link) => {
            const active =
              link.href === "/" ? pathname === "/" : pathname.startsWith(link.href);
            return (
              <Link
                key={link.href}
                href={link.href}
                className={`rounded-xl px-3 py-1.5 text-sm font-medium transition ${
                  active
                    ? "bg-forest/10 text-forest"
                    : "text-stone-500 hover:bg-stone-100 hover:text-stone-800"
                }`}
                aria-current={active ? "page" : undefined}
              >
                {t(lang, link.key)}
              </Link>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-2">
          <Link
            href="/iot#alerts"
            aria-label={t(lang, "alertsTitle")}
            title={t(lang, "alertsTitle")}
            className="relative inline-flex items-center rounded-xl p-2 text-stone-500 transition hover:bg-stone-100 hover:text-stone-800"
          >
            <Bell size={17} />
            {activeAlerts > 0 && (
              <span className="absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full bg-bad px-1 text-[10px] font-bold leading-none text-white tabular-nums">
                {activeAlerts > 99 ? "99+" : activeAlerts}
              </span>
            )}
          </Link>
          <label htmlFor="site-lang" className="text-xs font-medium text-stone-500">
            {t(lang, "language")}
          </label>
          <select
            id="site-lang"
            value={lang}
            onChange={(e) => setLang(e.target.value as Lang)}
            className="rounded-xl border border-stone-200 bg-white px-3 py-1.5 text-sm font-medium text-stone-800 outline-none focus:border-forest"
          >
            {(Object.keys(LANG_LABELS) as Lang[]).map((l) => (
              <option key={l} value={l}>
                {LANG_LABELS[l]}
              </option>
            ))}
          </select>
        </div>
      </div>
    </header>
  );
}
