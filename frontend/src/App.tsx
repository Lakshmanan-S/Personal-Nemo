/**
 * App entry point: mounts React into #root (merged from the default
 * Vite main.tsx) and provides the tab-based layout/navigation shell around
 * the screens defined in pages.tsx. A simple tab switch is used instead of a
 * router since this is a single-user dashboard (plan.md section 10).
 */
import { AnimatePresence, motion } from "framer-motion";
import {
  BarChart3,
  Home as HomeIcon,
  MessageCircle,
  ScanFace,
  Shield,
  Sparkles,
  UserCircle2,
} from "lucide-react";
import { StrictMode, useEffect, useState } from "react";
import { createRoot } from "react-dom/client";
import "./index.css";
import { API_BASE, PUBLIC_DEMO } from "./api";
import {
  ChatPage,
  ApprovalInbox,
  ComparePage,
  HomePage,
  KnowMeBetterPage,
  Logo,
  PrivacyPage,
  ProfilePage,
  UnderTheHoodPage,
  TodayWidget,
} from "./pages";


const TABS = [
  { id: "home", label: "Home", icon: HomeIcon, component: HomePage },
  { id: "profile", label: "My Profile", icon: UserCircle2, component: ProfilePage },
  { id: "chat", label: "Chat", icon: MessageCircle, component: ChatPage },
  { id: "compare", label: "See the Difference", icon: ScanFace, component: ComparePage },
  { id: "qa", label: "Know Me Better", icon: Sparkles, component: KnowMeBetterPage },
  { id: "privacy", label: "Privacy & MCP", icon: Shield, component: PrivacyPage },
  { id: "hood", label: "Under the Hood", icon: BarChart3, component: UnderTheHoodPage },
] as const;


function BackendStatus() {
  const [online, setOnline] = useState<boolean | null>(null);


  useEffect(() => {
    fetch(`${API_BASE}/health`)
      .then((r) => setOnline(r.ok))
      .catch(() => setOnline(false));
  }, []);


  return (
    <div className="hidden items-center gap-2 rounded-full border border-vault-700 bg-vault-900/60 px-3 py-1.5 text-xs text-vault-300 sm:flex">
      <span
        className={`h-2 w-2 rounded-full ${
          online === null ? "bg-vault-500" : online ? "bg-share-500 pulse-dot" : "bg-private-500"
        }`}
      />
      {online === null ? "Connecting..." : online ? "Nemotron online" : "Backend offline"}
    </div>
  );
}


function App() {
  const [activeTab, setActiveTab] = useState<(typeof TABS)[number]["id"]>("home");
  const active = TABS.find((t) => t.id === activeTab) ?? TABS[0];
  const ActiveComponent = active.component;


  return (
    <div className="relative">
      <div className="aurora-bg" aria-hidden="true" />
      <div className="mx-auto flex min-h-screen max-w-6xl flex-col gap-6 px-4 py-6 pb-24">
        <header className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-3">
            <Logo />
            <div className="leading-tight">
              <p className="text-lg font-bold tracking-tight text-white">
                Personal <span className="gradient-text">Nemo</span>
              </p>
              <p className="text-[11px] text-vault-400">The AI that actually knows you</p>
            </div>
          </div>
          <div className="flex items-center gap-3">
            <BackendStatus />
          </div>
        </header>
        {PUBLIC_DEMO && (
          <aside className="rounded-xl border border-gold-500/30 bg-vault-900 p-3 text-xs text-vault-200">
            Public sample demo. Chat and comparisons use a fictional profile, not your personal data.
            Do not enter private information. AI requests use API credits; chat text is not saved by this demo.
          </aside>
        )}


        <nav className="glass flex flex-wrap gap-1 rounded-2xl p-1.5">
          {TABS.map((tab) => {
            const Icon = tab.icon;
            const isActive = tab.id === activeTab;
            return (
              <button
                key={tab.id}
                onClick={() => setActiveTab(tab.id)}
                className={`relative flex items-center gap-1.5 rounded-xl px-3 py-2 text-xs font-medium transition-colors ${
                  isActive ? "text-vault-950" : "text-vault-300 hover:text-white"
                }`}
              >
                {isActive && (
                  <motion.span
                    layoutId="active-tab-pill"
                    className="absolute inset-0 rounded-xl bg-gradient-to-r from-gold-500 to-gold-400 shadow-lg shadow-gold-500/20"
                    transition={{ type: "spring", stiffness: 500, damping: 35 }}
                  />
                )}
                <Icon className="relative z-10 h-3.5 w-3.5" />
                <span className="relative z-10 hidden sm:inline">{tab.label}</span>
              </button>
            );
          })}
        </nav>


        <ApprovalInbox expanded={activeTab === "privacy"} />
        <main className="flex-1">
          <AnimatePresence mode="wait">
            <motion.div
              key={activeTab}
              initial={{ opacity: 0, y: 10 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0, y: -10 }}
              transition={{ duration: 0.25, ease: "easeOut" }}
            >
              {PUBLIC_DEMO && ["qa", "privacy"].includes(activeTab) ? (
                <div className="space-y-3">
                  <p className="text-xs text-gold-400">This shared sample view is read-only. Editing, personal onboarding and MCP administration remain available in your private installation.</p>
                  <fieldset disabled className="min-w-0"><ActiveComponent /></fieldset>
                </div>
              ) : <ActiveComponent />}
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
      <TodayWidget />
    </div>
  );
}


createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <App />
  </StrictMode>,
);




