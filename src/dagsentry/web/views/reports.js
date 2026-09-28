import { contextLink, appendCell, formatTimestamp, formatDateTime, timezoneLabel, timestampBlock, textElement, definitionItem } from "../core/dom.js";
import { elements } from "../core/elements.js";
import { REPORT_PAGE_SIZE } from "../core/constants.js";
import { state } from "../core/state.js";
import { clearSession } from "../core/session.js";
import { authHeaders, errorDetail } from "../core/api.js";
import { showAuth } from "../core/auth.js";
import { reportQueryFromFilters, updateReportUrl, reportDetailBackHref, reportHref } from "../core/router.js";
import { applyStatusColor } from "../components/badges.js";

function reportStatusBadge(status) {
  const badge = textElement("span", status, "status-badge badge");
  badge.dataset.status = status;
  applyStatusColor(badge, status);
  return badge;
}

function setReportSchedulerStatus(status) {
  elements.reportSchedulerStatus.textContent = status === "ONLINE" ? "정상" : "오프라인";
  elements.reportSchedulerStatus.dataset.status = status;
  applyStatusColor(elements.reportSchedulerStatus, status);
}

function formatScheduleTimestamp(value, timezone) {
  const timestamp = new Date(value);
  if (Number.isNaN(timestamp.getTime())) {
    return "Unavailable";
  }
  return `${formatDateTime(timestamp, timezone)} ${timezoneLabel(timestamp, timezone)} · ${formatDateTime(timestamp, "UTC")} UTC`;
}

function scheduleSummary(schedule) {
  const card = document.createElement("article");
  card.className = "report-schedule-card card card-body";
  const heading = textElement("h3", schedule.display_name);
  const copy = textElement(
    "p",
    `${schedule.enabled
      ? "자동 리포트가 활성화되어 있습니다."
      : "자동 리포트가 비활성화되어 있습니다."} · ${schedule.environment}`,
  );
  const next = textElement(
    "p",
    schedule.next_run_at
      ? `다음 실행: ${formatScheduleTimestamp(schedule.next_run_at, schedule.timezone)}`
      : "다음 실행: 자동 리포트 비활성",
    "muted-copy",
  );
  const applied = textElement(
    "p",
    schedule.applied_revision === schedule.revision ? "스케줄러 적용 완료" : "스케줄러 적용 대기 중",
    "muted-copy",
  );
  const heartbeat = textElement(
    "p",
    schedule.last_heartbeat_at
      ? `마지막 heartbeat: ${formatTimestamp(schedule.last_heartbeat_at).primary}`
      : "마지막 heartbeat: 없음",
    "muted-copy",
  );
  card.append(heading, copy, next, applied, heartbeat);
  return card;
}

