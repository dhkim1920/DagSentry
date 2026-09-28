import { LANGUAGE_STORAGE_KEY, TIMEZONE_STORAGE_KEY } from "./constants.js";

export const state = {};

state.currentLanguage = localStorage.getItem(LANGUAGE_STORAGE_KEY)
  || (navigator.language.toLowerCase().startsWith("ko") ? "ko" : "en");
if (!new Set(["ko", "en"]).has(state.currentLanguage)) {
  state.currentLanguage = "en";
}
state.currentTimezone = localStorage.getItem(TIMEZONE_STORAGE_KEY) || "Asia/Seoul";
if (!new Set(["Asia/Seoul", "UTC", "browser"]).has(state.currentTimezone)) {
  state.currentTimezone = "Asia/Seoul";
}
state.currentOffset = 0;
state.currentTotal = 0;
state.currentIncidentId = null;
state.currentIncidentStatus = null;
state.pendingOperatorAction = null;
state.currentSignatureOffset = 0;
state.currentSignatureTotal = 0;
state.currentSignatureId = null;
state.currentSignature = null;
state.currentSignatureTrendDays = 7;
state.currentOccurrenceOffset = 0;
state.currentOccurrenceTotal = 0;
state.currentDiagnosisOffset = 0;
state.currentDiagnosisTotal = 0;
state.currentDiagnosisId = null;
state.currentReportOffset = 0;
state.currentReportTotal = 0;
state.currentReportId = null;
state.currentReportSchedule = null;
state.reportNotificationConnections = [];
state.currentUser = null;
state.passwordResetUser = null;
state.editingConnection = null;
