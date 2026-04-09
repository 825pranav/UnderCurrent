import React from "react";

const NAV_ITEMS = [
  { id: "overview",  label: "Overview",        icon: "dashboard" },
  { id: "stateless", label: "Stateless Track",  icon: "dataset" },
  { id: "stateful",  label: "Stateful Track",   icon: "database" },
  { id: "shadow",    label: "Shadow Track",     icon: "visibility_off" },
  { id: "audit",     label: "Audit Log",        icon: "history" },
];

export default function Sidebar({ activeTab, setActiveTab, darkMode, setDarkMode }) {
  return (
    <aside className="fixed left-0 top-0 h-screen w-64 bg-surface-container-lowest flex flex-col py-8 z-50 no-print"
           style={{ boxShadow: "24px 0 48px rgba(0,0,0,0.3)" }}>

      {/* Logo */}
      <div className="px-8 mb-10">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl flex items-center justify-center flex-shrink-0"
               style={{ background: "linear-gradient(135deg, #00daf3, #009fb2)" }}>
            <span className="material-symbols-outlined text-on-primary text-xl"
                  style={{ fontVariationSettings: "'FILL' 1" }}>
              waves
            </span>
          </div>
          <div>
            <h1 className="text-xl font-extrabold tracking-tight text-primary font-headline leading-none">
              UnderCurrent
            </h1>
            <p className="text-[10px] uppercase tracking-[0.2em] text-on-surface-variant opacity-60 font-label mt-0.5">
              Control Plane
            </p>
          </div>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex-1 px-4 space-y-1 overflow-y-auto">
        <p className="text-[10px] uppercase tracking-[0.2em] text-on-surface-variant opacity-50 font-label px-4 mb-3">
          Navigation
        </p>
        {NAV_ITEMS.map((item) => {
          const active = activeTab === item.id;
          return (
            <button
              key={item.id}
              onClick={() => setActiveTab(item.id)}
              className={`w-full flex items-center gap-3 px-4 py-3 text-sm font-body transition-all duration-150 group
                ${active
                  ? "text-primary bg-surface-container rounded-r-lg border-l-4 border-primary font-bold"
                  : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface rounded-lg border-l-4 border-transparent"
                }`}
            >
              <span
                className="material-symbols-outlined text-xl transition-colors"
                style={active ? { fontVariationSettings: "'FILL' 1" } : {}}
              >
                {item.icon}
              </span>
              <span>{item.label}</span>
            </button>
          );
        })}
      </nav>

      {/* Bottom */}
      <div className="px-4 mt-auto space-y-2">
        <button
          onClick={() => setActiveTab("settings")}
          className={`w-full flex items-center gap-3 px-4 py-3 text-sm font-body rounded-lg transition-all duration-150 border-l-4
            ${activeTab === "settings"
              ? "text-primary bg-surface-container border-primary font-bold"
              : "text-on-surface-variant hover:bg-surface-container hover:text-on-surface border-transparent"
            }`}
        >
          <span className="material-symbols-outlined text-xl">settings</span>
          <span>Settings</span>
        </button>

        {/* Dark/light toggle */}
        <div className="flex items-center justify-between px-4 py-3 rounded-xl bg-surface-container-low">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined text-on-surface-variant text-xl">
              {darkMode ? "dark_mode" : "light_mode"}
            </span>
            <span className="text-xs text-on-surface-variant font-body">
              {darkMode ? "Dark" : "Light"}
            </span>
          </div>
          <button
            onClick={() => setDarkMode(!darkMode)}
            className={`relative w-10 h-5 rounded-full transition-colors duration-200 flex-shrink-0 ${
              darkMode ? "bg-primary" : "bg-outline"
            }`}
          >
            <span
              className={`absolute top-0.5 left-0 w-4 h-4 rounded-full bg-white transition-transform duration-200 shadow ${
                darkMode ? "translate-x-5" : "translate-x-0.5"
              }`}
            />
          </button>
        </div>

        {/* User */}
        <div className="flex items-center gap-3 px-4 py-3 rounded-xl bg-surface-container-low">
          <div className="w-9 h-9 rounded-full bg-surface-container-highest flex items-center justify-center flex-shrink-0">
            <span className="material-symbols-outlined text-primary text-base"
                  style={{ fontVariationSettings: "'FILL' 1" }}>
              person
            </span>
          </div>
          <div className="min-w-0">
            <p className="text-xs font-bold text-on-surface truncate font-headline">Admin</p>
            <p className="text-[10px] text-on-surface-variant truncate">Control Plane</p>
          </div>
          <span className="material-symbols-outlined text-outline text-base ml-auto flex-shrink-0">
            logout
          </span>
        </div>
      </div>
    </aside>
  );
}