function populateReportScheduleForm(schedule) {
  state.currentReportSchedule = schedule;
  elements.reportScheduleId.value = schedule?.id || "";
  elements.reportScheduleRevision.value = schedule?.revision ? String(schedule.revision) : "";
  elements.reportScheduleName.value = schedule?.display_name || "Production Daily Report";
  elements.reportScheduleTitle.value = schedule?.report_title || "DagSentry 일일 장애 리포트";
  elements.reportScheduleEnvironment.value = schedule?.environment || "production";
  elements.reportScheduleNotificationConnection.value = schedule?.notification_connection_id || "";
  elements.reportScheduleTime.value = schedule?.run_at_local_time?.slice(0, 5) || "09:00";
  elements.reportScheduleTimezone.value = schedule?.timezone || "Asia/Seoul";
  elements.reportScheduleAiSummary.checked = schedule?.use_ai_summary ?? false;
  elements.reportScheduleEnabled.checked = schedule?.enabled ?? true;
  elements.reportManualRunForm.hidden = !schedule;
  const localToday = new Intl.DateTimeFormat("en-CA", { timeZone: schedule?.timezone || "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());
  const yesterday = new Date(Date.parse(`${localToday}T00:00:00Z`) - 86_400_000).toISOString().slice(0, 10);
  elements.reportManualDate.max = yesterday;
  if (!elements.reportManualDate.value) {
    elements.reportManualDate.value = yesterday;
  }
  renderReportSchedulePreview();
}

function renderReportSchedulePreview() {
  const time = elements.reportScheduleTime.value || "09:00";
  const timezone = elements.reportScheduleTimezone.value || "Asia/Seoul";
  elements.reportSchedulePreview.replaceChildren(
    textElement("p", elements.reportScheduleEnabled.checked
      ? `매일 ${time} ${timezone}에 실행됩니다.`
      : "자동 리포트가 비활성화되어 있습니다."),
    textElement("p", "전일 UTC 기준 데이터를 집계합니다."),
  );
}

function renderNotificationConnections() {
  const selected = elements.reportScheduleNotificationConnection.value;
  elements.reportScheduleNotificationConnection.replaceChildren(
    new Option("기본 알림 설정", ""),
    ...state.reportNotificationConnections.map((connection) => new Option(
      `${connection.display_name} · ${connection.environment}`,
      connection.id,
    )),
  );
  elements.reportScheduleNotificationConnection.value = selected;
}

function renderReportSchedules(schedules, runs, schedulerStatus) {
  setReportSchedulerStatus(schedulerStatus.status);
  elements.reportScheduleList.replaceChildren(...schedules.map(scheduleSummary));
  if (!schedules.length) {
    elements.reportScheduleList.append(textElement("p", "아직 저장된 리포트 스케줄이 없습니다.", "aside-empty"));
  }
  const isAdmin = state.currentUser?.role === "ADMIN";
  elements.reportScheduleForm.hidden = !isAdmin;
  elements.reportManualRunForm.hidden = true;
  if (isAdmin) {
    const selected = schedules.find((schedule) => schedule.id === state.currentReportSchedule?.id)
      || schedules[0]
      || null;
    populateReportScheduleForm(selected);
  }
  elements.reportScheduleRuns.replaceChildren();
  if (!runs.length) {
    elements.reportScheduleRuns.append(textElement("p", "아직 실행 이력이 없습니다.", "aside-empty"));
    return;
  }
  const latest = runs[0];
  const statusLabels = {
    CLAIMED: "대기 중",
    RUNNING: "실행 중",
    SUCCEEDED: "성공",
    FAILED: "실패",
    SKIPPED: "건너뜀",
  };
  const deliveryLabels = {
    CLAIMED: "전송 대기",
    RUNNING: "전송 중",
    SUCCEEDED: "전송 완료",
    FAILED: "전송 실패",
    SKIPPED: "이미 전송됨",
  };
  const details = document.createElement("dl");
  details.className = "report-execution-grid";
  const appendDetail = (label, value) => {
    details.append(textElement("dt", label), textElement("dd", value));
  };
  appendDetail(
    "실행 시각",
    formatTimestamp(latest.started_at || latest.scheduled_for).primary,
  );
  appendDetail("상태", statusLabels[latest.status] || latest.status);
  appendDetail("리포트 날짜", latest.report_date);
  appendDetail("전송 상태", deliveryLabels[latest.status] || latest.status);
  if (latest.report_id) {
    details.append(textElement("dt", "리포트"), contextLink("상세 보기", reportHref(latest.report_id)));
  }
  elements.reportScheduleRuns.append(details);
}

export async function loadReportSchedules() {
  elements.reportScheduleError.hidden = true;
  try {
    const requests = [
      fetch("/api/v1/daily-report-schedules", { headers: authHeaders() }),
      fetch("/api/v1/daily-report-schedules/runs?limit=5", { headers: authHeaders() }),
      fetch("/api/v1/daily-report-schedules/status", { headers: authHeaders() }),
    ];
    if (state.currentUser?.role === "ADMIN") {
      requests.push(fetch("/api/v1/admin/connections?limit=100", { headers: authHeaders() }));
    }
    const responses = await Promise.all(requests);
    if (responses.some((response) => !response.ok)) {
      const response = responses.find((item) => !item.ok);
      throw new Error(await errorDetail(response));
    }
    const schedules = await responses[0].json();
    const runs = await responses[1].json();
    const schedulerStatus = await responses[2].json();
    if (responses[3]) {
      const connections = await responses[3].json();
      state.reportNotificationConnections = connections.items.filter((connection) => (
        connection.purpose === "NOTIFICATION" && connection.enabled
      ));
      renderNotificationConnections();
    }
    renderReportSchedules(schedules.items, runs.items, schedulerStatus);
    return true;
  } catch (error) {
    elements.reportScheduleError.textContent = error instanceof Error
      ? error.message
      : "Daily Report 스케줄을 불러오지 못했습니다.";
    elements.reportScheduleError.hidden = false;
    return false;
  }
}

async function saveReportSchedule() {
  const body = {
    display_name: elements.reportScheduleName.value.trim(),
    report_title: elements.reportScheduleTitle.value.trim(),
    environment: elements.reportScheduleEnvironment.value.trim(),
    notification_connection_id: elements.reportScheduleNotificationConnection.value || null,
    use_ai_summary: elements.reportScheduleAiSummary.checked,
    enabled: elements.reportScheduleEnabled.checked,
    run_at_local_time: elements.reportScheduleTime.value,
    timezone: elements.reportScheduleTimezone.value,
  };
  const scheduleId = elements.reportScheduleId.value;
  if (scheduleId) {
    body.expected_revision = Number(elements.reportScheduleRevision.value);
  }
  elements.reportScheduleSave.disabled = true;
  elements.reportScheduleError.hidden = true;
  try {
    const response = await fetch(
      scheduleId
        ? `/api/v1/admin/daily-report-schedules/${encodeURIComponent(scheduleId)}`
        : "/api/v1/admin/daily-report-schedules",
      {
        method: scheduleId ? "PUT" : "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify(body),
      },
    );
    if (!response.ok) {
      throw new Error(await errorDetail(response));
    }
    state.currentReportSchedule = await response.json();
    elements.reportScheduleFeedback.textContent = "설정을 저장했습니다. 스케줄러가 다음 동기화 때 적용합니다.";
    elements.reportScheduleFeedback.hidden = false;
    await loadReportSchedules();
  } catch (error) {
    elements.reportScheduleError.textContent = error instanceof Error
      ? error.message
      : "Daily Report 스케줄을 저장하지 못했습니다.";
    elements.reportScheduleError.hidden = false;
  } finally {
    elements.reportScheduleSave.disabled = false;
  }
}

async function requestManualReportRun() {
  if (!state.currentReportSchedule) {
    return;
  }
  elements.reportManualRun.disabled = true;
  elements.reportScheduleError.hidden = true;
  try {
    const response = await fetch(
      `/api/v1/admin/daily-report-schedules/${encodeURIComponent(state.currentReportSchedule.id)}/runs`,
      {
        method: "POST",
        headers: { ...authHeaders(), "Content-Type": "application/json" },
        body: JSON.stringify({ report_date: elements.reportManualDate.value }),
      },
    );
    if (!response.ok) {
      throw new Error(await errorDetail(response));
    }
    elements.reportScheduleFeedback.textContent = "Daily Report 실행을 스케줄러에 요청했습니다.";
    elements.reportScheduleFeedback.hidden = false;
    await loadReportSchedules();
  } catch (error) {
    elements.reportScheduleError.textContent = error instanceof Error
      ? error.message
      : "Daily Report 실행을 요청하지 못했습니다.";
    elements.reportScheduleError.hidden = false;
  } finally {
    elements.reportManualRun.disabled = false;
  }
}

function renderReportRows(items) {
  elements.reportRows.replaceChildren();
  for (const report of items) {
    const row = document.createElement("tr");
    appendCell(row, report.report_date);
    appendCell(row, textElement("span", report.environment, "environment-chip badge bg-secondary-lt"));
    appendCell(row, String(report.statistics.failure_attempts), "numeric");
    appendCell(row, String(report.statistics.affected_dag_runs), "numeric");
    appendCell(row, String(report.statistics.incidents.unresolved), "numeric");
    appendCell(row, reportStatusBadge(report.status));
    appendCell(row, timestampBlock(report.created_at));
    appendCell(row, contextLink("Inspect", reportHref(report.id)));
    elements.reportRows.append(row);
  }
}

function renderReportPage(payload) {
  state.currentReportTotal = payload.total;
  elements.reportError.hidden = true;
  elements.reportEmpty.hidden = payload.items.length !== 0;
  elements.reportResults.hidden = payload.items.length === 0;
  renderReportRows(payload.items);
  const start = payload.items.length ? state.currentReportOffset + 1 : 0;
  const end = state.currentReportOffset + payload.items.length;
  elements.reportRange.textContent = `${start}–${end} of ${payload.total}`;
  const page = Math.floor(state.currentReportOffset / REPORT_PAGE_SIZE) + 1;
  const pages = Math.max(1, Math.ceil(payload.total / REPORT_PAGE_SIZE));
  elements.reportPageLabel.textContent = `Page ${page} of ${pages}`;
  elements.reportPrevious.disabled = state.currentReportOffset === 0;
  elements.reportNext.disabled = state.currentReportOffset + REPORT_PAGE_SIZE >= payload.total;
}

function setReportLoading(isLoading) {
  elements.reportLoading.hidden = !isLoading;
  elements.reportFilterForm.setAttribute("aria-busy", String(isLoading));
  elements.reportPrevious.disabled = isLoading || state.currentReportOffset === 0;
  elements.reportNext.disabled = isLoading
    || state.currentReportOffset + REPORT_PAGE_SIZE >= state.currentReportTotal;
}

async function fetchReportTotal(query = "") {
  const suffix = query ? `&${query}` : "";
  const response = await fetch(`/api/v1/daily-reports?limit=1&offset=0${suffix}`, {
    headers: authHeaders(),
  });
  if (!response.ok) {
    return null;
  }
  return (await response.json()).total;
}

async function loadReportSummary() {
  const summaries = [
    [elements.reportTotal, ""],
    [elements.reportDeliveredTotal, "status=DELIVERED"],
    [elements.reportFailedTotal, "status=FAILED"],
    [elements.reportAiTotal, "ai_summary_used=true"],
  ];
  for (const [element] of summaries) {
    element.textContent = "—";
  }
  const results = await Promise.allSettled(
    summaries.map(([, query]) => fetchReportTotal(query)),
  );
  for (const [index, result] of results.entries()) {
    const total = result.status === "fulfilled" ? result.value : null;
    summaries[index][0].textContent = total === null ? "—" : String(total);
  }
}

export async function loadReports() {
  setReportLoading(true);
  elements.reportError.hidden = true;
  const params = reportQueryFromFilters();
  updateReportUrl(params);
  try {
    const response = await fetch(`/api/v1/daily-reports?${params.toString()}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return false;
      }
      throw new Error(detail);
    }
    renderReportPage(await response.json());
    void loadReportSummary();
    return true;
  } catch (error) {
    elements.reportResults.hidden = true;
    elements.reportEmpty.hidden = true;
    elements.reportError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Daily Reports";
    elements.reportError.hidden = false;
    return false;
  } finally {
    setReportLoading(false);
  }
}

function renderReportList(element, items) {
  element.replaceChildren(...items.map((item) => textElement("li", item)));
}

function renderReportDefinitions(element, fields) {
  element.replaceChildren(...fields.map(([term, value]) => definitionItem(term, String(value))));
}

function durationText(seconds) {
  if (seconds === null || seconds === undefined) {
    return "Unavailable";
  }
  if (seconds < 60) {
    return `${seconds.toFixed(1)} seconds`;
  }
  return `${(seconds / 60).toFixed(1)} minutes`;
}

function renderReportDetail(report) {
  const statistics = report.statistics;
  const ruleReport = report.rule_based_report;
  elements.reportDetailLabels.replaceChildren(
    reportStatusBadge(report.status),
    textElement("span", report.environment, "environment-chip badge bg-secondary-lt"),
    textElement("span", report.ai_summary_used ? "AI-assisted" : "Rule-based", "source-badge badge bg-blue-lt"),
  );
  elements.reportDetailTitle.textContent = ruleReport.title;
  elements.reportDetailSubtitle.textContent = `${report.report_date} · ${statistics.timezone} · schema v${report.report_schema_version}`;
  elements.reportDetailFailures.textContent = String(statistics.failure_attempts);
  elements.reportDetailTasks.textContent = String(statistics.affected_task_instances);
  elements.reportDetailDagRuns.textContent = String(statistics.affected_dag_runs);
  elements.reportDetailUnresolved.textContent = String(statistics.incidents.unresolved);
  elements.reportDetailOverview.textContent = ruleReport.overview;
  renderReportList(elements.reportDetailHighlights, ruleReport.highlights);
  renderReportList(elements.reportDetailPriorities, ruleReport.priorities);
  document.querySelector("#report-top-failures-title").textContent = state.currentLanguage === "ko" ? "상위 실패 Task" : "Top failures";
  const topFailures = (statistics.top_failures || []).map((item) =>
    `${item.dag_id}.${item.task_id} · ${item.failure_count} · ${item.classification || "—"} · ${item.incident_id || "—"} ${item.incident_status || ""} · ${item.last_failed_at} · ${item.root_cause || "—"}`
  );
  if (!topFailures.length) {
    topFailures.push(statistics.failure_attempts ? (state.currentLanguage === "ko" ? "상위 실패 목록 미수집" : "Top failures not collected") : (state.currentLanguage === "ko" ? "실패 없음" : "No failures"));
  }
  renderReportList(document.querySelector("#report-top-failures"), topFailures);

  elements.reportDetailAi.hidden = report.ai_summary === null;
  if (report.ai_summary) {
    elements.reportDetailAiProvider.textContent = report.summary_provider || "AI";
    renderReportList(elements.reportDetailAiChanges, report.ai_summary.key_changes);
    renderReportList(elements.reportDetailAiPriorities, report.ai_summary.priorities);
  }

  renderReportDefinitions(elements.reportDeliveryFields, [
    ["Provider", report.provider],
    ["Status", report.status],
    ["Attempts", report.attempt_count],
    ["HTTP status", report.last_response_status ?? "—"],
    ["Last error", report.last_error_category ?? "—"],
    ["Delivered", report.delivered_at ? formatTimestamp(report.delivered_at).primary : "—"],
  ]);
  renderReportDefinitions(elements.reportIncidentFields, [
    ["New", statistics.incidents.new],
    ["Unresolved", statistics.incidents.unresolved],
    ["Recovered", statistics.incidents.recovered],
    ["New signatures", statistics.error_signatures.new],
    ["Repeated signatures", statistics.error_signatures.repeated],
    ["Mean recovery", durationText(statistics.mean_time.recovery_seconds)],
    ["Mean resolution", durationText(statistics.mean_time.resolution_seconds)],
  ]);
  renderReportDefinitions(
    elements.reportClassificationFields,
    Object.entries(statistics.classification_counts)
      .filter(([, count]) => count > 0)
      .map(([classification, count]) => [classification.replaceAll("_", " "), count]),
  );
  if (!elements.reportClassificationFields.children.length) {
    elements.reportClassificationFields.append(definitionItem("Failures", "No classified failures"));
  }
}

export async function loadReportDetail(reportId) {
  state.currentReportId = reportId;
  elements.reportDetailBack.href = reportDetailBackHref();
  elements.reportDetailLoading.hidden = false;
  elements.reportDetailError.hidden = true;
  elements.reportDetailContent.hidden = true;
  try {
    const response = await fetch(`/api/v1/daily-reports/${encodeURIComponent(reportId)}`, {
      headers: authHeaders(),
    });
    if (!response.ok) {
      const detail = await errorDetail(response);
      if (response.status === 401 || response.status === 503) {
        clearSession();
        showAuth(detail);
        return false;
      }
      throw new Error(detail);
    }
    renderReportDetail(await response.json());
    elements.reportDetailContent.hidden = false;
    return true;
  } catch (error) {
    elements.reportDetailError.textContent = error instanceof Error
      ? error.message
      : "Unable to load Daily Report detail";
    elements.reportDetailError.hidden = false;
    return false;
  } finally {
    elements.reportDetailLoading.hidden = true;
  }
}

export function bindReportsEvents() {
  elements.reportFilterForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    state.currentReportOffset = 0;
    await loadReports();
  });

  elements.reportScheduleForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await saveReportSchedule();
  });

  for (const control of [
    elements.reportScheduleTime,
    elements.reportScheduleTimezone,
    elements.reportScheduleEnabled,
  ]) {
    control.addEventListener("change", renderReportSchedulePreview);
  }

  elements.reportManualRunForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    await requestManualReportRun();
  });

  elements.clearReportFilters.addEventListener("click", async () => {
    elements.reportFilterForm.reset();
    state.currentReportOffset = 0;
    await loadReports();
  });

  elements.reportPrevious.addEventListener("click", async () => {
    state.currentReportOffset = Math.max(0, state.currentReportOffset - REPORT_PAGE_SIZE);
    await loadReports();
  });

  elements.reportNext.addEventListener("click", async () => {
    if (state.currentReportOffset + REPORT_PAGE_SIZE < state.currentReportTotal) {
      state.currentReportOffset += REPORT_PAGE_SIZE;
      await loadReports();
    }
  });
}
