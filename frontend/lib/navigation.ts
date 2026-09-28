/**
 * The single source of truth for what can be navigated to.
 *
 * WHY THIS IS A DATA MODULE NOW. It lived in TopNav.tsx while the five sections
 * were a row of tabs along the top of the window. The shell follows the
 * Chatbot UI layout now: the sections live in the sidebar, beside the
 * conversation list, and the top bar only carries the page title, the AI
 * selector and the window controls. Two components render from this model, so
 * it belongs to neither of them.
 *
 * NOTHING WAS DROPPED. Every view is still reachable; the multi-view sections
 * show their views as sub-tabs above the page. `ViewId` remains the union the
 * shell switches on, so a view that exists here and nowhere else would be a dead
 * control -- which is what tests/test_ui_v1_contract.py checks.
 */

export type ViewId =
  | "chat" | "tasks" | "activity"
  | "permissions" | "agents" | "memory" | "knowledge" | "graph" | "integrations"
  | "capabilities" | "status" | "settings";

export type SectionId = "chat" | "tools" | "pc" | "memory" | "settings";

export type NavCounts = Partial<Record<ViewId, number>>;

/** One navigable page. `id` is a ViewId the shell renders — never a group. */
export type ViewEntry = { id: ViewId; label: string; hint: string };

/** A top-level section. Its `section` key is deliberately NOT called `id`:
 *  a section is not a destination, and conflating the two is how a nav entry
 *  that leads nowhere gets introduced. */
export type SectionEntry = { section: SectionId; label: string; views: ViewEntry[] };

export const SECTIONS: SectionEntry[] = [
  {
    section: "chat",
    label: "Chat",
    views: [{ id: "chat", label: "Conversa", hint: "Fala com o NANO" }],
  },
  {
    // FERRAMENTAS answers "what can this thing do for me?". It is a catalogue,
    // not a control surface: the providers that used to lead this section are
    // configuration and now live in Definições → IA, where they can be changed.
    section: "tools",
    label: "Ferramentas",
    views: [
      { id: "capabilities", label: "Capacidades", hint: "Tudo o que o NANO sabe fazer" },
      { id: "integrations", label: "Componentes", hint: "Módulos instalados que fornecem capacidades" },
      { id: "agents", label: "Agentes", hint: "Agentes registados e o que sabem fazer" },
    ],
  },
  {
    // PC is THIS COMPUTER — its state, what Nano may do to it, and what Nano
    // has actually done. Not the generic capability list, which is Ferramentas.
    section: "pc",
    label: "PC",
    views: [
      { id: "status", label: "Estado", hint: "Recursos e saúde desta máquina" },
      { id: "permissions", label: "Permissões", hint: "O que o NANO pode fazer neste computador" },
      { id: "activity", label: "Atividade", hint: "O que o NANO tem feito aqui" },
      { id: "tasks", label: "Tarefas", hint: "Trabalho em segundo plano" },
    ],
  },
  {
    // MEMÓRIA is what Nano remembers and what it knows. Three views, because
    // they answer three different questions: what do you remember about me,
    // what do you know about my world, and how is it connected?
    //
    // Privacidade is deliberately NOT a fourth view here. The switches that
    // govern memory are settings, they live with every other setting in
    // Definições → Memória and Definições → Privacidade, and duplicating them
    // would create two places to change one thing.
    section: "memory",
    label: "Memória",
    views: [
      { id: "memory", label: "Memórias", hint: "O que o NANO sabe sobre ti" },
      { id: "knowledge", label: "Second Brain", hint: "Pessoas, projetos e coisas que o NANO conhece" },
      { id: "graph", label: "Grafo", hint: "Como tudo se liga" },
    ],
  },
  {
    section: "settings",
    label: "Definições",
    views: [{ id: "settings", label: "Definições", hint: "Configurar o NANO" }],
  },
];

/** Which section owns a view. Falls back to chat so a bad id cannot blank the bar. */
export function sectionOf(view: ViewId): SectionId {
  return SECTIONS.find((s) => s.views.some((v) => v.id === view))?.section ?? "chat";
}

export function sectionEntry(section: SectionId): SectionEntry {
  return SECTIONS.find((s) => s.section === section) ?? SECTIONS[0];
}

export function viewEntry(view: ViewId): ViewEntry {
  for (const section of SECTIONS) {
    const found = section.views.find((v) => v.id === view);
    if (found) return found;
  }
  return SECTIONS[0].views[0];
}

/** A section's badge is the sum of what its pages need the user for. Only
 *  work that is genuinely waiting counts — see get_task_counts, where the
 *  badge deliberately excludes finished tasks. */
export function sectionBadge(entry: SectionEntry, counts: NavCounts): number {
  return entry.views.reduce((total, v) => total + (counts[v.id] ?? 0), 0);
}
