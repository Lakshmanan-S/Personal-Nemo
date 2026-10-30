/**
 * Every screen in one module (plan.md section 10): Home/Onboarding, My
 * Profile, Chat, See the Difference, Know Me Better, Privacy & MCP, and
 * Under the Hood - plus the small shared UI bits (Logo, confidence bar, tier
 * chip, buttons, cards, chat avatars, nudge card) they all reuse. Built with
 * lucide-react icons and framer-motion micro-interactions for a premium feel
 * without pulling in a component library.
 */
import { AnimatePresence, motion } from "framer-motion";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  Activity,
  AlertTriangle,
  ArrowLeft,
  BadgeCheck,
  BarChart3,
  Bot,
  CalendarDays,
  Check,
  CheckCircle2,
  ChevronDown,
  Clapperboard,
  Clock3,
  Copy,
  Dumbbell,
  Eye,
  EyeOff,
  Gauge,
  Gem,
  HelpCircle,
  KeyRound,
  Link2,
  Lock,
  Mail,
  MapPin,
  Music2,
  Pencil,
  RefreshCw,
  Save,
  Scale,
  Send,
  Shapes,
  Shield,
  ShieldCheck,
  Shirt,
  Sparkles,
  Trash2,
  TrendingDown,
  User as UserIcon,
  UtensilsCrossed,
  Wand2,
  X,
  Zap,
} from "lucide-react";
import { useCallback, useEffect, useId, useRef, useState } from "react";
import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import * as api from "./api";
import type {
  Connection,
  MCPLogEntry,
  MCPApproval,
  MyLife,
  Nudge,
  PrivacyTier,
  ProfileByCategory,
  QATopic,
  Trait,
  TraitDetail,
} from "./api";


const CATEGORY_LABELS: Record<string, string> = {
  food: "Food",
  music: "Music",
  entertainment: "Entertainment",
  fitness: "Fitness",
  routine: "Routine",
  places_travel: "Places & Travel",
  style_shopping: "Style & Shopping",
  personality: "Personality",
  appearance: "Appearance",
};


const CATEGORY_ICONS: Record<string, typeof UtensilsCrossed> = {
  food: UtensilsCrossed,
  music: Music2,
  entertainment: Clapperboard,
  fitness: Dumbbell,
  routine: Clock3,
  places_travel: MapPin,
  style_shopping: Shirt,
  personality: Shapes,
  appearance: Eye,
};


const PROVIDER_LABELS: Record<string, string> = {
  gmail: "Gmail",
  gcal: "Google Calendar",
  spotify: "Spotify",
  strava: "Strava",
  trakt: "Trakt",
};


const PROVIDER_ICONS: Record<string, typeof Mail> = {
  gmail: Mail,
  gcal: CalendarDays,
  spotify: Music2,
  strava: Activity,
  trakt: Clapperboard,
};


const TIER_META: Record<PrivacyTier, { icon: typeof Eye; label: string; classes: string }> = {
  share: { icon: Eye, label: "Share", classes: "bg-share-500/15 text-share-500 border-share-500/40" },
  ask: { icon: HelpCircle, label: "Ask", classes: "bg-ask-500/15 text-ask-500 border-ask-500/40" },
  private: { icon: Lock, label: "Private", classes: "bg-private-500/15 text-private-500 border-private-500/40" },
};


function categoryIcon(category: string) {
  return CATEGORY_ICONS[category] ?? Sparkles;
}


function todayCountdown(at: string, now: number): string {
  const minutes = Math.max(0, Math.ceil((Date.parse(at) - now) / 60000));
  if (minutes === 0) return "starting now";
  if (minutes < 60) return `in ${minutes} min`;
  const hours = Math.floor(minutes / 60);
  return `in ${hours}h${minutes % 60 ? ` ${minutes % 60}m` : ""}`;
}


export function TodayWidget() {
  const [open, setOpen] = useState(false);
  const [data, setData] = useState<api.TodayData | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now);
  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  const panelId = useId();
  const latest = useRef(0);


  const load = useCallback((signal?: AbortSignal) => {
    const request = ++latest.current;
    return api.getToday(zone, signal).then((saved) => {
      if (!signal?.aborted && request === latest.current) { setData(saved); setError(""); }
    }).catch((err: unknown) => {
      if (!signal?.aborted && request === latest.current)
        setError(err instanceof Error ? err.message : "Could not load today's items.");
    });
  }, [zone]);
  useEffect(() => {
    const controller = new AbortController();
    const refresh = () => { void load(controller.signal); };
    refresh();
    const timer = setInterval(() => {
      setNow(Date.now());
      if (document.visibilityState === "visible") refresh();
    }, 60000);
    window.addEventListener("focus", refresh);
    window.addEventListener("today-items-changed", refresh);
    return () => {
      controller.abort(); clearInterval(timer);
      window.removeEventListener("focus", refresh);
      window.removeEventListener("today-items-changed", refresh);
    };
  }, [load]);


  const scan = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    setNotice("");
    try {
      const result = await api.processToday(zone);
      await load();
      const failures = result.results.filter((entry) => entry.status === "failed");
      const unmatched = result.results.reduce((sum, entry) => sum + (entry.unmatched_updates ?? 0), 0);
      setNotice(result.message + (unmatched ? ` ${unmatched} update(s) could not be matched; existing items were not changed.` : ""));
      if (failures.length) setError(failures.map((entry) => entry.error).join(" "));
    } catch (err) { setError(err instanceof Error ? err.message : "Could not check imported emails."); }
    finally { setBusy(false); }
  };
  const update = async (id: string, action: "done" | "dismiss" | "snooze") => {
    if (busy) return;
    setBusy(true);
    setNotice("");
    try {
      const result = await api.updateTodayItem(id, action);
      setExpanded(null);
      await load();
      setNotice(result.message);
    } catch (err) { setError(err instanceof Error ? err.message : "Could not update item."); }
    finally { setBusy(false); }
  };
  const today = new Intl.DateTimeFormat("en-CA", { timeZone: zone, year: "numeric", month: "2-digit", day: "2-digit" }).format(now);
  const items = data?.date === today ? data.items.filter((item) =>
    item.kind === "action" || item.all_day || (item.at !== null && Date.parse(item.at) > now)) : [];
  const actions = items.filter((item) => item.kind === "action");
  const reminders = items.filter((item) => item.kind === "reminder");


  return (
    <aside aria-label="Today assistant" className="fixed bottom-4 right-4 z-50 max-w-[calc(100vw-2rem)]">
      {open && (
        <section id={panelId} aria-label="Today's items" className="mb-3 max-h-[calc(100dvh-6rem)] w-80 max-w-full overflow-y-auto rounded-2xl border border-vault-600 bg-vault-900 p-4 text-sm shadow-2xl">
          <div className="mb-3 flex items-center justify-between">
            <h2 className="font-semibold text-gold-400">Today</h2>
            <button type="button" aria-label="Minimize Today" onClick={() => setOpen(false)} className="rounded p-1 text-vault-300 hover:text-white"><X className="h-4 w-4" /></button>
          </div>
          <p className="mb-3 text-[11px] text-vault-400">Your actions and plans for today. {zone}.</p>
          <div className="max-h-[55vh] space-y-4 overflow-y-auto break-words pr-1">
            {error && <p role="alert" className="text-xs text-private-500">{error}{data ? " Last saved results may be stale." : ""}</p>}
            {notice && <p role="status" className="text-xs text-gold-400">{notice}</p>}
            <div>
              <h3 className="mb-2 font-semibold text-white">Actions · {actions.length}</h3>
              {!actions.length && <p className="text-xs text-vault-400">{error ? "Some checks need attention; today's actions may be missing." : data ? "No pending dated actions found for today." : "Saved actions have not loaded."}</p>}
              <ul className="space-y-2">
                {actions.map((item) => (
                  <li key={item.id} className="rounded-lg border border-vault-700 p-2">
                    <button type="button" aria-expanded={expanded === item.id} onClick={() => setExpanded(expanded === item.id ? null : item.id)}
                      className="w-full text-left text-xs font-medium text-white">{item.title}</button>
                    {expanded === item.id && (
                      <div className="mt-2 space-y-2 text-xs">
                        <p className="text-vault-200">{item.detail}</p>
                        {item.at && <p className="text-vault-400">Due {new Date(item.at).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</p>}
                        {item.source_url && <a className="text-gold-400 underline" href={item.source_url} target="_blank" rel="noopener noreferrer">Open email</a>}
                        <div className="flex flex-wrap gap-2">
                          <Button disabled={busy} variant="secondary" onClick={() => void update(item.id, "done")}>Done</Button>
                          <Button disabled={busy} variant="ghost" onClick={() => void update(item.id, "snooze")}>Snooze 30m</Button>
                          <Button disabled={busy} variant="ghost" onClick={() => void update(item.id, "dismiss")}>Not an action</Button>
                        </div>
                      </div>
                    )}
                  </li>
                ))}
              </ul>
            </div>
            <div>
              <h3 className="mb-2 font-semibold text-white">Reminders · {reminders.length}</h3>
              {!reminders.length && <p className="text-xs text-vault-400">{data ? "No upcoming reminders found for today." : "Saved reminders have not loaded."}</p>}
              <ul className="space-y-2">
                {reminders.map((item) => (
                  <li key={item.id} className="flex items-start gap-2 text-xs text-vault-200">
                    <Clock3 className="mt-0.5 h-3 w-3 shrink-0" />
                    <span className="flex-1">{item.title} <time dateTime={item.at ?? undefined} className="text-gold-400" title={item.at ? new Date(item.at).toLocaleString() : undefined}>{item.all_day ? "All day" : item.at && todayCountdown(item.at, now)}</time></span>
                    {item.source_url && <a href={item.source_url} target="_blank" rel="noopener noreferrer" aria-label={`Open ${item.source === "gcal" ? "Calendar event" : "email"} for ${item.title}`} className="text-vault-300">{item.source === "gcal" ? <CalendarDays className="h-3 w-3" /> : <Mail className="h-3 w-3" />}</a>}
                    <button type="button" disabled={busy} onClick={() => void update(item.id, "dismiss")} aria-label={`Dismiss ${item.title}`} className="text-vault-400 hover:text-white"><X className="h-3 w-3" /></button>
                  </li>
                ))}
              </ul>
            </div>
          </div>
          <div className="mt-4 space-y-2 border-t border-vault-700 pt-3">
            <Button disabled={api.PUBLIC_DEMO || busy || data === null || data.pending_emails === 0} variant="secondary" onClick={() => void scan()}>
              {busy ? "Working..." : "Check imported emails"}
            </Button>
            <button type="button" disabled={busy} onClick={() => void load()} className="block text-xs text-vault-300 underline">Refresh saved items (free)</button>
            {data && <p className="text-[11px] text-vault-400">{data.pending_emails} emails unchecked / needing review.{data.failed_scans > 0 ? ` ${data.failed_scans} saved checks need review.` : ""}</p>}
            <p className="text-[11px] text-vault-400">Checking new emails uses paid AI. Saved checks are reused.</p>
            {api.PUBLIC_DEMO && <p className="text-[11px] text-gold-400">Live email and Calendar checks are available in your private installation.</p>}
            <details className="text-[11px] text-vault-400">
              <summary className="cursor-pointer text-vault-300">How Today works</summary>
              <p className="mt-2">Today uses imported Gmail emails and manually synced primary Calendar events, not your full inbox or calendar. Check imports first for new deadlines. Each check sends up to five imported emails' subject, snippet, bounded body text and receipt date to Nebius, with at most five paid Super calls. No automatic retries, email sending or profile changes. Relative deadlines use the email's receipt date; EOD has no invented clock time. Future items appear on their day.</p>
            </details>
          </div>
        </section>
      )}
      <button type="button" aria-expanded={open} aria-controls={panelId} onClick={() => setOpen(!open)}
        className="ml-auto flex items-center gap-2 rounded-full border border-gold-500/40 bg-vault-900 px-4 py-3 text-xs font-medium text-gold-400 shadow-xl">
        <Bot className="h-4 w-4" />
        {error ? "Today · needs attention" : data ? `Today · ${actions.length} ${actions.length === 1 ? "action" : "actions"} · ${reminders.length} ${reminders.length === 1 ? "reminder" : "reminders"}` : "Today · loading"}
      </button>
    </aside>
  );
}


