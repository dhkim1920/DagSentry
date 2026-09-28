from __future__ import annotations

from dagsentry.api import create_app
from dagsentry.config import Settings
from dagsentry.db import SessionFactory
from tests.test_incident_api import request
from tests.web_assets import asset_source


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

    asset_source(create_app(settings, session_factory), "/ui/app.js")
    assert 'src="/ui/app.js?v=20260928-modules1"' in response.text


def test_auth_title_remains_exactly_two_lines_in_both_languages(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = asset_source(app, "/ui/app.css")
    script = asset_source(app, "/ui/app.js")

    assert '<span class="auth-title-line">See the failure.</span>' in shell.text
    assert '<span class="auth-title-line">Trace the evidence.</span>' in shell.text
    assert "Operational visibility for Airflow" not in shell.text + script
    assert "Sign in with your DagSentry account" not in shell.text + script
    assert "Sanitized evidence only" not in shell.text + script
    assert "Server-enforced roles" not in shell.text + script
    assert "Selectable timezone display" not in shell.text + script
    assert "Secure session" not in shell.text + script
    assert "protected HttpOnly cookie" not in shell.text + script
    assert '"See the failure.": "실패를 확인하고."' in script
    assert '"Trace the evidence.": "근거를 추적하세요."' in script
    assert ".auth-title-line" in stylesheet
    assert "display: block" in stylesheet
    assert "white-space: nowrap" in stylesheet


def test_temporary_password_form_omits_redundant_introductory_copy(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = asset_source(app, "/ui/app.js")

    assert "Required account step" not in shell.text + script
    assert "Choose a personal password before opening the dashboard." not in shell.text + script
    assert "Change temporary password" in shell.text
    assert 'id="current-password"' in shell.text


def test_login_shows_centered_loading_overlay_and_locks_auth_controls(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = asset_source(app, "/ui/app.css")
    script = asset_source(app, "/ui/app.js")

    assert 'id="auth-loading"' in shell.text
    assert 'class="auth-loading-overlay"' in shell.text
    assert 'role="status"' in shell.text
    assert 'aria-live="polite"' in shell.text
    assert 'id="auth-submit"' in shell.text
    assert ".auth-loading-overlay" in stylesheet
    assert "position: fixed" in stylesheet
    assert "place-items: center" in stylesheet
    assert "function setAuthLoading(isLoading)" in script
    assert "elements.authLoading.hidden = !isLoading" in script
    assert "elements.authSubmit.disabled = isLoading" in script
    assert "setAuthLoading(true)" in script
    assert "setAuthLoading(false)" in script


def test_session_bootstrap_prevents_auth_flash_and_primary_navigation_reload(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = asset_source(app, "/ui/app.js")

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
    assert 'primaryNavigation: document.querySelector("#primary-navigation")' in script
    assert 'elements.primaryNavigation.addEventListener("click"' in script
    assert "event.preventDefault()" in script
    assert 'history.pushState({}, "", link.href)' in script
    assert "await loadCurrentView()" in script
    assert "elements.sessionLoading.hidden = true" in script


def test_web_ui_assets_are_packaged_and_use_authenticated_incident_api(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)

    asset_source(app, "/ui/app.css")
    script = asset_source(app, "/ui/app.js")

    assert "sessionStorage" not in script
    assert "apiJson(`/api/v1/incidents?" in script
    assert "apiJson(`/api/v1/incidents/${encodeURIComponent(incidentId)}`" in script
    assert 'apiJson("/api/v1/auth/login"' in script
    assert 'apiRequest("/api/v1/auth/logout"' in script
    assert 'apiJson("/api/v1/auth/me"' in script
    assert "X-CSRF-Token" in script
    assert "X-DagSentry-Viewer-Token" not in script
    assert "X-DagSentry-Operator-Token" not in script


def test_web_ui_supports_persisted_korean_and_english_language_switching(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = asset_source(app, "/ui/app.css")
    script = asset_source(app, "/ui/app.js")

    assert 'id="language-select"' in shell.text
    assert '<option value="ko">한국어</option>' in shell.text
    assert '<option value="en">English</option>' in shell.text
    assert "navigator.language" in script
    assert "localStorage.getItem(LANGUAGE_STORAGE_KEY)" in script
    assert "localStorage.setItem(LANGUAGE_STORAGE_KEY" in script
    assert "document.documentElement.lang = state.currentLanguage" in script
    assert '"Matching incidents": "검색 결과"' in script
    assert "#language-select" in stylesheet


def test_web_ui_supports_persisted_timezone_with_selected_timezone_only(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = asset_source(app, "/ui/app.css")
    script = asset_source(app, "/ui/app.js")

    assert 'id="timezone-select"' in shell.text
    assert '<option value="Asia/Seoul">Asia/Seoul</option>' in shell.text
    assert '<option value="UTC">UTC</option>' in shell.text
    assert '<option value="browser">Browser local</option>' in shell.text
    assert 'const TIMEZONE_STORAGE_KEY = "dagsentry.timezone"' in script
    assert "localStorage.getItem(TIMEZONE_STORAGE_KEY)" in script
    assert "localStorage.setItem(TIMEZONE_STORAGE_KEY, state.currentTimezone)" in script
    assert 'state.currentTimezone = "Asia/Seoul"' in script
    assert "function formatScheduleTimestamp(value, timezone)" in script
    assert "return { primary };" in script
    assert "includeUtc" not in script
    assert ").detail" not in script
    assert "timestampBlock(occurrence.observed_at, true)" not in script
    assert "timestampBlock(occurrence.observed_at)" in script
    assert ".timestamp-detail" not in stylesheet
    assert "All timestamps are UTC" not in shell.text + script
    assert "All dates are UTC" not in shell.text + script
    assert "#timezone-select" in stylesheet


def test_primary_navigation_and_page_titles_follow_selected_language_without_global_footer(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = asset_source(app, "/ui/app.js")

    assert 'id="incidents-nav" data-i18n-fixed' not in shell.text
    assert 'id="signatures-nav" data-i18n-fixed' not in shell.text
    assert 'id="diagnoses-nav" data-i18n-fixed' not in shell.text
    assert 'id="reports-nav" data-i18n-fixed' not in shell.text
    assert 'id="dashboard-title" class="page-title">Incident response</h1>' in shell.text
    assert 'id="signature-dashboard-title" class="page-title">Error patterns</h1>' in shell.text
    assert 'id="diagnosis-dashboard-title" class="page-title">Diagnosis History</h1>' in shell.text
    assert 'id="report-dashboard-title" class="page-title">Daily Reports</h1>' in shell.text
    assert '"Incident response": "장애 대응"' in script
    assert '"Error patterns": "오류 패턴"' in script
    assert "<footer>" not in shell.text
    assert 'closest("[data-i18n-fixed]")' in script


def test_daily_report_ui_exposes_list_detail_filters_and_authenticated_api(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = asset_source(app, "/ui/app.js")
    stylesheet = asset_source(app, "/ui/app.css")

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
    assert "apiJson(`/api/v1/daily-reports?" in script
    assert "apiJson(`/api/v1/daily-reports/${encodeURIComponent(reportId)}`" in script
    assert 'params.set("view", "reports")' in script
    assert 'elements.reportFilterForm.addEventListener("submit"' in script
    assert 'apiJson("/api/v1/daily-report-schedules"' in script
    assert 'apiJson("/api/v1/daily-report-schedules/status"' in script
    assert 'apiJson("/api/v1/admin/connections?limit=100"' in script
    assert '"/api/v1/admin/daily-report-schedules"' in script
    assert "리포트 스케줄 설정" in shell.text
    assert "자동 리포트" in shell.text
    assert 'class="visually-hidden">AI 요약 사용</span>' in shell.text
    assert "max-width: 960px" in stylesheet
    assert ".setting-toggle-row > div" in stylesheet
    assert "min-width: 44px" in stylesheet
    assert "전일 UTC 기준 데이터를 집계합니다." in script
    assert "아직 저장된 리포트 스케줄이 없습니다." in script
    assert 'details.className = "report-execution-grid"' in script
    assert ".report-execution-grid .inspect-link" in stylesheet
    assert ".report-manual-run-form .field-group" in stylesheet
    assert "flex: 0 0 168px" in stylesheet
    assert "data-schedule-id" not in script
    assert (
        shell.text.index('id="report-schedule-save"')
        < shell.text.index('id="report-schedule-feedback"')
        < shell.text.index('id="report-manual-run-form"')
    )
    report_schedule_card = stylesheet[
        stylesheet.index(".report-schedule-card {") : stylesheet.index(".report-schedule-card h3,")
    ]
    assert "grid-template-columns: minmax(0, 1fr)" in report_schedule_card
    assert "auto" not in report_schedule_card


def test_page_descriptions_use_matching_english_and_korean_operational_copy(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = asset_source(app, "/ui/app.js")

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
        "Manage user accounts, roles, and sessions.": ("사용자 계정, 권한 및 세션을 관리합니다."),
    }
    for english, korean in descriptions.items():
        assert english in shell.text
        assert f'"{english}": "{korean}"' in script
    assert "Local account control" not in shell.text + script


def test_list_sections_remove_kickers_and_use_direct_bilingual_titles(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = asset_source(app, "/ui/app.js")

    for removed_copy in [
        "Correlated failures",
        "Deterministic correlations",
        "Stored reasoning attempts",
        "Human principals",
        "Encrypted outbound configuration",
        "Append-only control history",
    ]:
        assert removed_copy not in shell.text + script

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
        assert f'"{english}": "{korean}"' in script


def test_primary_navigation_uses_a_collapsible_left_sidebar(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = asset_source(app, "/ui/app.css")
    script = asset_source(app, "/ui/app.js")

    header = shell.text[
        shell.text.index('<header class="site-header navbar">') : shell.text.index("</header>")
    ]
    sidebar = shell.text[
        shell.text.index('<aside id="site-sidebar"') : shell.text.index("</aside>")
    ]
    assert 'id="sidebar-toggle"' in header
    assert 'id="sidebar-toggle"' not in sidebar
    assert 'aria-controls="primary-navigation"' in header
    assert 'aria-expanded="true"' in header
    assert 'class="navbar-toggler-icon"' in header
    assert (
        'id="site-sidebar" class="site-sidebar navbar navbar-vertical navbar-expand-lg" hidden'
        in shell.text
    )
    assert '<span class="nav-label nav-link-title">Error patterns</span>' in shell.text
    assert '<span class="nav-label nav-link-title">Diagnosis History</span>' in shell.text
    assert ".app-shell" in stylesheet
    assert ".site-sidebar" in stylesheet
    assert "body.sidebar-hidden .app-shell" in stylesheet
    assert "grid-template-columns: var(--tblr-sidebar-width) minmax(0, 1fr)" in stylesheet
    assert "body.sidebar-collapsed .site-sidebar" in stylesheet
    assert "function setSidebarCollapsed(collapsed)" in script
    assert "function setSidebarVisibility(visible)" in script
    assert "setSidebarVisibility(false)" in script
    assert "setSidebarVisibility(true)" in script
    assert 'document.body.classList.toggle("sidebar-hidden", !navigationVisible)' in script
    assert "elements.siteSidebar.hidden = !navigationVisible" in script
    assert 'setAttribute("data-bs-sidebar", "folded-hover")' in script
    assert 'sidebarMedia.addEventListener("change"' in script
    assert 'elements.sidebarToggle.addEventListener("click"' in script


def test_operational_lists_use_compact_headers_and_data_first_tables(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = asset_source(app, "/ui/app.css")
    script = asset_source(app, "/ui/app.js")

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
    assert "incident.error_signature_id" in script
    assert 'id="detail-first-seen"' in shell.text
    assert 'id="detail-incident-id"' in shell.text
    assert 'id="detail-signature-link"' in shell.text
    assert "radial-gradient" not in stylesheet
    assert "linear-gradient" not in stylesheet


def test_filter_and_managed_connection_controls_share_an_explicit_height(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = asset_source(app, "/ui/app.css")

    assert shell.text.count('class="filter-panel card uniform-control-height"') == 3
    assert shell.text.count('class="filter-panel card card-body uniform-control-height"') == 2
    assert 'class="connection-form card card-body uniform-control-height"' in shell.text
    assert "--control-height: 38px" in stylesheet
    assert ".uniform-control-height input," in stylesheet
    assert ".uniform-control-height select" in stylesheet
    uniform_controls = stylesheet[
        stylesheet.index(".uniform-control-height input,") : stylesheet.index("\n.filter-grid {")
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
    script = asset_source(app, "/ui/app.js")
    stylesheet = asset_source(app, "/ui/app.css")

    deleted_kickers = (
        "Incident investigation",
        "Chronological task tries",
        "Operator actions",
        "Immutable audit trail",
        "Correlation identity",
    )
    for kicker in deleted_kickers:
        assert kicker not in shell.text
        assert kicker not in script

    assert '"Failure tries": "실패 횟수"' in script
    assert '"Diagnosis attempts": "진단 결과"' in script
    assert 'Fingerprint: "시그니처 ID"' in script
    assert 'Effective: "대표 진단"' in script
    assert '"Not effective": "대표 진단 아님"' in script
    assert '"No effective result": "대표 진단 없음"' in script
    assert '"Not an effective Diagnosis": "대표 진단 아님"' in script
    assert '"Open effective Diagnosis": "대표 진단 보기"' in script
    assert "return `대표 진단 ${match[1]}`;" in script
    assert 'Review: "운영자 검토"' in script
    assert 'PASSED: "검증 통과"' in script
    assert 'Acknowledge: "조사 시작"' in script
    assert 'Resolve: "처리 완료"' in script
    assert 'Ignore: "무시"' in script
    assert '"Explore recurring occurrences →": "동일 오류 발생 이력 →"' in script
    assert "return `발생 ${match[1]}회`;" in script
    assert "return `${match[1]}차 시도 · ${KOREAN_TRANSLATIONS[match[2]] || match[2]}`;" in script
    assert '"Check the target service health.": "대상 서비스 상태를 확인합니다."' in script
    assert '"Confirm port 6543 is listening.": "6543 포트가 LISTEN 상태인지 확인합니다."' in script
    assert 'textElement("summary", "Version details")' in script
    assert "diagnosis-version-details" in script
    assert ".diagnosis-version-details summary" in stylesheet
    assert "schema v${diagnosis.diagnosis_schema_version}" in script
    assert "Change state" in shell.text
    assert "State history" in shell.text
    assert 'id="transition-count" class="history-count badge bg-secondary-lt"' in shell.text
    assert "Incident ID" in shell.text
    transition_heading = stylesheet[
        stylesheet.index(".transition-heading strong {") : stylesheet.index(
            ".transition-heading time,"
        )
    ]
    assert "flex-shrink: 0" in transition_heading
    assert "white-space: nowrap" in transition_heading
    transition_copy = stylesheet[
        stylesheet.index(".transition-list p {") : stylesheet.index(".transition-list blockquote {")
    ]
    assert "word-break: keep-all" in transition_copy
    assert 'REOPENED: "다시 열림"' in script
    assert "elements.transitionCount.textContent = String(transitions.length)" in script
    assert 'classList.toggle("is-scrollable", transitions.length > 3)' in script
    assert "const orderedTransitions = [...transitions].reverse()" in script
    assert "for (const transition of orderedTransitions)" in script
    assert 'return "REOPENED"' in script
    assert (
        "`${translatedText(transition.previous_status)} → "
        "${translatedText(statusLabel)} · ${transition.actor}`"
    ) in script
    assert "${transition.initiator}" not in script
    scrollable_history = stylesheet[
        stylesheet.index(".transition-list.is-scrollable {") : stylesheet.index(
            ".transition-list > li {"
        )
    ]
    assert "overflow-y: auto" in scrollable_history
    assert "max-height:" in scrollable_history


def test_web_ui_transition_request_uses_optimistic_status_without_browser_actor(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    script = asset_source(create_app(settings, session_factory), "/ui/app.js")

    assert 'method: "PATCH"' in script
    assert "expected_status: state.currentIncidentStatus" in script
    assert "body.actor" not in script
    assert "error.status === 409" in script
    assert "TERMINAL_OPERATOR_ACTIONS" in script
    assert "state.currentUser.email" in script
    assert "lastTransition.actor === state.currentUser.email" in script
    assert "renderOperatorControls(incident.status, payload.transitions)" in script
    assert 'status: "OPEN"' in script
    assert 'label: "Reopen"' in script
    assert '"Another active Incident already exists for this failure group"' in script
    assert "function normalizedErrorDetail(detail, fallback)" in script
    assert "Array.isArray(detail)" in script
    assert 'typeof item.msg !== "string"' in script
    assert 'item.loc.filter((part) => part !== "body").join(".")' in script


def test_operator_diagnosis_history_uses_latest_open_timeline_layout(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    script = asset_source(app, "/ui/app.js")
    stylesheet = asset_source(app, "/ui/app.css")

    renderer = script[
        script.index("async function renderHumanDiagnosisHistory(request)") : script.index(
            "function openHumanDiagnosisDialog()"
        )
    ]
    assert "for (const [index, revision] of history.items.entries())" in renderer
    assert "details.open = index === 0" in renderer
    assert 'fields.className = "human-diagnosis-revision-fields"' in renderer
    assert 'definitionItem("생성 시각"' not in renderer
    assert ".human-diagnosis-history li summary" in stylesheet
    assert "#incident-diagnoses {" in stylesheet
    assert shell.text.index('id="incident-diagnoses"') < shell.text.index('id="operator-controls"')
    assert ".human-diagnosis-revision-fields" in stylesheet
    assert "width: 100%" in stylesheet
    assert "justify-content: flex-start" in stylesheet
    assert "grid-template-columns: repeat(2, minmax(160px, 240px))" in stylesheet
    assert ".transition-list > li::before" in stylesheet
    assert ".human-diagnosis-actions-section li::marker" in stylesheet
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
    stylesheet = asset_source(app, "/ui/app.css")

    assert 'class="aside-panel card card-body human-diagnosis-panel disclosure-panel"' in shell.text
    assert ".human-diagnosis-panel dt," in stylesheet
    assert ".human-diagnosis-panel .history-count," in stylesheet
    assert ".human-diagnosis-panel .btn," in stylesheet
    assert (
        "font-family: var(--tblr-font-sans-serif);"
        in stylesheet[
            stylesheet.index(".human-diagnosis-panel,") : stylesheet.index(
                "}",
                stylesheet.index(".human-diagnosis-panel,"),
            )
        ]
    )


def test_admin_user_management_ui_is_role_gated_and_uses_write_only_password_fields(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=admin")
    stylesheet = asset_source(app, "/ui/app.css")
    script = asset_source(app, "/ui/app.js")

    assert 'id="admin-navigation" class="settings-navigation nav-item" hidden' in shell.text
    assert '<summary id="admin-nav" class="nav-link dropdown-toggle">' in shell.text
    for section in ("users", "connections", "reports", "audit"):
        assert f'data-settings-link="{section}"' in shell.text
        assert f'href="/ui/?view=admin&amp;section={section}"' in shell.text
        assert f'data-settings-page="{section}"' in shell.text
    assert 'id="admin-nav" data-i18n-fixed' not in shell.text
    assert 'id="admin-dashboard"' in shell.text
    assert 'id="admin-create-form"' in shell.text
    assert 'id="admin-user-password"' in shell.text
    assert 'autocomplete="new-password"' in shell.text
    assert 'id="password-reset-dialog"' in shell.text
    assert 'id="change-password-form"' in shell.text
    assert 'storedSession().role === "admin"' in script
    assert "state.currentUser.must_change_password" in script
    assert 'apiRequest("/api/v1/auth/change-password"' in script
    assert 'adminApiRequest("/api/v1/admin/users?limit=200")' in script
    assert 'adminApiRequest("/api/v1/admin/audit-events?limit=100")' in script
    assert "/reset-password`" in script
    assert "/revoke-sessions`" in script
    assert ".admin-create-grid" in stylesheet


def test_admin_managed_connection_ui_masks_secrets_and_uses_read_only_tests(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=admin")
    stylesheet = asset_source(app, "/ui/app.css")
    script = asset_source(app, "/ui/app.js")

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
    assert 'adminApiRequest("/api/v1/admin/connections?limit=200")' in script
    assert "/api/v1/admin/connections/${encodeURIComponent(connection.id)}/test" in script
    assert "/api/v1/admin/connections/${encodeURIComponent(connection.id)}/disable" in script
    assert 'typeof crypto.randomUUID === "function"' in script
    assert "crypto.getRandomValues(new Uint8Array(16))" in script
    assert "state.editingConnection?.id || generateUuid()" in script
    assert "•••••• Configured" in script
    assert "elements.adminConnectionsEmpty.hidden = hasConnections" in script
    assert "elements.adminConnectionsTable.hidden = !hasConnections" in script
    assert "secret_ciphertext" not in shell.text + script
    assert "secret_nonce" not in shell.text + script
    assert ".connection-form-grid" in stylesheet
    assert ".field-help-popover" in stylesheet
    assert "position: absolute" in stylesheet
    assert ".field-help-trigger:focus-visible + .field-help-tooltip" in stylesheet


def test_web_ui_includes_authenticated_error_signature_list_and_detail_views(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=signatures")
    script = asset_source(app, "/ui/app.js")

    assert shell.status_code == 200
    assert 'id="signature-dashboard"' in shell.text
    assert 'id="signature-detail"' in shell.text
    assert 'id="signature-filter-form"' in shell.text
    assert "apiJson(`/api/v1/error-signatures?" in script
    assert "apiJson(`/api/v1/error-signatures/${encodeURIComponent(signatureId)}`" in script
    assert "}/occurrences?${occurrenceParams.toString()}`" in script
    assert "}/trend?${trendParams.toString()}`" in script


def test_error_signature_list_is_compact_and_preserves_signature_distinction(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=signatures")
    script = asset_source(app, "/ui/app.js")

    assert 'id="signature-total" class="page-result-total"' in shell.text
    assert '<button class="btn btn-primary" type="submit">Search</button>' in shell.text
    assert '<th scope="col">Operator / Exception</th>' in shell.text
    assert shell.text.index('<th scope="col" class="numeric">Incidents</th>') < shell.text.index(
        '<th scope="col" class="numeric">Failures</th>',
        shell.text.index('id="signature-results"'),
    )
    assert 'signature.normalized_message || "No normalized message"' in script
    assert "[signature.exception_class, signature.vendor_error_code].filter(Boolean)" in script
    signature_list = shell.text.split('id="signature-dashboard"')[1].split(
        'id="diagnosis-dashboard"'
    )[0]
    assert "sparkline" not in signature_list


def test_error_signature_detail_prioritizes_occurrences_and_verified_diagnosis(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(
        app, "GET", "/ui/?view=signatures&signature=00000000-0000-0000-0000-000000000001"
    )
    stylesheet = asset_source(app, "/ui/app.css")
    script = asset_source(app, "/ui/app.js")

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
    assert 'copyDefinitionItem("Fingerprint", signature.fingerprint)' in script
    signature_identity = script[
        script.index("function renderSignatureIdentity") : script.index("function addUtcDays")
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
    assert ".operator-diagnosis-card .diagnosis-card-fields" in stylesheet
    assert "state.currentSignatureTrendDays = 7" in script
    assert 'elements.signatureTrend30.addEventListener("click"' in script
    trend_query = script[
        script.index("function trendQueryForSignature") : script.index(
            "function updateSignatureTrendControls"
        )
    ]
    assert "const dateFrom = addUtcDays(dateTo, -(days - 1))" in trend_query
    assert "signature.first_seen_at" not in trend_query
    render_trend = script[
        script.index("function renderSignatureTrend") : script.index(
            "function renderSignatureOccurrences"
        )
    ]
    assert "new window.TablerSparkline" in render_trend
    assert "scrollLeft" not in render_trend
    assert 'type: "bar"' in render_trend
    assert 'point.addEventListener("keydown"' in render_trend
    assert (
        "grid-template-columns: minmax(0, 1fr)"
        in stylesheet[
            stylesheet.index(".signature-detail-main {") : stylesheet.index(".trend-panel {")
        ]
    )
    trend_panel = stylesheet[
        stylesheet.index(".trend-panel {") : stylesheet.index(".trend-controls {")
    ]
    assert "min-width: 0" in trend_panel
    trend_chart = stylesheet[
        stylesheet.index(".trend-chart {") : stylesheet.index(".trend-y-axis,")
    ]
    assert "width: 100%" in trend_chart
    assert "grid-template-columns: auto minmax(0, 1fr)" in trend_chart
    assert "min-width: 0" in trend_chart


def test_error_signature_detail_removes_decorative_kickers_and_uses_clear_labels(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=signatures")
    script = asset_source(app, "/ui/app.js")

    deleted_kickers = (
        "Recurring Error Signature",
        "Daily Failure occurrences",
        "Exact linked Failure Tries",
        "Canonical fingerprint fields",
        "Latest validated original",
    )
    for kicker in deleted_kickers:
        assert kicker not in shell.text
        assert kicker not in script

    assert 'id="signature-version"' not in shell.text
    assert '<span class="source-badge badge">Deterministic</span>' not in shell.text
    assert "signatureVersion" not in script
    assert "<dt>Failure count</dt>" in shell.text
    assert "<dt>Incident count</dt>" in shell.text
    assert '<th scope="col">Run information</th>' in shell.text
    assert '<th scope="col">Failure time</th>' in shell.text
    assert '"Failure count": "실패 횟수"' in script
    assert '"Incident count": "인시던트 수"' in script
    assert 'Fingerprint: "시그니처 ID"' in script
    assert '"Fingerprint version": "시그니처 버전"' in script
    assert '"Run information": "실행 정보"' in script
    assert '"Failure time": "발생 시각"' in script
    assert '"Inspect Diagnosis": "상세 보기"' in script
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
    script = asset_source(app, "/ui/app.js")

    assert shell.status_code == 200
    assert 'id="diagnosis-dashboard"' in shell.text
    assert 'id="diagnosis-detail"' in shell.text
    assert 'id="diagnosis-filter-form"' in shell.text
    assert "apiJson(`/api/v1/diagnoses?" in script
    assert "apiJson(`/api/v1/diagnoses/${encodeURIComponent(diagnosisId)}`" in script


def test_diagnosis_history_prioritizes_source_validation_and_signature_context(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=diagnoses")
    script = asset_source(app, "/ui/app.js")

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
    assert "diagnosisHistorySourceBadge(diagnosis.source_type)" in script
    assert "diagnosisHistoryStatusBadge(diagnosis.status)" in script
    assert 'diagnosis.actor_identity || "—"' in script
    assert "diagnosis.error_signature_id" in script


def test_diagnosis_detail_orders_root_cause_evidence_actions_and_related_context(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(
        app, "GET", "/ui/?view=diagnoses&diagnosis=00000000-0000-0000-0000-000000000001"
    )
    stylesheet = asset_source(app, "/ui/app.css")
    script = asset_source(app, "/ui/app.js")

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
    assert 'copyDefinitionItem("Diagnosis ID", diagnosis.id)' in script
    assert 'copyDefinitionItem("Failure Event ID", diagnosis.failure_event_id)' in script
    assert 'copyDefinitionItem("Signature ID", diagnosis.error_signature_id)' in script
    assert "error_signature_id: diagnosis.error_signature_id" in script
    assert 'renderEvidence(diagnosis.evidence, "h2")' in script
    assert 'renderActions(diagnosis.recommended_actions, "h2")' in script
    assert 'textElement("span", String(item.line_id), "evidence-line-id")' in script
    assert ".evidence-line-id" in stylesheet
    assert "grid-template-columns: 40px minmax(0, 1fr)" in stylesheet


def test_diagnosis_detail_removes_redundant_kickers_and_collapses_technical_metadata(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/?view=diagnoses")
    stylesheet = asset_source(app, "/ui/app.css")
    script = asset_source(app, "/ui/app.js")

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
        assert kicker not in script

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
    assert '"Similar Diagnosis": "유사 진단 이력"' in script
    assert '"Failure run information": "실패 실행 정보"' in script
    assert '"Reasoning metadata": "근거 메타데이터"' in script
    assert '"Inspect related Incident": "인시던트 보기"' in script
    assert '"Explore Error Signature": "오류 시그니처 보기"' in script
    assert '"View details": "상세 보기"' in script

    technical_details = shell.text[
        shell.text.index('class="diagnosis-technical-details"') : shell.text.index(
            "</details>", shell.text.index('class="diagnosis-technical-details"')
        )
    ]
    assert "open" not in technical_details
    assert "Technical details" in technical_details
    assert 'id="diagnosis-technical-fields"' in technical_details
    assert "diagnosisTechnicalFields" in script
    assert 'copyDefinitionItem("Content Diagnosis ID", diagnosis.content_diagnosis_id)' in script
    assert 'definitionItem("Schema", `v${diagnosis.diagnosis_schema_version}`)' in script
    assert ".diagnosis-technical-details" in stylesheet
    assert 'id="diagnosis-related-diagnoses"' in shell.text
    assert 'id="diagnosis-signature-link"' in shell.text


def test_web_ui_exposes_keyboard_and_assistive_technology_contracts(
    settings: Settings,
    session_factory: SessionFactory,
) -> None:
    app = create_app(settings, session_factory)
    shell = request(app, "GET", "/ui/")
    stylesheet = asset_source(app, "/ui/app.css")
    script = asset_source(app, "/ui/app.js")

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
    assert "document.title" in script
    assert "elements.detailFeedback.focus()" in script
    assert "@media (prefers-reduced-motion: reduce)" in stylesheet
    assert "@media (forced-colors: active)" in stylesheet
    assert "@media (max-width: 1050px)" in stylesheet
    assert "@media (max-width: 680px)" in stylesheet
