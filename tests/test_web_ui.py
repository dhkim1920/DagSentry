from __future__ import annotations

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from tests.test_incident_api import request


def test_root_redirects_to_packaged_web_ui(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    response = request(create_app(settings, session_factory), "GET", "/")

    assert response.status_code == 307
    assert response.headers["location"] == "/ui/"


def test_web_ui_serves_dashboard_with_browser_security_headers(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    response = request(create_app(settings, session_factory), "GET", "/ui/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["content-security-policy"] == (
        "default-src 'self'; script-src 'self'; style-src 'self'; "
        "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
        "base-uri 'none'; frame-ancestors 'none'; form-action 'self'"
    )
    assert 'id="incident-dashboard"' in response.text
    assert 'id="incident-detail"' in response.text
    assert response.text.count('class="auth-title-line"') == 2
    assert 'id="access-email"' in response.text
    assert 'id="access-password"' in response.text
    assert "test-operator-token" not in response.text
    assert "test-viewer-token" not in response.text

    script = request(create_app(settings, session_factory), "GET", "/ui/app.js")
    assert script.headers["cache-control"] == "no-store"
    assert 'src="/ui/app.js?v=20260927-hierarchy1"' in response.text


def test_auth_title_remains_exactly_two_lines_in_both_languages(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = request(app, "GET", "/ui/app.css")
    script = request(app, "GET", "/ui/app.js")

    assert '<span class="auth-title-line">See the failure.</span>' in shell.text
    assert '<span class="auth-title-line">Trace the evidence.</span>' in shell.text
    assert "Operational visibility for Airflow" not in shell.text + script.text
    assert "Sign in with your DagSentry account" not in shell.text + script.text
    assert "Sanitized evidence only" not in shell.text + script.text
    assert "Server-enforced roles" not in shell.text + script.text
    assert "Selectable timezone display" not in shell.text + script.text
    assert "Secure session" not in shell.text + script.text
    assert "protected HttpOnly cookie" not in shell.text + script.text
    assert '"See the failure.": "실패를 확인하고."' in script.text
    assert '"Trace the evidence.": "근거를 추적하세요."' in script.text
    assert ".auth-title-line" in stylesheet.text
    assert "display: block" in stylesheet.text
    assert "white-space: nowrap" in stylesheet.text


def test_temporary_password_form_omits_redundant_introductory_copy(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = request(app, "GET", "/ui/app.js")

    assert "Required account step" not in shell.text + script.text
    assert (
        "Choose a personal password before opening the dashboard." not in shell.text + script.text
    )
    assert "Change temporary password" in shell.text
    assert 'id="current-password"' in shell.text


def test_login_shows_centered_loading_overlay_and_locks_auth_controls(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = request(app, "GET", "/ui/app.css")
    script = request(app, "GET", "/ui/app.js")

    assert 'id="auth-loading"' in shell.text
    assert 'class="auth-loading-overlay"' in shell.text
    assert 'role="status"' in shell.text
    assert 'aria-live="polite"' in shell.text
    assert 'id="auth-submit"' in shell.text
    assert ".auth-loading-overlay" in stylesheet.text
    assert "position: fixed" in stylesheet.text
    assert "place-items: center" in stylesheet.text
    assert "function setAuthLoading(isLoading)" in script.text
    assert "elements.authLoading.hidden = !isLoading" in script.text
    assert "elements.authSubmit.disabled = isLoading" in script.text
    assert "setAuthLoading(true)" in script.text
    assert "setAuthLoading(false)" in script.text


def test_session_bootstrap_prevents_auth_flash_and_primary_navigation_reload(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = request(app, "GET", "/ui/app.js")

    auth_tag = shell.text[
        shell.text.index('<section id="auth-panel"') : shell.text.index(
            ">", shell.text.index('<section id="auth-panel"')
        )
    ]
    session_loading_tag = shell.text[
        shell.text.index('id="session-loading"') : shell.text.index(
            ">", shell.text.index('id="session-loading"')
        )
    ]
    assert "hidden" in auth_tag
    assert "hidden" not in session_loading_tag
    assert "Loading DagSentry…" in shell.text
    assert 'primaryNavigation: document.querySelector("#primary-navigation")' in script.text
    assert 'elements.primaryNavigation.addEventListener("click"' in script.text
    assert "event.preventDefault()" in script.text
    assert 'history.pushState({}, "", link.href)' in script.text
    assert "await loadCurrentView()" in script.text
    assert "elements.sessionLoading.hidden = true" in script.text


def test_web_ui_assets_are_packaged_and_use_authenticated_incident_api(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)

    stylesheet = request(app, "GET", "/ui/app.css")
    script = request(app, "GET", "/ui/app.js")

    assert stylesheet.status_code == 200
    assert stylesheet.headers["content-type"].startswith("text/css")
    assert script.status_code == 200
    assert "sessionStorage" not in script.text
    assert "fetch(`/api/v1/incidents?" in script.text
    assert "fetch(`/api/v1/incidents/${encodeURIComponent(incidentId)}`" in script.text
    assert 'fetch("/api/v1/auth/login"' in script.text
    assert 'fetch("/api/v1/auth/logout"' in script.text
    assert 'fetch("/api/v1/auth/me"' in script.text
    assert "X-CSRF-Token" in script.text
    assert "X-DagSentry-Viewer-Token" not in script.text
    assert "X-DagSentry-Operator-Token" not in script.text


def test_web_ui_supports_persisted_korean_and_english_language_switching(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = request(app, "GET", "/ui/app.css")
    script = request(app, "GET", "/ui/app.js")

    assert 'id="language-select"' in shell.text
    assert '<option value="ko">한국어</option>' in shell.text
    assert '<option value="en">English</option>' in shell.text
    assert "navigator.language" in script.text
    assert "localStorage.getItem(LANGUAGE_STORAGE_KEY)" in script.text
    assert "localStorage.setItem(LANGUAGE_STORAGE_KEY" in script.text
    assert "document.documentElement.lang = currentLanguage" in script.text
    assert '"Matching incidents": "일치하는 인시던트"' in script.text
    assert "#language-select" in stylesheet.text


def test_web_ui_supports_persisted_timezone_with_selected_timezone_only(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = request(app, "GET", "/ui/app.css")
    script = request(app, "GET", "/ui/app.js")

    assert 'id="timezone-select"' in shell.text
    assert '<option value="Asia/Seoul">Asia/Seoul</option>' in shell.text
    assert '<option value="UTC">UTC</option>' in shell.text
    assert '<option value="browser">Browser local</option>' in shell.text
    assert 'const TIMEZONE_STORAGE_KEY = "dagsentry.timezone"' in script.text
    assert "localStorage.getItem(TIMEZONE_STORAGE_KEY)" in script.text
    assert "localStorage.setItem(TIMEZONE_STORAGE_KEY, currentTimezone)" in script.text
    assert 'currentTimezone = "Asia/Seoul"' in script.text
    assert "function formatScheduleTimestamp(value, timezone)" in script.text
    assert "return { primary };" in script.text
    assert "includeUtc" not in script.text
    assert ").detail" not in script.text
    assert "timestampBlock(occurrence.observed_at, true)" not in script.text
    assert "timestampBlock(occurrence.observed_at)" in script.text
    assert ".timestamp-detail" not in stylesheet.text
    assert "All timestamps are UTC" not in shell.text + script.text
    assert "All dates are UTC" not in shell.text + script.text
    assert "#timezone-select" in stylesheet.text


def test_primary_navigation_and_page_titles_follow_selected_language_without_global_footer(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = request(app, "GET", "/ui/app.js")

    assert 'id="incidents-nav" data-i18n-fixed' not in shell.text
    assert 'id="signatures-nav" data-i18n-fixed' not in shell.text
    assert 'id="diagnoses-nav" data-i18n-fixed' not in shell.text
    assert 'id="reports-nav" data-i18n-fixed' not in shell.text
    assert 'id="dashboard-title" class="page-title">Incident response</h1>' in shell.text
    assert 'id="signature-dashboard-title" class="page-title">Error patterns</h1>' in shell.text
    assert 'id="diagnosis-dashboard-title" class="page-title">Diagnosis History</h1>' in shell.text
    assert 'id="report-dashboard-title" class="page-title">Daily Reports</h1>' in shell.text
    assert '"Incident response": "장애 대응"' in script.text
    assert '"Error patterns": "오류 패턴"' in script.text
    assert "<footer>" not in shell.text
    assert 'closest("[data-i18n-fixed]")' in script.text


def test_daily_report_ui_exposes_list_detail_filters_and_authenticated_api(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = request(app, "GET", "/ui/app.js")
    stylesheet = request(app, "GET", "/ui/app.css")

    for element_id in [
        "report-dashboard",
        "report-filter-form",
        "report-status-filter",
        "report-date-from-filter",
        "report-date-to-filter",
        "report-rows",
        "report-detail",
        "report-detail-overview",
        "report-detail-ai",
        "report-delivery-fields",
        "report-classification-fields",
        "report-schedule-panel",
        "report-schedule-form",
        "report-manual-run-form",
        "report-scheduler-status",
        "report-schedule-name",
        "report-schedule-title-input",
        "report-schedule-notification-connection",
        "report-schedule-ai-summary",
        "report-schedule-preview",
        "report-schedule-runs",
    ]:
        assert f'id="{element_id}"' in shell.text
    assert "fetch(`/api/v1/daily-reports?" in script.text
    assert "fetch(`/api/v1/daily-reports/${encodeURIComponent(reportId)}`" in script.text
    assert 'params.set("view", "reports")' in script.text
    assert 'elements.reportFilterForm.addEventListener("submit"' in script.text
    assert 'fetch("/api/v1/daily-report-schedules"' in script.text
    assert 'fetch("/api/v1/daily-report-schedules/status"' in script.text
    assert 'fetch("/api/v1/admin/connections?limit=100"' in script.text
    assert '"/api/v1/admin/daily-report-schedules"' in script.text
    assert "리포트 스케줄 설정" in shell.text
    assert "자동 리포트" in shell.text
    assert 'class="visually-hidden">AI 요약 사용</span>' in shell.text
    assert "max-width: 960px" in stylesheet.text
    assert ".setting-toggle-row > div" in stylesheet.text
    assert "min-width: 44px" in stylesheet.text
    assert "전일 UTC 기준 데이터를 집계합니다." in script.text
    assert "아직 저장된 리포트 스케줄이 없습니다." in script.text
    assert 'details.className = "report-execution-grid"' in script.text
    assert ".report-execution-grid .inspect-link" in stylesheet.text
    assert ".report-manual-run-form .field-group" in stylesheet.text
    assert "flex: 0 0 168px" in stylesheet.text
    assert "data-schedule-id" not in script.text
    assert (
        shell.text.index('id="report-schedule-save"')
        < shell.text.index('id="report-schedule-feedback"')
        < shell.text.index('id="report-manual-run-form"')
    )
    report_schedule_card = stylesheet.text[
        stylesheet.text.index(".report-schedule-card {") : stylesheet.text.index(
            ".report-schedule-card h3,"
        )
    ]
    assert "grid-template-columns: minmax(0, 1fr)" in report_schedule_card
    assert "auto" not in report_schedule_card


def test_page_descriptions_use_matching_english_and_korean_operational_copy(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = request(app, "GET", "/ui/app.js")

    descriptions = {
        "Find an incident, review its cause, and decide the next action.": (
            "장애를 찾고 원인을 확인한 뒤 다음 조치를 결정합니다."
        ),
        "Find recurring failures with the same error pattern.": (
            "같은 오류 패턴으로 반복되는 실패를 확인합니다."
        ),
        "Search AI, Rule, and operator-authored diagnosis records with their Incident and Error Signature context.": (
            "AI, 규칙 및 운영자 작성 진단 기록을 인시던트와 오류 시그니처 맥락과 함께 검색합니다."
        ),
        "Manage users, external connections, and administration history.": (
            "사용자, 외부 연결, 관리자 변경 이력을 관리합니다."
        ),
    }
    for english, korean in descriptions.items():
        assert english in shell.text
        assert f'"{english}": "{korean}"' in script.text
    assert "Local account control" not in shell.text + script.text


def test_list_sections_remove_kickers_and_use_direct_bilingual_titles(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = request(app, "GET", "/ui/app.js")

    for removed_copy in [
        "Correlated failures",
        "Deterministic correlations",
        "Stored reasoning attempts",
        "Human principals",
        "Encrypted outbound configuration",
        "Append-only control history",
    ]:
        assert removed_copy not in shell.text + script.text

    titles = {
        "Incident list": "인시던트 목록",
        "Error Signatures": "오류 시그니처",
        "Diagnosis History": "진단 이력",
        "User list": "사용자 목록",
        "External connections": "외부 연결",
        "Administrator audit history": "관리자 감사 이력",
    }
    for english, korean in titles.items():
        assert english in shell.text
        assert f'"{english}": "{korean}"' in script.text


def test_primary_navigation_uses_a_collapsible_left_sidebar(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = request(app, "GET", "/ui/app.css")
    script = request(app, "GET", "/ui/app.js")

    header = shell.text[
        shell.text.index('<header class="site-header navbar">') : shell.text.index("</header>")
    ]
    sidebar = shell.text[
        shell.text.index('<aside id="site-sidebar"') : shell.text.index("</aside>")
    ]
    assert 'id="sidebar-toggle"' not in header
    assert 'id="sidebar-toggle"' in sidebar
    assert sidebar.index('id="sidebar-toggle"') > sidebar.index("</nav>")
    assert 'aria-controls="primary-navigation"' in sidebar
    assert 'aria-expanded="true"' in sidebar
    assert 'id="sidebar-toggle-icon"' in sidebar
    assert 'id="site-sidebar" class="site-sidebar navbar navbar-vertical" hidden' in shell.text
    assert '<span class="nav-label">Error patterns</span>' in shell.text
    assert '<span class="nav-label">Diagnosis History</span>' in shell.text
    assert ".app-shell" in stylesheet.text
    assert ".site-sidebar" in stylesheet.text
    assert "body.sidebar-collapsed .app-shell" in stylesheet.text
    assert "body.sidebar-hidden .app-shell" in stylesheet.text
    assert "grid-template-columns: 48px minmax(0, 1fr)" in stylesheet.text
    assert "body.sidebar-collapsed .primary-nav" in stylesheet.text
    assert "function setSidebarCollapsed(collapsed)" in script.text
    assert "function setSidebarVisibility(visible)" in script.text
    assert "setSidebarVisibility(false)" in script.text
    assert "setSidebarVisibility(true)" in script.text
    assert 'document.body.classList.toggle("sidebar-hidden", !navigationVisible)' in script.text
    assert "elements.siteSidebar.hidden = !navigationVisible" in script.text
    assert 'elements.sidebarToggleIcon.textContent = sidebarCollapsed ? ">" : "<"' in script.text
    assert 'elements.sidebarToggle.addEventListener("click"' in script.text


def test_operational_lists_use_compact_headers_and_data_first_tables(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = request(app, "GET", "/ui/app.css")
    script = request(app, "GET", "/ui/app.js")

    assert 'class="page-header-actions"' in shell.text
    assert 'id="refresh-incidents"' in shell.text
    assert 'class="incident-summary"' in shell.text
    assert 'id="incident-open-total"' in shell.text
    assert 'id="incident-acknowledged-total"' in shell.text
    incident_list = shell.text.split('id="incident-dashboard"')[1].split(
        'id="signature-dashboard"'
    )[0]
    assert '<th scope="col">Error summary</th>' in incident_list
    assert '<th scope="col">Error Signature</th>' not in incident_list
    assert '<th scope="col">First seen</th>' not in incident_list
    assert incident_list.count('<th scope="col"') == 7
    assert 'aria-describedby="incident-summary-scope"' in incident_list
    assert "incident.error_signature_id" in script.text
    assert 'id="detail-first-seen"' in shell.text
    assert 'id="detail-incident-id"' in shell.text
    assert 'id="detail-signature-link"' in shell.text
    assert "radial-gradient" not in stylesheet.text
    assert "linear-gradient" not in stylesheet.text


def test_filter_and_managed_connection_controls_share_an_explicit_height(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = request(app, "GET", "/ui/app.css")

    assert shell.text.count('class="filter-panel card card-body uniform-control-height"') == 5
    assert 'class="connection-form card card-body uniform-control-height"' in shell.text
    assert "--control-height: 38px" in stylesheet.text
    assert ".uniform-control-height input," in stylesheet.text
    assert ".uniform-control-height select" in stylesheet.text
    uniform_controls = stylesheet.text[
        stylesheet.text.index(".uniform-control-height input,") : stylesheet.text.index(
            "\n.filter-grid {"
        )
    ]
    assert "height: var(--control-height)" in uniform_controls
    assert "padding: 6px 12px" in uniform_controls
    assert "line-height: 1.25" in uniform_controls


def test_incident_detail_url_serves_same_secure_ui_shell(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    response = request(
        create_app(settings, session_factory),
        "GET",
        "/ui/?incident=00000000-0000-0000-0000-000000000001",
    )

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert 'id="detail-failures"' in response.text
    assert 'id="transition-history"' in response.text
    assert 'id="operator-controls"' in response.text
    assert 'id="transition-dialog"' in response.text
    assert response.text.index('id="human-diagnosis-title"') < response.text.index(
        'id="human-diagnosis-history-title"'
    )


def test_incident_detail_uses_clear_korean_copy_and_collapses_version_metadata(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = request(app, "GET", "/ui/app.js")
    stylesheet = request(app, "GET", "/ui/app.css")

    deleted_kickers = (
        "Incident investigation",
        "Chronological task tries",
        "Operator actions",
        "Immutable audit trail",
        "Correlation identity",
    )
    for kicker in deleted_kickers:
        assert kicker not in shell.text
        assert kicker not in script.text

    assert '"Failure tries": "실패 횟수"' in script.text
    assert '"Diagnosis attempts": "진단 결과"' in script.text
    assert 'Fingerprint: "시그니처 ID"' in script.text
    assert 'Effective: "대표 진단"' in script.text
    assert '"Not effective": "대표 진단 아님"' in script.text
    assert '"No effective result": "대표 진단 없음"' in script.text
    assert '"Not an effective Diagnosis": "대표 진단 아님"' in script.text
    assert '"Open effective Diagnosis": "대표 진단 보기"' in script.text
    assert "return `대표 진단 ${match[1]}`;" in script.text
    assert 'Review: "운영자 검토"' in script.text
    assert 'PASSED: "검증 통과"' in script.text
    assert 'Acknowledge: "조사 시작"' in script.text
    assert 'Resolve: "처리 완료"' in script.text
    assert 'Ignore: "무시"' in script.text
    assert '"Explore recurring occurrences →": "동일 오류 발생 이력 →"' in script.text
    assert "return `발생 ${match[1]}회`;" in script.text
    assert (
        "return `${match[1]}차 시도 · ${KOREAN_TRANSLATIONS[match[2]] || match[2]}`;" in script.text
    )
    assert '"Check the target service health.": "대상 서비스 상태를 확인합니다."' in script.text
    assert (
        '"Confirm port 6543 is listening.": "6543 포트가 LISTEN 상태인지 확인합니다."'
        in script.text
    )
    assert 'textElement("summary", "Version details")' in script.text
    assert "diagnosis-version-details" in script.text
    assert ".diagnosis-version-details summary" in stylesheet.text
    assert "schema v${diagnosis.diagnosis_schema_version}" in script.text
    assert "Change state" in shell.text
    assert "State history" in shell.text
    assert 'id="transition-count" class="history-count badge bg-secondary-lt"' in shell.text
    assert "Incident ID" in shell.text
    transition_heading = stylesheet.text[
        stylesheet.text.index(".transition-heading strong {") : stylesheet.text.index(
            ".transition-heading time,"
        )
    ]
    assert "flex-shrink: 0" in transition_heading
    assert "white-space: nowrap" in transition_heading
    transition_copy = stylesheet.text[
        stylesheet.text.index(".transition-list p {") : stylesheet.text.index(
            ".transition-list blockquote {"
        )
    ]
    assert "word-break: keep-all" in transition_copy
    assert 'REOPENED: "다시 열림"' in script.text
    assert "elements.transitionCount.textContent = String(transitions.length)" in script.text
    assert 'classList.toggle("is-scrollable", transitions.length > 3)' in script.text
    assert "const orderedTransitions = [...transitions].reverse()" in script.text
    assert "for (const transition of orderedTransitions)" in script.text
    assert 'return "REOPENED"' in script.text
    assert (
        "`${translatedText(transition.previous_status)} → "
        "${translatedText(statusLabel)} · ${transition.actor}`"
    ) in script.text
    assert "${transition.initiator}" not in script.text
    scrollable_history = stylesheet.text[
        stylesheet.text.index(".transition-list.is-scrollable {") : stylesheet.text.index(
            ".transition-list > li {"
        )
    ]
    assert "overflow-y: auto" in scrollable_history
    assert "max-height:" in scrollable_history


def test_web_ui_transition_request_uses_optimistic_status_without_browser_actor(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    script = request(create_app(settings, session_factory), "GET", "/ui/app.js")

    assert 'method: "PATCH"' in script.text
    assert "expected_status: currentIncidentStatus" in script.text
    assert "body.actor" not in script.text
    assert "response.status === 409" in script.text
    assert "TERMINAL_OPERATOR_ACTIONS" in script.text
    assert "currentUser.email" in script.text
    assert "lastTransition.actor === currentUser.email" in script.text
    assert "renderOperatorControls(incident.status, payload.transitions)" in script.text
    assert 'status: "OPEN"' in script.text
    assert 'label: "Reopen"' in script.text
    assert '"Another active Incident already exists for this failure group"' in script.text
    assert "function normalizedErrorDetail(detail, fallback)" in script.text
    assert "Array.isArray(detail)" in script.text
    assert 'typeof item.msg !== "string"' in script.text
    assert 'item.loc.filter((part) => part !== "body").join(".")' in script.text


def test_operator_diagnosis_history_uses_latest_open_timeline_layout(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = request(app, "GET", "/ui/app.js")
    stylesheet = request(app, "GET", "/ui/app.css")

    renderer = script.text[
        script.text.index("async function renderHumanDiagnosisHistory()") : script.text.index(
            "function openHumanDiagnosisDialog()"
        )
    ]
    assert "for (const [index, revision] of history.items.entries())" in renderer
    assert "details.open = index === 0" in renderer
    assert 'fields.className = "human-diagnosis-revision-fields"' in renderer
    assert 'definitionItem("생성 시각"' not in renderer
    assert ".human-diagnosis-history li summary" in stylesheet.text
    assert "#incident-diagnoses {" in stylesheet.text
    assert shell.text.index('id="incident-diagnoses"') < shell.text.index(
        'id="operator-controls"'
    )
    assert ".human-diagnosis-revision-fields" in stylesheet.text
    assert "width: 100%" in stylesheet.text
    assert "justify-content: flex-start" in stylesheet.text
    assert "grid-template-columns: repeat(2, minmax(160px, 240px))" in stylesheet.text
    assert ".transition-list > li::before" in stylesheet.text
    assert ".human-diagnosis-actions-section li::marker" in stylesheet.text
    assert (
        shell.text.index('id="detail-failures"')
        < shell.text.index(
            'class="context-panel card card-body human-diagnosis-history disclosure-panel"'
        )
        < shell.text.index('class="detail-aside"')
    )


def test_operator_confirmed_diagnosis_uses_site_ui_font(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = request(app, "GET", "/ui/app.css")

    assert 'class="aside-panel card card-body human-diagnosis-panel disclosure-panel"' in shell.text
    assert ".human-diagnosis-panel dt," in stylesheet.text
    assert ".human-diagnosis-panel .history-count," in stylesheet.text
    assert ".human-diagnosis-panel .btn," in stylesheet.text
    assert (
        "font-family: var(--tblr-font-sans-serif);"
        in stylesheet.text[
            stylesheet.text.index(".human-diagnosis-panel,") : stylesheet.text.index(
                "}",
                stylesheet.text.index(".human-diagnosis-panel,"),
            )
        ]
    )


def test_admin_user_management_ui_is_role_gated_and_uses_write_only_password_fields(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=admin")
    stylesheet = request(app, "GET", "/ui/app.css")
    script = request(app, "GET", "/ui/app.js")

    assert 'id="admin-nav" class="nav-link" href="/ui/?view=admin" hidden' in shell.text
    assert 'id="admin-nav" data-i18n-fixed' not in shell.text
    assert 'id="admin-dashboard"' in shell.text
    assert 'id="admin-create-form"' in shell.text
    assert 'id="admin-user-password"' in shell.text
    assert 'autocomplete="new-password"' in shell.text
    assert 'id="password-reset-dialog"' in shell.text
    assert 'id="change-password-form"' in shell.text
    assert 'storedSession().role === "admin"' in script.text
    assert "currentUser.must_change_password" in script.text
    assert 'fetch("/api/v1/auth/change-password"' in script.text
    assert 'adminApiRequest("/api/v1/admin/users?limit=200")' in script.text
    assert 'adminApiRequest("/api/v1/admin/audit-events?limit=100")' in script.text
    assert "/reset-password`" in script.text
    assert "/revoke-sessions`" in script.text
    assert ".admin-create-grid" in stylesheet.text


def test_admin_managed_connection_ui_masks_secrets_and_uses_read_only_tests(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=admin")
    stylesheet = request(app, "GET", "/ui/app.css")
    script = request(app, "GET", "/ui/app.js")

    assert 'id="connection-form"' in shell.text
    assert 'id="connection-provider"' in shell.text
    assert 'id="connection-secret"' in shell.text
    assert 'aria-describedby="connection-secret-help"' in shell.text
    assert 'id="connection-secret-help-trigger"' in shell.text
    assert 'role="tooltip"' in shell.text
    secret_group_start = shell.text.index('id="connection-secret-group"')
    secret_group_end = shell.text.index("</div>", secret_group_start)
    assert secret_group_start < shell.text.index('id="connection-secret-help"') < secret_group_end
    assert 'name="secret"' in shell.text
    assert 'type="password"' in shell.text
    assert 'id="admin-connections-empty"' in shell.text
    assert 'id="admin-connections-table"' in shell.text
    assert 'id="admin-connection-rows"' in shell.text
    assert 'adminApiRequest("/api/v1/admin/connections?limit=200")' in script.text
    assert "/api/v1/admin/connections/${encodeURIComponent(connection.id)}/test" in script.text
    assert "/api/v1/admin/connections/${encodeURIComponent(connection.id)}/disable" in script.text
    assert 'typeof crypto.randomUUID === "function"' in script.text
    assert "crypto.getRandomValues(new Uint8Array(16))" in script.text
    assert "editingConnection?.id || generateUuid()" in script.text
    assert "•••••• Configured" in script.text
    assert "elements.adminConnectionsEmpty.hidden = hasConnections" in script.text
    assert "elements.adminConnectionsTable.hidden = !hasConnections" in script.text
    assert "secret_ciphertext" not in shell.text + script.text
    assert "secret_nonce" not in shell.text + script.text
    assert ".connection-form-grid" in stylesheet.text
    assert ".field-help-popover" in stylesheet.text
    assert "position: absolute" in stylesheet.text
    assert ".field-help-trigger:focus-visible + .field-help-tooltip" in stylesheet.text


def test_web_ui_includes_authenticated_error_signature_list_and_detail_views(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=signatures")
    script = request(app, "GET", "/ui/app.js")

    assert shell.status_code == 200
    assert 'id="signature-dashboard"' in shell.text
    assert 'id="signature-detail"' in shell.text
    assert 'id="signature-filter-form"' in shell.text
    assert "fetch(`/api/v1/error-signatures?" in script.text
    assert "fetch(`/api/v1/error-signatures/${encodeURIComponent(signatureId)}`" in script.text
    assert "}/occurrences?${occurrenceParams.toString()}`" in script.text
    assert "}/trend?${trendParams.toString()}`" in script.text


def test_error_signature_list_is_compact_and_preserves_signature_distinction(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=signatures")
    script = request(app, "GET", "/ui/app.js")

    assert 'id="signature-total" class="page-result-total"' in shell.text
    assert '<button class="btn btn-primary" type="submit">Search</button>' in shell.text
    assert '<th scope="col">Operator / Exception</th>' in shell.text
    assert shell.text.index('<th scope="col" class="numeric">Incidents</th>') < shell.text.index(
        '<th scope="col" class="numeric">Failures</th>',
        shell.text.index('id="signature-results"'),
    )
    assert 'signature.normalized_message || "No normalized message"' in script.text
    assert "[signature.exception_class, signature.vendor_error_code].filter(Boolean)" in script.text
    assert "sparkline" not in shell.text + script.text


def test_error_signature_detail_prioritizes_occurrences_and_verified_diagnosis(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(
        app, "GET", "/ui/?view=signatures&signature=00000000-0000-0000-0000-000000000001"
    )
    stylesheet = request(app, "GET", "/ui/app.css")
    script = request(app, "GET", "/ui/app.js")

    assert 'class="breadcrumb" aria-label="Breadcrumb"' in shell.text
    assert 'id="signature-breadcrumb-current"' in shell.text
    assert 'id="signature-trend-7"' in shell.text
    assert 'id="signature-trend-30"' in shell.text
    assert (
        'aria-pressed="true" class="btn btn-outline-secondary btn-sm">7 days</button>' in shell.text
    )
    assert '<th scope="col">Environment</th>' in shell.text
    assert shell.text.index('id="signature-latest-diagnosis"') < shell.text.index(
        'class="detail-aside signature-detail-aside"',
    )
    assert 'copyDefinitionItem("Fingerprint", signature.fingerprint)' in script.text
    signature_identity = script.text[
        script.text.index("function renderSignatureIdentity") : script.text.index(
            "function addUtcDays"
        )
    ]
    assert (
        'item.className = "diagnosis-card card card-body operator-diagnosis-card"'
        in signature_identity
    )
    assert 'body.className = "operator-diagnosis-body"' in signature_identity
    assert 'footer.className = "operator-diagnosis-actions"' in signature_identity
    assert (
        'incidentButton.className = "btn btn-outline-secondary operator-diagnosis-button"'
        in signature_identity
    )
    assert ".operator-diagnosis-card .diagnosis-card-fields" in stylesheet.text
    assert "currentSignatureTrendDays = 7" in script.text
    assert 'elements.signatureTrend30.addEventListener("click"' in script.text
    trend_query = script.text[
        script.text.index("function trendQueryForSignature") : script.text.index(
            "function updateSignatureTrendControls"
        )
    ]
    assert "const dateFrom = addUtcDays(dateTo, -(days - 1))" in trend_query
    assert "signature.first_seen_at" not in trend_query
    render_trend = script.text[
        script.text.index("function renderSignatureTrend") : script.text.index(
            "function renderSignatureOccurrences"
        )
    ]
    assert (
        "elements.signatureTrend.scrollLeft = elements.signatureTrend.scrollWidth" in render_trend
    )
    assert (
        "grid-template-columns: minmax(0, 1fr)"
        in stylesheet.text[
            stylesheet.text.index(".signature-detail-main {") : stylesheet.text.index(
                ".trend-panel {"
            )
        ]
    )
    trend_panel = stylesheet.text[
        stylesheet.text.index(".trend-panel {") : stylesheet.text.index(".trend-controls {")
    ]
    assert "min-width: 0" in trend_panel
    trend_chart = stylesheet.text[
        stylesheet.text.index(".trend-chart {") : stylesheet.text.index(".trend-chart li {")
    ]
    assert "width: 100%" in trend_chart
    assert "overflow-x: auto" in trend_chart


def test_error_signature_detail_removes_decorative_kickers_and_uses_clear_labels(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=signatures")
    script = request(app, "GET", "/ui/app.js")

    deleted_kickers = (
        "Recurring Error Signature",
        "Daily Failure occurrences",
        "Exact linked Failure Tries",
        "Canonical fingerprint fields",
        "Latest validated original",
    )
    for kicker in deleted_kickers:
        assert kicker not in shell.text
        assert kicker not in script.text

    assert 'id="signature-version"' not in shell.text
    assert '<span class="source-badge badge">Deterministic</span>' not in shell.text
    assert "signatureVersion" not in script.text
    assert "<dt>Failure count</dt>" in shell.text
    assert "<dt>Incident count</dt>" in shell.text
    assert '<th scope="col">Run information</th>' in shell.text
    assert '<th scope="col">Failure time</th>' in shell.text
    assert '"Failure count": "실패 횟수"' in script.text
    assert '"Incident count": "인시던트 수"' in script.text
    assert 'Fingerprint: "시그니처 ID"' in script.text
    assert '"Fingerprint version": "시그니처 버전"' in script.text
    assert '"Run information": "실행 정보"' in script.text
    assert '"Failure time": "발생 시각"' in script.text
    assert '"Inspect Diagnosis": "상세 보기"' in script.text
    assert "Occurrence trend" in shell.text
    assert "Occurrences" in shell.text
    assert "Signature Metadata" in shell.text
    assert "Latest Verified Diagnosis" in shell.text


def test_web_ui_includes_authenticated_diagnosis_history_and_detail_views(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=diagnoses")
    script = request(app, "GET", "/ui/app.js")

    assert shell.status_code == 200
    assert 'id="diagnosis-dashboard"' in shell.text
    assert 'id="diagnosis-detail"' in shell.text
    assert 'id="diagnosis-filter-form"' in shell.text
    assert "fetch(`/api/v1/diagnoses?" in script.text
    assert "fetch(`/api/v1/diagnoses/${encodeURIComponent(diagnosisId)}`" in script.text


def test_diagnosis_history_prioritizes_source_validation_and_signature_context(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=diagnoses")
    script = request(app, "GET", "/ui/app.js")

    assert 'id="diagnosis-passed-total"' in shell.text
    assert 'id="diagnosis-reused-total"' in shell.text
    assert 'id="diagnosis-rejected-total"' in shell.text
    assert '<button class="btn btn-primary" type="submit">Search</button>' in shell.text
    diagnosis_table = shell.text[
        shell.text.index('id="diagnosis-results"') : shell.text.index('id="diagnosis-detail"')
    ]
    for heading in [
        "Incident",
        "Error Signature",
        "Source",
        "Root cause",
        "Status",
        "Actor",
        "Created",
    ]:
        assert f'<th scope="col">{heading}</th>' in diagnosis_table
    assert "diagnosisHistorySourceBadge(diagnosis.source_type)" in script.text
    assert "diagnosisHistoryStatusBadge(diagnosis.status)" in script.text
    assert 'diagnosis.actor_identity || "—"' in script.text
    assert "diagnosis.error_signature_id" in script.text


def test_diagnosis_detail_orders_root_cause_evidence_actions_and_related_context(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(
        app, "GET", "/ui/?view=diagnoses&diagnosis=00000000-0000-0000-0000-000000000001"
    )
    stylesheet = request(app, "GET", "/ui/app.css")
    script = request(app, "GET", "/ui/app.js")

    assert 'class="breadcrumb" aria-label="Breadcrumb"' in shell.text
    assert 'id="diagnosis-breadcrumb-current"' in shell.text
    assert 'id="diagnosis-detail-source"' in shell.text
    main = shell.text[
        shell.text.index('class="detail-main diagnosis-detail-main"') : shell.text.index(
            'class="detail-aside diagnosis-detail-aside"'
        )
    ]
    ordered_ids = [
        'id="diagnosis-detail-card"',
        'id="diagnosis-log-panel"',
        'id="diagnosis-similar"',
        'id="diagnosis-failure-fields"',
    ]
    assert [main.index(value) for value in ordered_ids] == sorted(
        main.index(value) for value in ordered_ids
    )
    assert 'id="diagnosis-linked-incident"' in shell.text
    assert 'copyDefinitionItem("Diagnosis ID", diagnosis.id)' in script.text
    assert 'copyDefinitionItem("Failure Event ID", diagnosis.failure_event_id)' in script.text
    assert 'copyDefinitionItem("Signature ID", diagnosis.error_signature_id)' in script.text
    assert "error_signature_id: diagnosis.error_signature_id" in script.text
    assert 'renderEvidence(diagnosis.evidence, "h2")' in script.text
    assert 'renderActions(diagnosis.recommended_actions, "h2")' in script.text
    assert 'textElement("span", String(item.line_id), "evidence-line-id")' in script.text
    assert ".evidence-line-id" in stylesheet.text
    assert "grid-template-columns: 40px minmax(0, 1fr)" in stylesheet.text


def test_diagnosis_detail_removes_redundant_kickers_and_collapses_technical_metadata(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=diagnoses")
    stylesheet = request(app, "GET", "/ui/app.css")
    script = request(app, "GET", "/ui/app.js")

    deleted_kickers = (
        "Diagnosis provenance record",
        "Versioned provenance",
        "Same Error Signature",
        "Exact operational input",
        "Operational relationship",
        "Resolved relationships",
    )
    for kicker in deleted_kickers:
        assert kicker not in shell.text
        assert kicker not in script.text

    assert (
        '<h2 id="diagnosis-similar-title" class="card-title">Similar Diagnosis</h2>' in shell.text
    )
    assert (
        '<h2 id="diagnosis-failure-title" class="card-title">Failure run information</h2>'
        in shell.text
    )
    assert '<h2 class="card-title">Diagnosis Metadata</h2>' in shell.text
    assert '<h2 class="card-title">Linked Incident</h2>' in shell.text
    assert '<h2 class="card-title">Error Signature</h2>' in shell.text
    assert '"Similar Diagnosis": "유사 진단 이력"' in script.text
    assert '"Failure run information": "실패 실행 정보"' in script.text
    assert '"Reasoning metadata": "근거 메타데이터"' in script.text
    assert '"Inspect related Incident": "인시던트 보기"' in script.text
    assert '"Explore Error Signature": "오류 시그니처 보기"' in script.text
    assert '"View details": "상세 보기"' in script.text

    technical_details = shell.text[
        shell.text.index('class="diagnosis-technical-details"') : shell.text.index(
            "</details>", shell.text.index('class="diagnosis-technical-details"')
        )
    ]
    assert "open" not in technical_details
    assert "Technical details" in technical_details
    assert 'id="diagnosis-technical-fields"' in technical_details
    assert "diagnosisTechnicalFields" in script.text
    assert (
        'copyDefinitionItem("Content Diagnosis ID", diagnosis.content_diagnosis_id)' in script.text
    )
    assert 'definitionItem("Schema", `v${diagnosis.diagnosis_schema_version}`)' in script.text
    assert ".diagnosis-technical-details" in stylesheet.text
    assert 'id="diagnosis-related-diagnoses"' in shell.text
    assert 'id="diagnosis-signature-link"' in shell.text


def test_web_ui_exposes_keyboard_and_assistive_technology_contracts(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = request(app, "GET", "/ui/app.css")
    script = request(app, "GET", "/ui/app.js")

    assert 'class="skip-link" href="#main-content"' in shell.text
    assert (
        'id="detail-feedback" class="notice alert notice-success alert-success" role="status" tabindex="-1"'
        in shell.text
    )
    assert 'aria-describedby="transition-dialog-copy"' in shell.text
    assert 'class="table-wrap table-responsive" role="region" tabindex="0"' in shell.text
    assert shell.text.count('<caption class="visually-hidden">') == 6
    assert 'id="signature-trend"' in shell.text
    assert 'aria-labelledby="signature-trend-title signature-trend-range"' in shell.text
    assert "document.title" in script.text
    assert "elements.detailFeedback.focus()" in script.text
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet.text
    assert "@media (forced-colors: active)" in stylesheet.text
    assert "@media (max-width: 1050px)" in stylesheet.text
    assert "@media (max-width: 680px)" in stylesheet.text
