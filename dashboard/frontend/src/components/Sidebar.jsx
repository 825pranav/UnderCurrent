import React from "react";

const NAV_ITEMS = [
  { id: "overview",   label: "Overview",        icon: "dashboard" },
  { id: "stateless",  label: "Stateless Track",  icon: "waves" },
  { id: "stateful",   label: "Stateful Track",   icon: "memory" },
  { id: "audit",      label: "Audit Log",        icon: "receipt_long" },
  { id: "settings",   label: "Settings",         icon: "settings" },
];

export default function Sidebar({ activeTab, setActiveTab, darkMode, setDarkMode }) {
  return (
    <aside className="fixed top-0 left-0 h-screen w-64 bg-surface-container-low border-r border-outline-variant flex flex-col z-40 no-print">
      {/* Logo */}
      <div className="flex items-center gap-3 px-5 py-5 border-b border-outline-variant">
        <div className="w-8 h-8 rounded-lg bg-primary-container flex items-center justify-center flex-shrink-0">
          <span className="material-symbols-outlined text-on-primary text-base">waves</span>
        </div>
        <span className="font-headline font-semibold text-primary-container tracking-tight text-lg">
          UnderCurrent
        </span>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-3 py-4 space-y-0.5 overflow-y-auto">
        <p className="text-xs font-label font-medium text-outline uppercase tracking-widest px-3 mb-3">
          Navigation
        </p>
        {NAV_ITEMS.map((item) => {
          const active = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={`w-full flex items-center gap-3 px-3 py-2.5 rounded text-sm font-body transition-all duration-150
                ${active
                  ? "bg-surface-container-high text-primary-container border-l-2 border-primary-container pl-[10px]"
                  : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface border-l-2 border-transparent"
                }`}
            >
              <span className={`material-symbols-outlined text-xl ${active ? "text-primary-container" : "text-outline"}`}>
                {item.icon}
              </span>
              <span className="font-medium">{item.label}</span>
            </button>
          );
        })}
      </nav>

      {/* Bottom section */}
      <div className="px-3 pb-4 border-t border-outline-variant pt-4 space-y-3">
        {/* Dark/Light toggle */}
        <div className="flex items-center justify-between px-3 py-2 rounded bg-surface-container">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-outline text-xl">
              {darkMode ? "dark_mode" : "light_mode"}
            </span>
            <span className="text-sm text-on-surface-variant font-body">
              {darkMode ? "Dark" : "Light"} Mode
            </span>
          </div>
          <button
            onClick={() => setDarkMode(!darkMode)}
            className={`relative w-10 h-5 rounded-full transition-colors duration-200 ${
              darkMode ? "bg-primary-container" : "bg-outline-variant"
            }`}
          >
            <span
              className={`absolute top-0.5 w-4 h-4 rounded-full bg-white transition-transform duration-200 ${
                darkMode ? "translate-x-5" : "translate-x-0.5"
              }`}
            />
          </button>
        </div>

        {/* Admin session */}
        <div className="flex items-center gap-3 px-3 py-2.5 rounded bg-surface-container">
          <div className="w-8 h-8 rounded-full bg-secondary-container flex items-center justify-center flex-shrink-0">
            <span className="material-symbols-outlined text-secondary text-base">person</span>
          </div>
          <div className="min-w-0">
            <p className="text-xs font-semibold text-on-surface truncate">Admin</p>
            <p className="text-xs text-outline truncate">admin@undercurrent</p>
          </div>
          <span className="material-symbols-outlined text-outline text-base ml-auto flex-shrink-0">logout</span>
        </div>
      </div>
    </aside>
  );
}