// ---------------------------------------------------------------------------
// Shared small UI pieces
// ---------------------------------------------------------------------------


export function Logo() {
  return (
    <div className="relative flex h-10 w-10 items-center justify-center rounded-2xl bg-gradient-to-br from-gold-500 via-gold-400 to-violet-500 shadow-lg shadow-gold-500/30">
      <Gem className="h-5 w-5 text-vault-950" strokeWidth={2.5} />
    </div>
  );
}


export function ConfidenceBar({ value }: { value: number }) {
  const pct = Math.round(Math.max(0, Math.min(1, value)) * 100);
  return (
    <div className="h-1.5 w-full overflow-hidden rounded-full bg-vault-700/80">
      <motion.div
        className="h-1.5 rounded-full bg-gradient-to-r from-violet-500 to-gold-400"
        initial={{ width: 0 }}
        animate={{ width: `${pct}%` }}
        transition={{ duration: 0.7, ease: "easeOut" }}
        aria-label={`Confidence ${pct}%`}
      />
    </div>
  );
}


export function TierChip({ tier }: { tier: PrivacyTier }) {
  const meta = TIER_META[tier];
  const Icon = meta.icon;
  return (
    <span className={`inline-flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs font-medium ${meta.classes}`}>
      <Icon className="h-3 w-3" />
      {meta.label}
    </span>
  );
}


function Card({ children, className = "", glow = false }: { children: React.ReactNode; className?: string; glow?: boolean }) {
  return (
    <div className={`glass glass-hover rounded-2xl p-5 ${glow ? "shimmer-border" : ""} ${className}`}>{children}</div>
  );
}


function SectionHeading({ icon: Icon, children }: { icon: typeof Sparkles; children: React.ReactNode }) {
  return (
    <h2 className="mb-3 flex items-center gap-2 text-xs font-semibold uppercase tracking-wider text-vault-400">
      <Icon className="h-3.5 w-3.5 text-gold-400" />
      {children}
    </h2>
  );
}


function Button({
  children,
  onClick,
  variant = "primary",
  disabled,
  className = "",
  icon: Icon,
}: {
  children: React.ReactNode;
  onClick?: () => void;
  variant?: "primary" | "secondary" | "danger" | "ghost";
  disabled?: boolean;
  className?: string;
  icon?: typeof Sparkles;
}) {
  const styles = {
    primary:
      "bg-gradient-to-r from-gold-500 to-gold-400 text-vault-950 shadow-lg shadow-gold-500/25 hover:shadow-gold-500/40",
    secondary: "glass text-vault-200 hover:text-white",
    danger: "bg-private-500/15 text-private-500 border border-private-500/40 hover:bg-private-500/25",
    ghost: "text-vault-300 hover:text-white hover:bg-vault-800",
  }[variant];
  return (
    <motion.button
      type="button"
      onClick={onClick}
      disabled={disabled}
      whileHover={disabled ? undefined : { scale: 1.03 }}
      whileTap={disabled ? undefined : { scale: 0.97 }}
      className={`inline-flex items-center justify-center gap-1.5 rounded-xl px-4 py-2 text-sm font-semibold transition-all disabled:cursor-not-allowed disabled:opacity-50 ${styles} ${className}`}
    >
      {Icon && <Icon className="h-4 w-4" />}
      {children}
    </motion.button>
  );
}


function StatusPill({ status }: { status: Connection["status"] }) {
  if (status === "disconnected") {
    return (
      <span className="inline-flex items-center gap-1 rounded-full bg-vault-700/60 px-2 py-0.5 text-xs text-vault-300">
        <span className="h-1.5 w-1.5 rounded-full bg-vault-400" />
        Not connected
      </span>
    );
  }
  return (
    <span className="inline-flex items-center gap-1 rounded-full bg-share-500/15 px-2 py-0.5 text-xs text-share-500">
      <span className="h-1.5 w-1.5 rounded-full bg-share-500 pulse-dot" />
      {status === "demo" ? "Demo data" : status}
    </span>
  );
}


// ---------------------------------------------------------------------------
// Home / Onboarding
// ---------------------------------------------------------------------------


function NudgeCard() {
  const [nudge, setNudge] = useState<Nudge | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);


  const generate = async () => {
    setLoading(true);
    setError("");
    try {
      setNudge(await api.getNudge());
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not generate a nudge.");
    } finally {
      setLoading(false);
    }
  };


  if (!nudge) {
    return (
      <Card>
        <p className="mb-3 text-sm text-vault-300">
          Get a personal nudge when you want one. Uses a paid Nemotron request.
        </p>
        <Button onClick={generate} disabled={loading} icon={Sparkles}>
          {loading ? "Generating..." : "Generate a nudge"}
        </Button>
        {error && <p role="alert" className="mt-3 text-sm text-private-500">{error}</p>}
      </Card>
    );
  }


  const Icon = nudge.type === "suggestion" ? Sparkles : HelpCircle;


  return (
    <Card glow className="relative overflow-hidden border-gold-500/30">
      <div className="flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-gold-500/30 to-violet-500/30">
          <Icon className="h-4.5 w-4.5 text-gold-400" />
        </div>
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-gold-400">
            {nudge.type === "suggestion" ? "Today's nudge" : "Help us learn more about you"}
          </p>
          <p className="mt-1 text-sm text-vault-200">{nudge.message}</p>
        </div>
      </div>
    </Card>
  );
}


