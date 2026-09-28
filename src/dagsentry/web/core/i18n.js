import { KOREAN_TRANSLATIONS } from "../i18n/ko.js";
import { state } from "./state.js";

const originalText = new WeakMap();
const originalAttributes = new WeakMap();

function translatePattern(value) {
  let match = value.match(/^(\d+)–(\d+) of (\d+)$/);
  if (match) {
    return `전체 ${match[3]}개 중 ${match[1]}–${match[2]}`;
  }
  match = value.match(/^Page (\d+) of (\d+)$/);
  if (match) {
    return `${match[2]}페이지 중 ${match[1]}페이지`;
  }
  match = value.match(/^(viewer|operator) session$/);
  if (match) {
    return match[1] === "operator" ? "Operator 세션" : "Viewer 세션";
  }
  match = value.match(/^Try (\d+) · (.+)$/);
  if (match) {
    return `${match[1]}차 시도 · ${KOREAN_TRANSLATIONS[match[2]] || match[2]}`;
  }
  match = value.match(/^Occurrence (\d+)$/);
  if (match) {
    return `발생 ${match[1]}회`;
  }
  match = value.match(/^(\d+)% confidence$/);
  if (match) {
    return `신뢰도 ${match[1]}%`;
  }
  match = value.match(/^from (.+)$/);
  if (match) {
    return `원본 ${match[1]}`;
  }
  match = value.match(/^effective (.+)$/);
  if (match) {
    return `대표 진단 ${match[1]}`;
  }
  match = value.match(/^run (.+)$/);
  if (match) {
    return `실행 ${match[1]}`;
  }
  match = value.match(/^map (.+)$/);
  if (match) {
    return `맵 ${match[1]}`;
  }
  match = value.match(/^Request failed with status (\d+)$/);
  if (match) {
    return `요청 실패 (상태 ${match[1]})`;
  }
  match = value.match(/^Incident changed to (.+)\.$/);
  if (match) {
    return `인시던트 상태가 ${KOREAN_TRANSLATIONS[match[1]] || match[1]}(으)로 변경되었습니다.`;
  }
  match = value.match(/^Inspect Incident (.+)$/);
  if (match) {
    return `인시던트 ${match[1]} 확인`;
  }
  match = value.match(/^Inspect Diagnosis (.+)$/);
  if (match) {
    return `진단 ${match[1]} 확인`;
  }
  match = value.match(/^Explore (.+)$/);
  if (match) {
    return `${match[1]} 탐색`;
  }
  match = value.match(/^Inspect (.+) Incident$/);
  if (match) {
    return `${match[1]} 인시던트 확인`;
  }
  return value;
}

export function translatedText(value) {
  if (state.currentLanguage === "en") {
    return value;
  }
  return KOREAN_TRANSLATIONS[value] || translatePattern(value);
}

function translatedValue(value) {
  const trimmed = value.trim();
  if (!trimmed) {
    return value;
  }
  return value.replace(trimmed, translatedText(trimmed));
}

function isLanguageInvariant(node) {
  const element = node instanceof Element ? node : node.parentElement;
  return element?.closest("[data-i18n-fixed]") !== null;
}

function translateElementAttributes(element) {
  let attributes = originalAttributes.get(element);
  if (!attributes) {
    attributes = new Map();
    originalAttributes.set(element, attributes);
  }
  for (const name of ["aria-label", "placeholder", "title"]) {
    if (element.hasAttribute(name) && !attributes.has(name)) {
      attributes.set(name, element.getAttribute(name));
    }
    if (attributes.has(name)) {
      element.setAttribute(name, translatedText(attributes.get(name)));
    }
  }
}

function translateSubtree(root) {
  if (isLanguageInvariant(root)) {
    return;
  }
  if (root.nodeType === Node.TEXT_NODE) {
    if (!originalText.has(root)) {
      originalText.set(root, root.nodeValue);
    }
    root.nodeValue = translatedValue(originalText.get(root));
    return;
  }
  if (root instanceof Element) {
    translateElementAttributes(root);
  }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
  let node = walker.nextNode();
  while (node) {
    if (isLanguageInvariant(node)) {
      node = walker.nextNode();
      continue;
    }
    if (node.nodeType === Node.TEXT_NODE) {
      if (!originalText.has(node)) {
        originalText.set(node, node.nodeValue);
      }
      node.nodeValue = translatedValue(originalText.get(node));
    } else {
      translateElementAttributes(node);
    }
    node = walker.nextNode();
  }
}

export function applyLanguage() {
  document.documentElement.lang = state.currentLanguage;
  document.querySelector("#language-select").value = state.currentLanguage;
  document.querySelector("#timezone-select").value = state.currentTimezone;
  translateSubtree(document.body);
}

export function bindI18NEvents() {
  const languageObserver = new MutationObserver((mutations) => {
    for (const mutation of mutations) {
      for (const node of mutation.addedNodes) {
        translateSubtree(node);
      }
    }
  });

  languageObserver.observe(document.body, { childList: true, subtree: true });
}
