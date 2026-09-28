import { elements } from "./elements.js";
import { translatedText } from "./i18n.js";

export const sidebarMedia = window.matchMedia("(max-width: 991.98px)");
let desktopSidebarCollapsed = false;
let mobileSidebarCollapsed = true;
export let sidebarCollapsed = sidebarMedia.matches;

export function applySidebarState() {
  sidebarCollapsed = sidebarMedia.matches ? mobileSidebarCollapsed : desktopSidebarCollapsed;
  const navigationVisible = !elements.sidebarToggle.hidden;
  const toggleLabel = sidebarCollapsed ? "Expand navigation" : "Collapse navigation";
  document.body.classList.toggle("sidebar-hidden", !navigationVisible);
  document.body.classList.toggle("sidebar-collapsed", navigationVisible && sidebarCollapsed);
  if (navigationVisible && sidebarCollapsed && !sidebarMedia.matches) {
    document.documentElement.setAttribute("data-bs-sidebar", "folded-hover");
  } else {
    document.documentElement.removeAttribute("data-bs-sidebar");
  }
  elements.siteSidebar.hidden = !navigationVisible;
  elements.sidebarToggle.setAttribute("aria-expanded", String(!sidebarCollapsed));
  elements.sidebarToggle.setAttribute("aria-label", translatedText(toggleLabel));
  elements.sidebarToggle.title = translatedText(toggleLabel);
}

export function setSidebarCollapsed(collapsed) {
  if (sidebarMedia.matches) mobileSidebarCollapsed = collapsed;
  else desktopSidebarCollapsed = collapsed;
  applySidebarState();
}

export function setSidebarVisibility(visible) {
  elements.sidebarToggle.hidden = !visible;
  applySidebarState();
}