export function HomePage() {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [loadingDemo, setLoadingDemo] = useState(false);
  const [demoResult, setDemoResult] = useState<{ ok: boolean; message: string } | null>(null);
  const [syncing, setSyncing] = useState<string | null>(null);
  const [connectionNotice, setConnectionNotice] = useState<{ ok: boolean; message: string } | null>(() => {
    const params = new URLSearchParams(window.location.search);
    const provider = params.has("gcal") ? "gcal" : "gmail";
    const result = params.get(provider);
    const label = PROVIDER_LABELS[provider];
    if (!result) return null;
    return {
      ok: result === "connected",
      message: result === "connected"
        ? `${label} connected. Sign-in did not import data. Click ${provider === "gcal" ? "Sync Calendar" : "Sync Gmail"} when ready.`
        : result === "cancelled" ? `${label} connection cancelled. No new access was saved.`
          : `${label} connection failed. Check OAuth settings, read-only permission, and offline access, then try Connect again. Disconnect first to change accounts.`,
    };
  });


  const refresh = () => api.getConnections().then(setConnections).catch((err: unknown) => {
    setConnectionNotice({ ok: false, message: err instanceof Error ? err.message : "Could not load connections." });
  });


  useEffect(() => {
    void refresh();
    const url = new URL(window.location.href);
    if (url.searchParams.has("gmail") || url.searchParams.has("gcal")) {
      url.searchParams.delete("gmail");
      url.searchParams.delete("gcal");
      window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    }
  }, []);


  const handleDemoLoad = async () => {
    setLoadingDemo(true);
    setDemoResult(null);
    try {
      const result = (await api.loadDemoData()) as { traits_upserted: number };
      setDemoResult({ ok: true, message: `Profile loaded - ${result.traits_upserted} traits ready to explore.` });
      refresh();
    } catch (e) {
      setDemoResult({ ok: false, message: String(e) });
    } finally {
      setLoadingDemo(false);
    }
  };


  const handleSync = async (provider: string) => {
    if (syncing) return;
    setSyncing(provider);
    setConnectionNotice(null);
    try {
      const result = await api.syncProvider(provider);
      const message = provider === "gmail"
        ? `Gmail: ${result.messages_fetched ?? 0} messages checked; ${result.raw_items_inserted} new emails imported. Awaiting AI processing; no model charges.${result.limit_reached ? " The 25-message limit was reached; this is not a full mailbox sync." : ""}`
        : provider === "gcal" ? result.message
        : `${PROVIDER_LABELS[provider] ?? provider}: sample data refreshed.`;
      if (!message) throw new Error("Calendar sync returned no result summary. Restart the backend and try again.");
      setConnectionNotice({ ok: true, message });
      if (provider === "gcal") window.dispatchEvent(new Event("today-items-changed"));
      await refresh();
    } catch (err) {
      setConnectionNotice({ ok: false, message: err instanceof Error ? err.message : "Sync failed. No sample fallback was used." });
    } finally {
      setSyncing(null);
    }
  };


  const handleDisconnect = async (provider: "gmail" | "gcal") => {
    if (syncing || !confirm(`Revoke ${PROVIDER_LABELS[provider]} access? Google grants can be shared: Gmail and Calendar connections for the SAME Google account will both disconnect. Imported data, profile cards and reminders remain. Use Delete all my data to remove them.`)) return;
    setSyncing(provider);
    try {
      const result = await api.disconnectGoogle(provider);
      setConnectionNotice({ ok: true, message: result.message });
      await refresh();
    } catch (err) {
      setConnectionNotice({ ok: false, message: err instanceof Error ? err.message : "Could not disconnect Google access." });
    } finally {
      setSyncing(null);
    }
  };


  const handleLearnCalendar = async () => {
    if (syncing) return;
    setSyncing("gcal");
    setConnectionNotice(null);
    try {
      const result = await api.learnCalendar();
      const failures = result.results.filter((entry) => entry.status === "failed");
      setConnectionNotice({
        ok: failures.length === 0,
        message: result.message + (failures.length ? "\n" + [...new Set(failures.map((entry) => entry.error))].join("\n") : ""),
      });
      await refresh();
    } catch (err) {
      setConnectionNotice({ ok: false, message: err instanceof Error ? err.message : "Calendar learning failed. Inspect usage before retrying." });
    } finally { setSyncing(null); }
  };


  const handleProcessEmail = async () => {
    if (syncing) return;
    setSyncing("gmail");
    setConnectionNotice(null);
    try {
      const result = await api.processImportedEmail();
      const failures = result.results.filter((entry) => entry.status === "failed");
      setConnectionNotice({
        ok: failures.length === 0,
        message: result.message + (failures.length ? "\n" + failures.map((entry) => `${entry.email_id}: ${entry.error}`).join("\n") : ""),
      });
      await refresh();
    } catch (err) {
      setConnectionNotice({ ok: false, message: err instanceof Error ? err.message : "Email processing failed. Inspect usage before retrying." });
    } finally {
      setSyncing(null);
    }
  };


  return (
    <div className="space-y-6">
      <Card glow className="relative overflow-hidden text-center">
        <div className="relative mx-auto max-w-lg py-4">
          <span className="inline-flex items-center gap-1.5 rounded-full border border-vault-600 bg-vault-800/60 px-3 py-1 text-[11px] font-medium text-vault-300">
            <Zap className="h-3 w-3 text-gold-400" />
            Powered by Nemotron on Nebius Token Factory
          </span>
          <h1 className="mt-4 text-3xl font-bold tracking-tight text-white">
            The AI that <span className="gradient-text">actually knows you</span>
          </h1>
          <p className="mt-2 text-sm text-vault-300">
            Personal Nemo learns from your real behavior across Gmail, Calendar, Spotify, Strava and Trakt -
            then powers a chat and an MCP bridge that only ever share what you approve.
          </p>
          <Button className="mt-5" onClick={handleDemoLoad} disabled={loadingDemo} icon={loadingDemo ? RefreshCw : Wand2}>
            {loadingDemo ? "Loading sample data..." : "Load sample data"}
          </Button>
          <AnimatePresence>
            {demoResult && (
              <motion.p
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                exit={{ opacity: 0 }}
                className={`mt-3 text-xs ${demoResult.ok ? "text-share-500" : "text-private-500"}`}
              >
                {demoResult.message}
              </motion.p>
            )}
          </AnimatePresence>
        </div>
      </Card>


      <NudgeCard />


      <div>
        <SectionHeading icon={Link2}>Connect your apps</SectionHeading>
        {connectionNotice && (
          <p role={connectionNotice.ok ? "status" : "alert"} className={`mb-4 whitespace-pre-wrap break-words text-sm ${connectionNotice.ok ? "text-share-500" : "text-private-500"}`}>
            {connectionNotice.message}
          </p>
        )}
        <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {connections.map((c) => {
            const Icon = PROVIDER_ICONS[c.provider] ?? Link2;
            return (
              <Card key={c.provider} className="flex flex-col gap-3">
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-vault-700/70">
                      <Icon className="h-4 w-4 text-vault-200" />
                    </div>
                    <span className="font-semibold text-white">{PROVIDER_LABELS[c.provider] ?? c.provider}</span>
                  </div>
                  <StatusPill status={c.status} />
                </div>
                <p className="text-xs text-vault-400">
                  {c.last_synced_at ? `Last synced ${new Date(c.last_synced_at).toLocaleString()}` : "Never synced"}
                </p>
                {c.provider === "gmail" ? (
                  <div className="space-y-1 text-xs leading-relaxed text-vault-300">
                    <p>Discover your tastes from receipts and bookings, and bring important deadlines and follow-ups into Today.</p>
                    {c.account_email && <p className="break-all">{c.account_email}</p>}
                    <p>{c.pending_items ?? 0} imported emails ready for profile processing. Use Today to check for actions and reminders.</p>
                  </div>
                ) : c.provider === "gcal" ? (
                  <div className="space-y-1 text-xs leading-relaxed text-vault-300">
                    <p>Keep today's meetings and games close at hand, and let Personal Nemo learn your planned routines.</p>
                    {c.account_email && <p className="break-all">{c.account_email}</p>}
                    <p>Sync brings reminders into Today without AI charges. {c.pending_items ?? 0} events ready for optional private profile learning.</p>
                  </div>
                ) : <p className="text-xs text-vault-400">Sample data only; live connection not implemented.</p>}
                <div className="flex flex-wrap gap-2">
                  <Button
                    variant="secondary"
                    icon={Link2}
                    onClick={() => (window.location.href = api.connectProviderUrl(c.provider))}
                    disabled={api.PUBLIC_DEMO || syncing !== null || !["gmail", "gcal"].includes(c.provider)}
                  >
                    {["gmail", "gcal"].includes(c.provider) && c.account_email ? "Reconnect" : "Connect"}
                  </Button>
                  <Button
                    variant="secondary"
                    icon={RefreshCw}
                    disabled={(api.PUBLIC_DEMO && ["gmail", "gcal"].includes(c.provider)) || syncing !== null || (["gmail", "gcal"].includes(c.provider) && !c.account_email)}
                    onClick={() => handleSync(c.provider)}
                  >
                    {syncing === c.provider ? "Working..." : c.provider === "gmail" ? "Sync Gmail" : c.provider === "gcal" ? "Sync Calendar" : "Load sample"}
                  </Button>
                  {["gmail", "gcal"].includes(c.provider) && c.account_email && (
                    <Button variant="danger" disabled={api.PUBLIC_DEMO || syncing !== null} onClick={() => handleDisconnect(c.provider === "gcal" ? "gcal" : "gmail")}>Disconnect</Button>
                  )}
                  {c.provider === "gmail" && (
                    <Button icon={Sparkles} disabled={api.PUBLIC_DEMO || syncing !== null || !(c.pending_items > 0)} onClick={handleProcessEmail}>
                      Process imported emails
                    </Button>
                  )}
                  {c.provider === "gcal" && (
                    <Button icon={Sparkles} disabled={api.PUBLIC_DEMO || syncing !== null || !(c.pending_items > 0)} onClick={handleLearnCalendar}>
                      Learn from Calendar
                    </Button>
                  )}
                </div>
                {["gmail", "gcal"].includes(c.provider) && (
                  <div className="mt-auto space-y-2 text-[11px] leading-relaxed text-vault-400">
                    <p>AI learning sends selected imported text to Nebius and may incur charges. Sync itself has no model charges.</p>
                    {api.PUBLIC_DEMO && <p className="text-gold-400">Public sample connection. Live account access stays in your private installation.</p>}
                    <details>
                      <summary className="cursor-pointer text-vault-300">Sync, privacy and AI details</summary>
                      {c.provider === "gmail" ? (
                        <div className="mt-2 space-y-2">
                          <p>Each sync imports up to 25 matching receipt, booking or action/deadline emails from the last 30 days, without attachments. Action searches revisit this window so earlier receipt-only syncs do not hide requests. This is not a full inbox sync.</p>
                          <p>Process imported emails sends subject, snippet and up to 4,000 body characters per email to Nebius. A batch uses up to six paid calls: one Nano classification and up to five Super extractions. Results create private preference cards; sample cards are examples, not inbox evidence. Check Today separately for tasks.</p>
                        </div>
                      ) : (
                        <div className="mt-2 space-y-2">
                          <p>Read-only primary Calendar, today plus six days, up to 100 event instances. Sync creates reminders directly without AI calls.</p>
                          <p>Learn from Calendar sends up to five saved titles and local event IDs to Nebius in at most one paid Super call. Descriptions and attendees are not sent. Private cards describe planned routines, not confirmed attendance or likes.</p>
                        </div>
                      )}
                      <p className="mt-2">Processing runs only when clicked. Saved candidates are reused; there are no automatic retries or fallback calls. Failed model requests may still be charged. Disconnecting or deleting data still asks for confirmation.</p>
                    </details>
                  </div>
                )}
              </Card>
            );
          })}
        </div>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// My Profile
// ---------------------------------------------------------------------------


function TraitCard({ trait, onChanged }: { trait: Trait; onChanged: () => void }) {
  const [expanded, setExpanded] = useState(false);
  const [detail, setDetail] = useState<TraitDetail | null>(null);
  const [editing, setEditing] = useState(false);
  const [editValue, setEditValue] = useState(trait.value);
  const Icon = categoryIcon(trait.category);


  const toggleExpand = async () => {
    if (!expanded && !detail) {
      const d = await api.getTrait(trait.id);
      setDetail(d);
    }
    setExpanded(!expanded);
  };


  const saveEdit = async () => {
    await api.patchTrait(trait.id, { value: editValue });
    setEditing(false);
    onChanged();
  };


  const cycleTier = async () => {
    const order: PrivacyTier[] = ["share", "ask", "private"];
    const next = order[(order.indexOf(trait.privacy_tier) + 1) % order.length];
    await api.patchTrait(trait.id, { privacy_tier: next });
    onChanged();
  };


  const handleDelete = async () => {
    if (!confirm(`Delete trait "${trait.value}"? This will stop it from being re-inferred.`)) return;
    await api.deleteTrait(trait.id);
    onChanged();
  };


  return (
    <Card className="space-y-3">
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-start gap-2">
          <div className="mt-0.5 flex h-7 w-7 shrink-0 items-center justify-center rounded-lg bg-vault-700/70">
            <Icon className="h-3.5 w-3.5 text-gold-400" />
          </div>
          {editing ? (
            <input
              className="w-full rounded bg-vault-800 px-2 py-1 text-sm outline-none ring-1 ring-vault-600 focus:ring-gold-500"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
            />
          ) : (
            <p className="text-sm font-medium text-vault-50">{trait.value}</p>
          )}
        </div>
        <button disabled={api.PUBLIC_DEMO} onClick={cycleTier} title={api.PUBLIC_DEMO ? "Read-only sample privacy tier" : "Click to cycle privacy tier"}>
          <TierChip tier={trait.privacy_tier} />
        </button>
      </div>
      <ConfidenceBar value={trait.confidence} />
      <div className="flex items-center justify-between text-xs text-vault-400">
        <span>
          {Math.round(trait.confidence * 100)}% confidence · {trait.evidence_count} evidence item
          {trait.evidence_count === 1 ? "" : "s"}
        </span>
        <button className="flex items-center gap-0.5 hover:text-gold-400" onClick={toggleExpand}>
          {expanded ? "Hide" : "Evidence"}
          <motion.span animate={{ rotate: expanded ? 180 : 0 }}>
            <ChevronDown className="h-3 w-3" />
          </motion.span>
        </button>
      </div>
      {trait.summary && <p className="text-xs italic text-vault-400">{trait.summary}</p>}
      <AnimatePresence>
        {expanded && detail && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            className="overflow-hidden"
          >
            <div className="space-y-1 rounded-lg bg-vault-800/70 p-2.5 text-xs text-vault-300">
              {detail.facts.map((f) => (
                <p key={f.id} className="flex gap-1.5">
                  <span className="text-gold-400">•</span>
                  {f.statement}
                </p>
              ))}
              {detail.facts.length === 0 && <p>No supporting facts recorded.</p>}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
      <div className="flex gap-2 pt-1">
        {editing ? (
          <>
            <Button variant="secondary" icon={Save} onClick={saveEdit}>
              Save
            </Button>
            <Button variant="ghost" icon={X} onClick={() => setEditing(false)}>
              Cancel
            </Button>
          </>
        ) : (
          <Button disabled={api.PUBLIC_DEMO} variant="secondary" icon={Pencil} onClick={() => setEditing(true)}>
            Edit
          </Button>
        )}
        <Button disabled={api.PUBLIC_DEMO} variant="danger" icon={Trash2} onClick={handleDelete}>
          Delete
        </Button>
      </div>
    </Card>
  );
}


function LifeField({
  label, value, onChange, maxLength = 120, required = false, hint,
}: {
  label: string; value: string; onChange: (value: string) => void;
  maxLength?: number; required?: boolean; hint?: string;
}) {
  const id = useId();
  return (
    <div className="flex min-w-0 flex-col gap-1.5 text-xs text-vault-300">
      <label htmlFor={id}>{label}{required ? " *" : ""}</label>
      <input
        id={id}
        aria-describedby={hint ? `${id}-hint` : undefined}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        maxLength={maxLength}
        required={required}
        className="w-full rounded-xl border border-vault-600 bg-vault-900/70 px-3 py-2.5 text-sm text-white outline-none focus:border-gold-500"
      />
      {hint && <span id={`${id}-hint`} className="text-vault-400">{hint}</span>}
    </div>
  );
}


function MyLifeSection() {
  const [saved, setSaved] = useState<MyLife | null>(null);
  const [draft, setDraft] = useState<MyLife | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [status, setStatus] = useState("");
  const dirty = JSON.stringify(saved) !== JSON.stringify(draft);


  const load = () => api.getMyLife()
    .then((data) => {
      setSaved(data);
      setDraft(data);
    })
    .catch((err: unknown) => {
      setError(err instanceof Error ? err.message : "Could not load My Life.");
    })
    .finally(() => setLoading(false));


  useEffect(() => { void load(); }, []);


  const save = async () => {
    if (!draft || saving) return;
    setSaving(true);
    setError("");
    setStatus("");
    try {
      const data = await api.saveMyLife(draft);
      setSaved(data);
      setDraft(data);
      setStatus("My Life saved. Your next personalized answer will use these details when relevant.");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save My Life.");
    } finally {
      setSaving(false);
    }
  };


  const update = (next: MyLife) => { setDraft(next); setStatus(""); };


  return (
    <Card className="space-y-5">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <h2 className="flex items-center gap-2 text-lg font-semibold text-white">
            <MapPin className="h-5 w-5 text-gold-400" /> My Life
          </h2>
          <p className="mt-1 text-sm text-vault-300">Your home, work, and the people you live with.</p>
        </div>
        <span className="flex items-center gap-1.5 rounded-full border border-vault-600 px-3 py-1 text-xs text-vault-300">
          <Lock className="h-3 w-3" /> Not shared via MCP
        </span>
      </div>
      <p className="text-xs leading-relaxed text-vault-300">
        Used in your personalized Chat and comparison answers through Nemotron, not baseline answers.
        Saving is free. Share only broad areas, not exact addresses; nicknames are fine.
        Leave anything unknown blank. Family preferences stay separate from your own.
      </p>
      {error && <p role="alert" className="text-sm text-private-500">{error}</p>}
      {loading ? <p role="status" className="text-sm text-vault-300">Loading My Life...</p> : !draft ? (
        <Button onClick={() => { setLoading(true); setError(""); void load(); }} variant="secondary" icon={RefreshCw}>Retry loading My Life</Button>
      ) : (
        <form onSubmit={(event) => { event.preventDefault(); void save(); }} className="space-y-5">
          <fieldset disabled={api.PUBLIC_DEMO || saving} className="min-w-0 space-y-5 disabled:opacity-60">
            <div className="grid gap-5 md:grid-cols-2">
              <section className="space-y-3">
                <h3 className="text-sm font-semibold text-gold-400">Current home</h3>
                <LifeField label="Home city" value={draft.home_city} onChange={(home_city) => update({ ...draft, home_city })} />
                <LifeField label="Home area / neighborhood" value={draft.home_area} onChange={(home_area) => update({ ...draft, home_area })} />
              </section>
              <section className="space-y-3">
                <h3 className="text-sm font-semibold text-gold-400">Work</h3>
                <LifeField label="Office city" value={draft.office_city} onChange={(office_city) => update({ ...draft, office_city })} />
                <LifeField label="Office area / neighborhood" value={draft.office_area} onChange={(office_area) => update({ ...draft, office_area })} />
                <LifeField label="Work arrangement" maxLength={300} value={draft.work_arrangement} onChange={(work_arrangement) => update({ ...draft, work_arrangement })} hint="For example: remote, hybrid, or office days." />
              </section>
            </div>
            <section className="space-y-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <h3 className="text-sm font-semibold text-gold-400">People I live with</h3>
                <Button variant="secondary" disabled={draft.household_members.length >= 10} onClick={() => update({
                  ...draft,
                  household_members: [...draft.household_members, { name: "", relationship: "", likes: "", dislikes: "", dietary_needs: "" }],
                })}>Add household member</Button>
              </div>
              {draft.household_members.length === 0 && (
                <p className="text-xs text-vault-400">No household members added. This does not imply you live alone.</p>
              )}
              {draft.household_members.map((member, index) => (
                <fieldset key={index} className="min-w-0 rounded-xl border border-vault-600 p-4">
                  <legend className="px-2 text-xs text-vault-300">Household member {index + 1}</legend>
                  <div className="grid gap-3 sm:grid-cols-2">
                    {([
                      ["name", "Name or nickname", 80],
                      ["relationship", "Relationship", 80],
                      ["likes", "Likes", 300],
                      ["dislikes", "Dislikes", 300],
                      ["dietary_needs", "Dietary needs / allergies", 300],
                    ] as const).map(([key, label, maxLength]) => (
                      <LifeField key={key} label={label} value={member[key]} maxLength={maxLength}
                        required={key === "name" || key === "relationship"}
                        onChange={(value) => update({ ...draft, household_members: draft.household_members.map((entry, i) => i === index ? { ...entry, [key]: value } : entry) })}
                      />
                    ))}
                  </div>
                  <div className="mt-3">
                    <Button variant="danger" icon={Trash2} onClick={() => update({
                      ...draft, household_members: draft.household_members.filter((_, i) => i !== index),
                    })}>Remove member {index + 1}</Button>
                  </div>
                </fieldset>
              ))}
            </section>
            <div className="flex flex-wrap items-center gap-3">
              <button type="submit" disabled={!dirty || saving} className="rounded-xl bg-gold-500 px-4 py-2 text-sm font-medium text-vault-950 disabled:cursor-not-allowed disabled:opacity-50">
                {saving ? "Saving..." : "Save My Life"}
              </button>
              <Button variant="ghost" disabled={!dirty} onClick={() => { setDraft(saved); setStatus(""); setError(""); }}>Cancel changes</Button>
              {dirty && <span className="text-xs text-ask-500">Unsaved changes</span>}
            </div>
          </fieldset>
          {status && <p role="status" className="text-sm text-share-500">{status}</p>}
        </form>
      )}
    </Card>
  );
}


function ActivityDetails({ onChanged }: { onChanged: () => void }) {
  const [data, setData] = useState<api.ActivityData | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const load = async () => {
    try { setData(await api.getActivity()); setError(""); }
    catch (err) { setError(err instanceof Error ? err.message : "Could not load activity."); }
  };
  useEffect(() => {
    api.getActivity().then(setData).catch((err: unknown) =>
      setError(err instanceof Error ? err.message : "Could not load activity."));
  }, []);
  const act = async (id: string, remove: boolean) => {
    if (busy) return;
    if (remove && !confirm("Remove this activity from counts and summaries? All duplicate receipts for this activity will be excluded. Imported email evidence is retained.")) return;
    setBusy(true);
    try {
      if (remove) await api.removeActivity(id);
      else await api.retryActivity(id);
      await load();
      onChanged();
    } catch (err) { setError(err instanceof Error ? err.message : "Activity update failed."); }
    finally { setBusy(false); }
  };
  const refresh = async () => {
    if (busy) return;
    setBusy(true);
    setNotice("");
    try {
      const result = await api.refreshPreferences();
      await load();
      setNotice(result.message);
      onChanged();
    } catch (err) { setError(err instanceof Error ? err.message : "Could not refresh preferences."); }
    finally { setBusy(false); }
  };
  const labels: Record<string, string> = {
    foods: "Most ordered foods", restaurants: "Most ordered restaurants",
    sports: "Most booked sports", venues: "Frequently booked sports venues", destinations: "Booked destinations",
  };
  return (
    <Card>
      <details>
        <summary className="cursor-pointer font-semibold text-gold-400">Activity details and ingestion review</summary>
        <p className="my-3 text-xs text-vault-300">
          All imported activity, not confirmed likes or attendance. Rankings are private here.
          Profile cards describe grouped food and sports patterns, not leaderboards. Exact names and counts stay here.
          Editing or changing a card&apos;s privacy freezes that snapshot; new activity continues updating these private rankings.
          Sample cards and old single-email cards are unchanged.
        </p>
        <Button disabled={api.PUBLIC_DEMO || busy} variant="secondary" onClick={() => void refresh()}>Refresh preference cards (no model calls)</Button>
        {notice && <p role="status" className="my-2 text-xs text-gold-400">{notice}</p>}
        {error && <p role="alert" className="text-sm text-private-500">{error}</p>}
        {!data && <Button onClick={() => void load()} variant="secondary">Load activity</Button>}
        {data && (
          <div className="space-y-4">
            {Object.entries(data.rankings).map(([dimension, entries]) => (
              <details key={dimension} className="rounded-lg border border-vault-600 p-3">
                <summary className="cursor-pointer text-sm text-white">{labels[dimension] ?? dimension} ({entries.length})</summary>
                <ul className="mt-2 space-y-2 break-words text-xs text-vault-200">
                  {entries.map((entry) => (
                    <li key={entry.name}>
                      {entry.name} — {entry.count} distinct {dimension === "foods" || dimension === "restaurants" ? "orders" : "bookings"}
                      {entry.children.length > 0 && (
                        <ul className="ml-4 mt-1 list-disc text-vault-300">
                          {entry.children.map((child) => <li key={child.name}>{child.name}: {child.count}</li>)}
                        </ul>
                      )}
                    </li>
                  ))}
                </ul>
              </details>
            ))}
            <details>
              <summary className="cursor-pointer text-sm text-white">Activity history ({data.events.length})</summary>
              <div className="mt-2 max-h-96 space-y-3 overflow-y-auto">
                {data.events.map((event) => (
                  <div key={event.fact_id} className="rounded-lg border border-vault-600 p-3 text-xs text-vault-200">
                    <p className="mb-2 break-words">{event.statement} ({event.source_count} source emails)</p>
                    <Button disabled={api.PUBLIC_DEMO || busy} variant="danger" onClick={() => void act(event.fact_id, true)}>Remove activity</Button>
                  </div>
                ))}
              </div>
            </details>
            <details>
              <summary className="cursor-pointer text-sm text-white">Skipped / needs review ({data.review.length})</summary>
              <p className="my-2 text-xs text-vault-300">Unknown emails are not discarded. Requeueing is free; the next Home batch needs paid-call consent.</p>
              <div className="max-h-72 space-y-2 overflow-y-auto">
                {data.review.map((entry) => (
                  <div key={entry.email_id} className="rounded border border-vault-600 p-2 text-xs text-vault-300">
                    <p className="break-all">{entry.email_id}: {entry.type} — {entry.status}</p>
                    {entry.status !== "failed" ? <Button disabled={api.PUBLIC_DEMO || busy} variant="secondary" onClick={() => void act(entry.email_id, false)}>Requeue classification</Button>
                      : <p>Retry using Home. Saved classifications are reused; a saved extraction is revalidated locally without another model call.</p>}
                  </div>
                ))}
              </div>
            </details>
          </div>
        )}
      </details>
    </Card>
  );
}


export function ProfilePage() {
  const [profile, setProfile] = useState<ProfileByCategory>({});
  const [rebuilding, setRebuilding] = useState(false);
  const [expandedCategories, setExpandedCategories] = useState<string[]>([]);


  const refresh = () => api.getProfile().then(setProfile);


  useEffect(() => {
    refresh();
  }, []);


  const handleRebuild = async () => {
    setRebuilding(true);
    try {
      await api.rebuildProfile();
      await refresh();
    } catch (e) {
      alert(String(e));
    } finally {
      setRebuilding(false);
    }
  };


  const categories = Object.keys(profile);


  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-white">My Profile</h1>
          <p className="text-xs text-vault-400">Evidence-backed traits, grouped by category.</p>
        </div>
        <Button onClick={handleRebuild} disabled={api.PUBLIC_DEMO || rebuilding} icon={rebuilding ? RefreshCw : Gauge}>
          {rebuilding ? "Rebuilding..." : "Rebuild profile"}
        </Button>
      </div>
      {api.PUBLIC_DEMO && <p className="text-xs text-gold-400">Read-only sample profile. Browse the preference cards and their evidence; personal changes stay in your private installation.</p>}
      <MyLifeSection />
      <ActivityDetails onChanged={refresh} />
      {categories.length === 0 && (
        <Card className="text-center text-sm text-vault-400">
          No traits yet. Load demo data from the Home tab to get started.
        </Card>
      )}
      {categories.map((category) => {
        const Icon = categoryIcon(category);
        return (
          <div key={category}>
            <SectionHeading icon={Icon}>{CATEGORY_LABELS[category] ?? category}</SectionHeading>
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
              {(expandedCategories.includes(category) ? profile[category] : profile[category].slice(0, 10)).map((trait) => (
                <TraitCard key={trait.id} trait={trait} onChanged={refresh} />
              ))}
            </div>
            {profile[category].length > 10 && (
              <Button variant="ghost" onClick={() => setExpandedCategories((current) =>
                current.includes(category) ? current.filter((entry) => entry !== category) : [...current, category])}>
                {expandedCategories.includes(category) ? "Show fewer" : `View all ${profile[category].length}`}
              </Button>
            )}
          </div>
        );
      })}
    </div>
  );
}


// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------


interface ChatMsg {
  role: "user" | "assistant";
  content: string;
  usedTraitIds?: string[];
  webSearchUsed?: boolean;
  sources?: api.WebSource[];
}


function WebSearchControls({ enabled, query, onEnabledChange, onQueryChange, disabled }: {
  enabled: boolean;
  query: string;
  onEnabledChange: (enabled: boolean) => void;
  onQueryChange: (query: string) => void;
  disabled: boolean;
}) {
  const queryId = useId();
  return (
    <fieldset disabled={disabled} className="space-y-2 rounded-xl border border-vault-700 bg-vault-900/50 p-3 text-xs">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <label className="flex items-center gap-2 text-vault-200">
          <input type="checkbox" checked={enabled} onChange={(event) => onEnabledChange(event.target.checked)} />
          Use web search
        </label>
        <span className="text-vault-400">Powered by <a href="https://tavily.com" target="_blank" rel="noopener noreferrer" className="text-gold-400 underline">Tavily</a></span>
      </div>
      {enabled ? (
        <>
          <label htmlFor={queryId} className="block text-vault-300">Public search query</label>
          <input id={queryId} value={query} maxLength={300} onChange={(event) => onQueryChange(event.target.value)}
            placeholder="Public topic and city, if needed" required
            className="w-full min-w-0 rounded-lg border border-vault-600 bg-vault-900 px-3 py-2 text-vault-100 outline-none focus:border-gold-500/50" />
          <p className="text-vault-400">Only this query goes to Tavily—not your profile, emails or chat history. Do not enter private data. Searches use Tavily credits.</p>
          {!query.trim() && <p role="status" className="text-gold-400">Enter a public search query to continue.</p>}
        </>
      ) : <p className="text-vault-400">Optional public web search is off. No search runs until you enable it and submit.</p>}
    </fieldset>
  );
}


function WebSources({ used, sources }: { used: boolean; sources: api.WebSource[] }) {
  if (!used) return null;
  return (
    <aside aria-label="Tavily web sources" className="mt-2 min-w-0 space-y-1 break-words text-xs text-vault-300 md:col-span-2">
      <p className="font-medium">Web search powered by Tavily</p>
      {sources.length ? <ul className="space-y-1">
        {sources.map((source,index) => <li key={`${source.url}-${index}`}>
          <a href={source.url} target="_blank" rel="noopener noreferrer" className="text-gold-400 underline">{source.title}</a>
        </li>)}
      </ul> : <p>No web sources found. This answer is not web-verified.</p>}
    </aside>
  );
}


function Avatar({ role }: { role: "user" | "assistant" }) {
  if (role === "assistant") {
    return (
      <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-violet-500 to-gold-400 shadow shadow-violet-500/30">
        <Bot className="h-3.5 w-3.5 text-vault-950" />
      </div>
    );
  }
  return (
    <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-vault-700">
      <UserIcon className="h-3.5 w-3.5 text-vault-200" />
    </div>
  );
}


function TypingIndicator() {
  return (
    <div className="flex items-center gap-1 px-1 py-1">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="typing-dot h-1.5 w-1.5 rounded-full bg-vault-300"
          style={{ animationDelay: `${i * 0.15}s` }}
        />
      ))}
    </div>
  );
}


function AssistantResponse({ content }: { content: string }) {
  return (
    <div className="assistant-response">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        skipHtml
        components={{
          table: ({ children }) => (
            <div className="response-table" role="region" aria-label="Response table" tabIndex={0}>
              <table>{children}</table>
            </div>
          ),
          a: ({ href, children }) => <a href={href} target="_blank" rel="noopener noreferrer">{children}</a>,
          img: ({ alt }) => <span>{alt}</span>,
        }}
      >
        {content}
      </ReactMarkdown>
    </div>
  );
}


export function ChatPage() {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [mode, setMode] = useState<"personalized" | "baseline">("personalized");
  const [sending, setSending] = useState(false);
  const [webEnabled, setWebEnabled] = useState(false);
  const [webQuery, setWebQuery] = useState("");
  const bottomRef = useRef<HTMLDivElement>(null);


  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);


  const send = async () => {
    if (!input.trim() || sending || (webEnabled && !webQuery.trim())) return;
    const question = input;
    setInput("");
    setMessages((m) => [...m, { role: "user", content: question }, { role: "assistant", content: "" }]);
    setSending(true);
    try {
      const { usedTraitIds, webSearchUsed, sources } = await api.sendChatMessage(question, mode, (chunk) => {
        setMessages((m) => {
          const copy = [...m];
          copy[copy.length - 1] = { ...copy[copy.length - 1], content: copy[copy.length - 1].content + chunk };
          return copy;
        });
      }, webEnabled ? webQuery.trim() : undefined);
      setMessages((m) => {
        const copy = [...m];
        copy[copy.length - 1] = { ...copy[copy.length - 1], usedTraitIds, webSearchUsed, sources };
        return copy;
      });
    } catch (e) {
      setMessages((current) => {
        const copy = [...current];
        const last = copy[copy.length - 1];
        copy[copy.length - 1] = { ...last, content: `${last.content}${last.content ? "\n\n" : ""}Error: ${String(e)}` };
        return copy;
      });
    } finally {
      setSending(false);
    }
  };


  return (
    <div className="flex min-h-[72vh] flex-col gap-3">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-bold text-white">Chat</h1>
        <div className="glass flex gap-1 rounded-xl p-1 text-xs">
          {(
            [
              { id: "personalized", icon: Sparkles },
              { id: "baseline", icon: Shapes },
            ] as const
          ).map(({ id, icon: Icon }) => (
            <button
              key={id}
              onClick={() => setMode(id)}
              className={`flex items-center gap-1 rounded-lg px-3 py-1.5 transition ${
                mode === id ? "bg-gold-500 text-vault-950" : "text-vault-300 hover:text-white"
              }`}
            >
              <Icon className="h-3 w-3" />
              {id}
            </button>
          ))}
        </div>
      </div>
      <WebSearchControls enabled={webEnabled} query={webQuery} onEnabledChange={setWebEnabled} onQueryChange={setWebQuery} disabled={sending} />
      <Card className="min-h-48 max-h-[55vh] flex-1 overflow-y-auto">
        {messages.length === 0 && (
          <div className="flex h-full flex-col items-center justify-center gap-2 text-center text-vault-400">
            <Bot className="h-8 w-8 text-vault-500" />
            <p className="text-sm">Ask about food, music, shows, fitness or style - I'll use what I know about you.</p>
          </div>
        )}
        <div className="space-y-4">
          {messages.map((m, i) => (
            <div key={i} className={`flex gap-2 ${m.role === "user" ? "flex-row-reverse" : ""}`}>
              <Avatar role={m.role} />
              <div className={`min-w-0 max-w-[90%] sm:max-w-[85%] ${m.role === "user" ? "text-right" : "flex-1 text-left"}`}>
                <div
                  className={`max-w-full rounded-2xl px-4 py-3 text-sm ${
                    m.role === "user"
                      ? "inline-block whitespace-pre-wrap break-words bg-gradient-to-br from-gold-500 to-gold-400 text-vault-950"
                      : "glass text-vault-100"
                  }`}
                >
                  {m.content
                    ? m.role === "assistant" ? <AssistantResponse content={m.content} /> : m.content
                    : <TypingIndicator />}
                </div>
                {m.role === "assistant" && m.usedTraitIds && m.usedTraitIds.length > 0 && (
                  <p className="mt-1 flex items-center gap-1 text-xs text-vault-400">
                    <BadgeCheck className="h-3 w-3 text-share-500" />
                    Used {m.usedTraitIds.length} trait(s) from your profile
                  </p>
                )}
                {m.role === "assistant" && <WebSources used={m.webSearchUsed ?? false} sources={m.sources ?? []} />}
              </div>
            </div>
          ))}
          <div ref={bottomRef} />
        </div>
      </Card>
      <div className="flex gap-2">
        <input
          className="min-w-0 flex-1 rounded-xl border border-vault-700 bg-vault-900/70 px-4 py-2.5 text-sm outline-none ring-0 focus:border-gold-500/50"
          placeholder="Ask anything..."
          value={input}
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && send()}
        />
        <Button onClick={send} disabled={sending || !input.trim() || (webEnabled && !webQuery.trim())} icon={Send}>
          Send
        </Button>
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// See the Difference (compare)
// ---------------------------------------------------------------------------


const SAMPLE_QUESTIONS = [
  "Where should I go for dinner tonight?",
  "What movie should I watch this weekend?",
  "Suggest an outfit for a casual day out.",
];


export function ComparePage() {
  const [question, setQuestion] = useState("");
  const [result, setResult] = useState<api.CompareResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [webEnabled, setWebEnabled] = useState(false);
  const [webQuery, setWebQuery] = useState("");
  const [error, setError] = useState("");


  const run = async (q: string) => {
    if (!q.trim() || loading || (webEnabled && !webQuery.trim())) return;
    setQuestion(q);
    setLoading(true);
    setResult(null);
    setError("");
    try {
      const res = await api.compareChat(q, webEnabled ? webQuery.trim() : undefined);
      setResult(res);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Comparison failed.");
    } finally {
      setLoading(false);
    }
  };


  return (
    <div className="space-y-4">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-bold text-white">
          <Scale className="h-5 w-5 text-gold-400" />
          See the Difference
        </h1>
        <p className="text-xs text-vault-400">The same question, answered with and without your profile.</p>
      </div>
      <WebSearchControls enabled={webEnabled} query={webQuery} onEnabledChange={setWebEnabled} onQueryChange={setWebQuery} disabled={loading} />
      {error && <p role="alert" className="break-words text-sm text-private-500">{error}</p>}
      <Card className="flex flex-wrap items-center gap-2">
        <input
          className="min-w-0 flex-1 rounded-xl border border-vault-700 bg-vault-900/70 px-4 py-2.5 text-sm outline-none focus:border-gold-500/50"
          placeholder="Ask a question..."
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && run(question)}
        />
        <Button onClick={() => run(question)} disabled={loading || !question.trim() || (webEnabled && !webQuery.trim())} icon={loading ? RefreshCw : Sparkles}>
          {loading ? "Thinking..." : "Compare"}
        </Button>
      </Card>
      <div className="flex flex-wrap gap-2">
        {SAMPLE_QUESTIONS.map((q) => (
          <button
            key={q}
            disabled={loading || (webEnabled && !webQuery.trim())}
            onClick={() => run(q)}
            className="rounded-full border border-vault-600 px-3 py-1 text-xs text-vault-300 transition hover:border-gold-500/40 hover:text-white"
          >
            {q}
          </button>
        ))}
      </div>
      <AnimatePresence>
        {result && (
          <motion.div
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            className="grid grid-cols-1 gap-4 md:grid-cols-2"
          >
            <Card className="min-w-0 space-y-4">
              <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-vault-400">
                <Shapes className="h-3.5 w-3.5" />
                Without Personal Nemo
              </p>
              <AssistantResponse content={result.baseline} />
            </Card>
            <Card glow className="min-w-0 space-y-4 border-gold-500/30">
              <p className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wide text-gold-400">
                <Sparkles className="h-3.5 w-3.5" />
                With Personal Nemo
              </p>
              <AssistantResponse content={result.personalized} />
            </Card>
            <WebSources used={result.web_search_used} sources={result.sources} />
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Know Me Better
// ---------------------------------------------------------------------------


export function KnowMeBetterPage() {
  const [topics, setTopics] = useState<QATopic[]>([]);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [topic, setTopic] = useState<string | null>(null);
  const [question, setQuestion] = useState<string | null>(null);
  const [answer, setAnswer] = useState("");
  const [progress, setProgress] = useState({ answered: 0, total: 3 });
  const [done, setDone] = useState(false);


  const refreshTopics = () => api.getQaTopics().then(setTopics);


  useEffect(() => {
    refreshTopics();
  }, []);


  const start = async (t: string) => {
    const res = await api.startQaSession(t);
    setSessionId(res.session_id);
    setTopic(t);
    setQuestion(res.question);
    setProgress(res.progress);
    setDone(false);
  };


  const submitAnswer = async () => {
    if (!sessionId || !answer.trim()) return;
    const res = await api.answerQaQuestion(sessionId, answer);
    setAnswer("");
    setProgress(res.progress);
    if (res.done) {
      setDone(true);
      refreshTopics();
    } else {
      setQuestion(res.question ?? null);
    }
  };


  if (topic && !done) {
    const TopicIcon = categoryIcon(topic);
    return (
      <div className="space-y-4">
        <h1 className="flex items-center gap-2 text-xl font-bold text-white">
          <TopicIcon className="h-5 w-5 text-gold-400" />
          Know Me Better: {CATEGORY_LABELS[topic] ?? topic}
        </h1>
        <Card glow className="border-gold-500/30">
          <div className="mb-3 flex items-center gap-1.5">
            {Array.from({ length: progress.total }).map((_, i) => (
              <span
                key={i}
                className={`h-1.5 flex-1 rounded-full ${i < progress.answered ? "bg-share-500" : i === progress.answered ? "bg-gold-500" : "bg-vault-700"}`}
              />
            ))}
          </div>
          <p className="text-xs text-vault-400">
            Question {progress.answered + 1} of {progress.total}
          </p>
          <p className="mt-2 text-base font-medium text-vault-50">{question}</p>
          <input
            className="mt-3 w-full rounded-xl border border-vault-700 bg-vault-900/70 px-4 py-2.5 text-sm outline-none focus:border-gold-500/50"
            value={answer}
            onChange={(e) => setAnswer(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && submitAnswer()}
            placeholder="Your answer..."
            autoFocus
          />
          <div className="mt-3 flex gap-2">
            <Button onClick={submitAnswer} icon={Send}>
              Submit
            </Button>
            <Button variant="ghost" icon={ArrowLeft} onClick={() => setTopic(null)}>
              Back to topics
            </Button>
          </div>
        </Card>
      </div>
    );
  }


  return (
    <div className="space-y-4">
      <div>
        <h1 className="text-xl font-bold text-white">Know Me Better</h1>
        <p className="text-xs text-vault-400">Answer a few quick questions to sharpen your lowest-confidence traits.</p>
      </div>
      <AnimatePresence>
        {done && (
          <motion.div initial={{ opacity: 0, y: -6 }} animate={{ opacity: 1, y: 0 }}>
            <Card className="flex items-center gap-2 border-share-500/40 text-share-500">
              <CheckCircle2 className="h-4 w-4" />
              Thanks! Your profile was updated.
            </Card>
          </motion.div>
        )}
      </AnimatePresence>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
        {topics.map((t) => {
          const Icon = categoryIcon(t.topic);
          return (
            <Card key={t.topic} className="space-y-3">
              <div className="flex items-center gap-2">
                <div className="flex h-8 w-8 items-center justify-center rounded-lg bg-vault-700/70">
                  <Icon className="h-4 w-4 text-gold-400" />
                </div>
                <p className="font-medium text-vault-50">{CATEGORY_LABELS[t.topic] ?? t.topic}</p>
              </div>
              <ConfidenceBar value={t.confidence} />
              <p className="text-xs text-vault-400">{Math.round(t.confidence * 100)}% confidence</p>
              <Button variant="secondary" icon={Sparkles} onClick={() => start(t.topic)}>
                Improve this
              </Button>
            </Card>
          );
        })}
      </div>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Privacy and MCP
// ---------------------------------------------------------------------------


export function ApprovalInbox({ expanded }: { expanded: boolean }) {
  const [requests, setRequests] = useState<MCPApproval[]>([]);
  const [error, setError] = useState("");
  const [review, setReview] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState("");
  const seen = useRef(new Set<string>());
  const refresh = async () => {
    try {
      const data = await api.getMcpApprovals();
      const fresh = data.filter((item) => item.status === "pending" && !seen.current.has(item.id));
      data.forEach((item) => seen.current.add(item.id));
      if (fresh.length) setNotice(`${fresh.length} new information access request(s). Review before sharing.`);
      setRequests(data);
      setError("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not check approval requests.");
    }
  };
  useEffect(() => {
    void refresh();
    const timer = setInterval(() => { void refresh(); }, 5000);
    return () => clearInterval(timer);
  }, []);
  const decide = async (id: string, decision: "allow" | "deny") => {
    setBusy(id);
    try {
      await api.decideMcpApproval(id, decision);
      setNotice(decision === "allow" ? "Allowed once. The requesting client must check again before expiry." : "Request denied. Nothing shared.");
      await refresh();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not save your decision.");
    } finally {
      setBusy(null);
    }
  };
  const pending = requests.filter((item) => item.status === "pending");
  return (
    <aside aria-label="MCP approval requests" className="space-y-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <button onClick={() => setReview(!review)} aria-expanded={expanded || review}
          className="flex items-center gap-2 rounded-xl border border-ask-500/40 bg-vault-900/80 px-3 py-2 text-sm text-gold-400">
          <ShieldCheck className="h-4 w-4" /> Pending requests ({pending.length})
        </button>
        <p role="status" className="text-xs text-vault-300">{notice}</p>
      </div>
      {error && <p role="alert" className="text-xs text-private-500">{error} Retrying every 5 seconds.</p>}
      {(expanded || review) && (
        <Card className="space-y-4">
          <h2 className="text-lg font-semibold text-white">Information access requests</h2>
          <p className="text-xs text-vault-300">
            Review the exact values below. Allow once does not change privacy tiers.
            Private traits and My Life are never included. Client names are self-reported, not verified identities.
            Requests expire after five minutes, even after approval.
          </p>
          {requests.length === 0 && <p className="text-sm text-vault-300">No requests yet.</p>}
          {requests.map((request) => (
            <section key={request.id} className="space-y-3 rounded-xl border border-vault-600 p-4">
              <div className="flex flex-wrap justify-between gap-2 text-sm">
                <h3 className="font-semibold text-white">{request.client_name ?? "Unknown client"} · {CATEGORY_LABELS[request.category] ?? request.category}</h3>
                <span className="text-gold-400">{request.status === "approved" ? "Approved · awaiting collection" : request.status}</span>
              </div>
              <p className="break-all text-xs text-vault-400">Request {request.id} · Expires {new Date(request.expires_at).toLocaleTimeString()}</p>
              <ul className="space-y-2 text-sm text-vault-200">
                {request.traits.map((trait) => (
                  <li key={trait.id} className="whitespace-pre-wrap break-words rounded-lg bg-vault-900/60 p-3">
                    {trait.value}{trait.summary && <p className="mt-1 text-xs text-vault-300">{trait.summary}</p>}
                  </li>
                ))}
              </ul>
              {request.status === "pending" && (
                <div className="flex flex-wrap gap-2">
                  <Button disabled={busy !== null} onClick={() => decide(request.id, "allow")} icon={Check}>Allow once</Button>
                  <Button disabled={busy !== null} onClick={() => decide(request.id, "deny")} variant="danger" icon={X}>Deny</Button>
                </div>
              )}
            </section>
          ))}
        </Card>
      )}
    </aside>
  );
}


export function PrivacyPage() {
  const [categoryDefaults, setCategoryDefaults] = useState<Record<string, PrivacyTier>>({});
  const [log, setLog] = useState<MCPLogEntry[]>([]);
  const [token, setToken] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);


  const refresh = () => {
    api.getPrivacy().then((p) => setCategoryDefaults(p.category_defaults));
    api.getMcpLog().then(setLog);
  };


  useEffect(() => {
    refresh();
  }, []);


  const handleSetTier = async (category: string, tier: PrivacyTier) => {
    await api.setCategoryTier(category, tier);
    refresh();
  };


  const handleGenerateToken = async () => {
    const res = await api.generateMcpToken();
    setToken(res.token);
  };


  const copyToken = () => {
    if (!token) return;
    navigator.clipboard.writeText(token);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };


  const handleDeleteAll = async () => {
    if (!confirm("This permanently deletes all your raw data, facts, traits and tokens. Continue?")) return;
    await api.deleteAllData();
    alert("All data deleted.");
    refresh();
  };


  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-bold text-white">
          <Shield className="h-5 w-5 text-gold-400" />
          Privacy & MCP
        </h1>
        <p className="text-xs text-vault-400">Control exactly what external AIs can see about you.</p>
      </div>


      <Card>
        <SectionHeading icon={ShieldCheck}>Category defaults</SectionHeading>
        <div className="space-y-2">
          {Object.entries(categoryDefaults).map(([category, tier]) => {
            const Icon = categoryIcon(category);
            return (
              <div key={category} className="flex items-center justify-between gap-2 rounded-lg px-2 py-1.5 hover:bg-vault-800/40">
                <span className="flex items-center gap-2 text-sm text-vault-200">
                  <Icon className="h-3.5 w-3.5 text-vault-400" />
                  {CATEGORY_LABELS[category] ?? category}
                </span>
                <div className="flex gap-1">
                  {(["share", "ask", "private"] as PrivacyTier[]).map((t) => {
                    const meta = TIER_META[t];
                    const TierIcon = meta.icon;
                    return (
                      <button
                        key={t}
                        onClick={() => handleSetTier(category, t)}
                        className={`flex items-center gap-1 rounded-full border px-2 py-0.5 text-xs transition ${
                          tier === t ? meta.classes : "border-vault-700 text-vault-500 hover:text-vault-300"
                        }`}
                      >
                        <TierIcon className="h-3 w-3" />
                        {meta.label}
                      </button>
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </Card>


      <Card glow className="border-violet-500/30">
        <SectionHeading icon={KeyRound}>Connect an external AI via MCP</SectionHeading>
        <p className="text-xs text-vault-400">MCP server URL</p>
        <code className="mt-1 block rounded-lg bg-vault-800/80 px-3 py-2 text-xs text-vault-200">{api.mcpServerUrl()}</code>
        <div className="mt-3 flex flex-wrap items-center gap-2">
          <Button variant="secondary" icon={KeyRound} onClick={handleGenerateToken}>
            Generate token
          </Button>
          {token && (
            <>
              <code className="rounded-lg bg-vault-800/80 px-3 py-2 text-xs text-vault-200">{token}</code>
              <Button variant="secondary" icon={copied ? Check : Copy} onClick={copyToken}>
                {copied ? "Copied" : "Copy"}
              </Button>
            </>
          )}
        </div>
        <ol className="mt-4 space-y-2 text-xs text-vault-400">
          {[
            "In Claude, add a custom connector with the MCP server URL above.",
            "Paste the bearer token when prompted for authentication.",
            "Share traits are available directly. Ask traits require Allow once in Pending requests; Private traits stay blocked.",
          ].map((step, i) => (
            <li key={i} className="flex gap-2">
              <span className="flex h-4 w-4 shrink-0 items-center justify-center rounded-full bg-violet-500/20 text-[10px] text-violet-400">
                {i + 1}
              </span>
              {step}
            </li>
          ))}
        </ol>
      </Card>


      <Card>
        <SectionHeading icon={Activity}>Live access log</SectionHeading>
        <div className="space-y-1 text-xs">
          {log.length === 0 && <p className="text-vault-400">No external access yet.</p>}
          {log.map((entry) => (
            <div key={entry.id} className="flex items-center justify-between border-b border-vault-800/70 py-1.5">
              <span className="text-vault-300">
                {entry.client_name ?? "Unknown client"} called <code className="text-vault-100">{entry.tool}</code>
              </span>
              <span className="flex items-center gap-2">
                <span className="flex items-center gap-1 text-share-500">
                  <Eye className="h-3 w-3" />
                  {entry.traits_returned}
                </span>
                <span className="flex items-center gap-1 text-private-500">
                  <EyeOff className="h-3 w-3" />
                  {entry.traits_blocked}
                </span>
              </span>
            </div>
          ))}
        </div>
      </Card>


      <Card className="border-private-500/30">
        <SectionHeading icon={AlertTriangle}>Danger zone</SectionHeading>
        <Button variant="danger" icon={Trash2} onClick={handleDeleteAll}>
          Delete all my data
        </Button>
      </Card>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Under the Hood
// ---------------------------------------------------------------------------


function StatCard({
  icon: Icon,
  label,
  value,
  accent,
}: {
  icon: typeof Zap;
  label: string;
  value: string;
  accent?: "gold" | "share";
}) {
  const color = accent === "share" ? "text-share-500" : accent === "gold" ? "text-gold-400" : "text-white";
  return (
    <Card className="flex items-center gap-3">
      <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-vault-700/70">
        <Icon className={`h-4.5 w-4.5 ${color}`} />
      </div>
      <div>
        <p className="text-xs text-vault-400">{label}</p>
        <p className={`text-xl font-bold ${color}`}>{value}</p>
      </div>
    </Card>
  );
}


export function UnderTheHoodPage() {
  const [usage, setUsage] = useState<api.UsageSummary | null>(null);


  useEffect(() => {
    api.getUsage().then(setUsage);
  }, []);


  if (!usage) {
    return (
      <Card className="h-32 animate-pulse text-center text-sm text-vault-400">Loading usage data...</Card>
    );
  }


  const chartData = usage.breakdown.map((row) => ({
    name: `${row.model}/${row.task}`,
    cost: Number(row.cost_usd.toFixed(5)),
    calls: row.calls,
  }));


  return (
    <div className="space-y-6">
      <div>
        <h1 className="flex items-center gap-2 text-xl font-bold text-white">
          <BarChart3 className="h-5 w-5 text-gold-400" />
          Under the Hood
        </h1>
        <p className="text-xs text-vault-400">Nano / Super / Ultra routing, tokens, latency and cost.</p>
      </div>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-3">
        <StatCard icon={Zap} label="Total calls" value={String(usage.total_calls)} />
        <StatCard icon={Gauge} label="Actual cost" value={`$${usage.total_cost_usd.toFixed(4)}`} accent="gold" />
        <StatCard icon={TrendingDown} label="Saved vs all-Ultra" value={`$${usage.savings_usd.toFixed(4)}`} accent="share" />
      </div>


      <Card>
        <SectionHeading icon={BarChart3}>Cost by model / task</SectionHeading>
        <ResponsiveContainer width="100%" height={280}>
          <BarChart data={chartData}>
            <defs>
              <linearGradient id="costGradient" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#f6cd73" />
                <stop offset="100%" stopColor="#9d6bff" />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#272150" />
            <XAxis dataKey="name" tick={{ fill: "#b8b1e6", fontSize: 11 }} />
            <YAxis tick={{ fill: "#b8b1e6", fontSize: 11 }} />
            <Tooltip contentStyle={{ background: "#191633", border: "1px solid #3a3270", borderRadius: 8 }} />
            <Legend />
            <Bar dataKey="cost" fill="url(#costGradient)" radius={[6, 6, 0, 0]} name="Cost (USD)" />
          </BarChart>
        </ResponsiveContainer>
      </Card>


      <Card>
        <SectionHeading icon={Clock3}>Call breakdown</SectionHeading>
        <table className="w-full text-left text-xs">
          <thead className="text-vault-400">
            <tr>
              <th className="pb-2">Model</th>
              <th className="pb-2">Task</th>
              <th className="pb-2">Calls</th>
              <th className="pb-2">Tokens (in/out)</th>
              <th className="pb-2">Cost</th>
              <th className="pb-2">Avg latency</th>
            </tr>
          </thead>
          <tbody>
            {usage.breakdown.map((row) => (
              <tr key={`${row.model}-${row.task}`} className="border-t border-vault-800/70">
                <td className="py-1.5 font-medium text-vault-100">{row.model}</td>
                <td className="py-1.5 text-vault-300">{row.task}</td>
                <td className="py-1.5 text-vault-300">{row.calls}</td>
                <td className="py-1.5 text-vault-300">
                  {row.input_tokens} / {row.output_tokens}
                </td>
                <td className="py-1.5 text-gold-400">${row.cost_usd.toFixed(5)}</td>
                <td className="py-1.5 text-vault-300">{row.avg_latency_ms}ms</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Card>
    </div>
  );
}


