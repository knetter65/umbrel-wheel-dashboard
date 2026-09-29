from __future__ import annotations

from copy import deepcopy
from decimal import Decimal
import json
import os
from pathlib import Path
from typing import Any

import plotly.graph_objects as go
from dash import Dash, Input, Output, dash_table, dcc, html
from plotly.subplots import make_subplots

from .database import (
    ACTIVE_DB_PATH,
    DATA_ROOT,
    DEFAULT_DB_PATH,
    resolve_dashboard_database,
    read_dashboard_snapshot,
    read_market_bars,
)
from .indicators import bollinger_bands, rsi, sma
from .freshness import effective_source, source_label
from .wheel_domain import CycleCashFlows

STATE_LABELS = {
    "PUT_OPEN": "Open short put",
    "ASSIGNED_NO_CALL": "Aandelen zonder call",
    "COVERED_CALL_OPEN": "Covered call open",
    "PARTIALLY_COVERED": "Onvolledig gedekt",
    "CLOSED_PUT_ONLY": "Gesloten zonder assignment",
    "CLOSED_CALLED_AWAY": "Gesloten via call assignment",
    "CLOSED_STOCK_SOLD": "Gesloten via aandelenverkoop",
    "UNRESOLVED": "Reconciliatie vereist",
}


def money(value: Any) -> str:
    if value in (None, ""):
        return "—"
    return f"${Decimal(str(value)):,.2f}"


def enrich_snapshot(raw: dict[str, Any]) -> dict[str, Any]:
    snapshot = deepcopy(raw)
    for cycle in snapshot["cycles"]:
        if Decimal(cycle["remaining_shares"]) <= 0:
            continue
        flows = CycleCashFlows(
            remaining_shares=Decimal(cycle["remaining_shares"]),
            stock_net_cashflow=Decimal(cycle["stock_net_cashflow"]),
            closed_put_net_pnl=Decimal(cycle["realized_put_pnl"]),
            closed_call_net_pnl=Decimal(cycle["realized_call_pnl"]),
            open_option_cashflow=Decimal(cycle["open_option_cashflow"]),
            dividends_net=Decimal(cycle["dividends_net"]),
            open_option_close_cost=Decimal(cycle["open_option_close_cost"]),
        )
        cycle["locked_break_even"] = str(flows.locked_break_even())
        cycle["locked_break_even_with_dividends"] = str(
            flows.locked_break_even(include_dividends=True)
        )
        cycle["cashflow_break_even"] = str(flows.cashflow_break_even())
        cycle["liquidation_break_even"] = str(flows.liquidation_break_even())
    return snapshot


DB_PATH = resolve_dashboard_database(DEFAULT_DB_PATH)


def refresh_runtime_state() -> None:
    """Reload the atomically promoted database on each browser page load."""
    global SNAPSHOT, IS_FIXTURE, IS_ACTIVE, SOURCE, ACCOUNT_SOURCE
    global ACCOUNT_EFFECTIVE, PHASE_LABEL, SOURCE_SUMMARY, REFRESH_STATUS
    global HARDENING_STATUS, HARDENING_REPORT
    global SCHEDULER_CONFIG, SCHEDULE_STATUS
    SNAPSHOT = enrich_snapshot(read_dashboard_snapshot(DB_PATH))
    IS_FIXTURE = all(source["kind"] == "FIXTURE" for source in SNAPSHOT["sources"])
    IS_ACTIVE = not IS_FIXTURE and Path(DB_PATH).resolve() == ACTIVE_DB_PATH.resolve()
    SOURCE = next(
        (source for source in SNAPSHOT["sources"] if source["kind"] == "MARKET_HISTORY"),
        SNAPSHOT["sources"][0],
    )
    ACCOUNT_SOURCE = next(
        (source for source in SNAPSHOT["sources"] if source["kind"] in {"BROKER_SNAPSHOT", "FIXTURE"}),
        SNAPSHOT["sources"][0],
    )
    ACCOUNT_EFFECTIVE = effective_source(ACCOUNT_SOURCE)
    PHASE_LABEL = (
        "FASE 1 · FIXTURE"
        if IS_FIXTURE
        else ("ACTIEF · READ-ONLY" if IS_ACTIVE else "STAGING · READ-ONLY")
    )
    SOURCE_SUMMARY = " | ".join(source_label(source) for source in SNAPSHOT["sources"])
    refresh_report = DATA_ROOT / "latest-refresh.json"
    REFRESH_STATUS = ""
    if refresh_report.is_file():
        payload = json.loads(refresh_report.read_text())
        state = str(payload.get("state") or "ONBEKEND")
        finished = str(payload.get("finished_at") or payload.get("started_at") or "onbekend")
        if state == "FAILED":
            unchanged = " · actieve bron ongewijzigd" if payload.get("active_unchanged") else ""
            REFRESH_STATUS = f"Laatste refresh: FAILED · {finished}{unchanged}."
        else:
            REFRESH_STATUS = f"Laatste refresh: {state} · {finished}."
    hardening_reports = sorted(
        (DATA_ROOT / "phase2c-runs").glob("*/acceptance-report.json")
    )
    HARDENING_REPORT = {}
    HARDENING_STATUS = ""
    if hardening_reports:
        HARDENING_REPORT = json.loads(hardening_reports[-1].read_text())
        scenario_count = len(HARDENING_REPORT.get("scenarios") or [])
        state = str(HARDENING_REPORT.get("state") or "ONBEKEND")
        unchanged = " · actief ongewijzigd" if HARDENING_REPORT.get("active_unchanged") else ""
        HARDENING_STATUS = f"Laatste hardening: {state} · {scenario_count}/{scenario_count}{unchanged}."
    data_root = DATA_ROOT
    scheduler_config_path = data_root / "scheduler-config.json"
    schedule_status_path = data_root / "latest-scheduled-refresh.json"
    SCHEDULER_CONFIG = json.loads(scheduler_config_path.read_text()) if scheduler_config_path.is_file() else {}
    SCHEDULE_STATUS = json.loads(schedule_status_path.read_text()) if schedule_status_path.is_file() else {}


refresh_runtime_state()


def metric_card(label: str, value: str, subtitle: str = "") -> html.Div:
    return html.Div(
        [html.Div(label, className="metric-label"), html.Div(value, className="metric-value"), html.Div(subtitle, className="metric-subtitle")],
        className="metric-card",
    )


def data_table(rows: list[dict[str, Any]], columns: list[tuple[str, str]]) -> dash_table.DataTable:
    return dash_table.DataTable(
        data=rows,
        columns=[{"name": label, "id": key} for label, key in columns],
        sort_action="native",
        page_action="none",
        style_as_list_view=True,
        style_table={"overflowX": "auto"},
        style_header={"backgroundColor": "#18233b", "color": "#94a3b8", "fontWeight": 600, "border": "0"},
        style_cell={
            "backgroundColor": "#0f172a",
            "color": "#e2e8f0",
            "border": "0",
            "borderBottom": "1px solid #26344f",
            "fontFamily": "Inter, system-ui, sans-serif",
            "fontSize": "13px",
            "padding": "12px",
            "textAlign": "left",
            "minWidth": "110px",
            "maxWidth": "240px",
        },
        style_data_conditional=[
            {"if": {"filter_query": '{status} contains "reconciliatie"'}, "color": "#fca5a5"},
            {"if": {"filter_query": '{status} contains "Open"'}, "color": "#bfdbfe"},
        ],
    )


def overview_layout() -> html.Div:
    account = SNAPSHOT["account"]
    positions = []
    for item in SNAPSHOT["positions"]:
        positions.append(
            {
                "type": item["asset_kind"],
                "symbool": item["underlying"],
                "aantal": item["quantity"],
                "marktprijs": money(item.get("market_price")),
                "marktwaarde": money(item.get("market_value")),
                "ongerealiseerd": money(item.get("unrealized_pnl")),
                "cyclus": item.get("cycle_id") or "—",
                "status": "Open",
            }
        )
    cycles = []
    for cycle in SNAPSHOT["cycles"]:
        cycles.append(
            {
                "cyclus": cycle["cycle_id"],
                "symbool": cycle["underlying"],
                "status": STATE_LABELS[cycle["state"]],
                "thesis": cycle["thesis_state"],
                "put pnl": money(cycle["realized_put_pnl"]),
                "call pnl": money(cycle["realized_call_pnl"]),
                "locked BE": money(cycle.get("locked_break_even")),
                "confidence": cycle["confidence"],
            }
        )
    return html.Div(
        [
            html.Div(
                [
                    metric_card("Cash", money(account["total_cash"]), f"{ACCOUNT_SOURCE['kind']} · {ACCOUNT_EFFECTIVE['effective_quality']} · leeftijd {ACCOUNT_EFFECTIVE['age']}"),
                    metric_card("Net liquidation", money(account["net_liquidation"]), "Stock + opties + cash"),
                    metric_card("Put assignment", money(account["open_put_assignment_obligation"]), "Volledige verplichting"),
                    metric_card("Cash na assignments", money(account["cash_after_all_assignments"]), "Geen buying-powermaskering"),
                    metric_card("Gerealiseerde trading-P&L", money(account["realized_trading_pnl"]), "Exclusief externe inleg"),
                    metric_card("Open MTM", money(account["unrealized_trading_pnl"]), "Niet gerealiseerd"),
                ],
                className="metric-grid",
            ),
            html.Section([html.H2("Open posities"), data_table(positions, [("Type", "type"), ("Symbool", "symbool"), ("Aantal", "aantal"), ("Marktprijs", "marktprijs"), ("Marktwaarde", "marktwaarde"), ("Open P&L", "ongerealiseerd"), ("Cyclus", "cyclus")])], className="panel"),
            html.Section([html.H2("Wheel-cycli"), html.P("Een covered call blijft onderdeel van dezelfde economische cyclus.", className="section-note"), data_table(cycles, [("Cyclus", "cyclus"), ("Symbool", "symbool"), ("Status", "status"), ("Thesis", "thesis"), ("Put P&L", "put pnl"), ("Call P&L", "call pnl"), ("Locked BE", "locked BE"), ("Kwaliteit", "confidence")])], className="panel"),
        ]
    )


def cycle_layout() -> html.Div:
    options = [{"label": f'{c["underlying"]} · {STATE_LABELS[c["state"]]}', "value": c["cycle_id"]} for c in SNAPSHOT["cycles"]]
    return html.Div(
        [
            html.Div([html.Label("Selecteer cyclus"), dcc.Dropdown(id="cycle-select", options=options, value=options[0]["value"], clearable=False)], className="selector"),
            html.Div(id="cycle-detail"),
        ]
    )


def technical_layout() -> html.Div:
    symbols = sorted({item["underlying"] for item in SNAPSHOT["technicals"]})
    warning = (
        "Candles en indicatoren zijn synthetische fixtures. Geen actuele marktdata."
        if IS_FIXTURE
        else "Afgesloten dagcandles · vertraagde historie · geen uitvoerbare live quote."
    )
    return html.Div(
        [
            html.Div([html.Label("Selecteer aandeel"), dcc.Dropdown(id="symbol-select", options=symbols, value=symbols[0], clearable=False)], className="selector"),
            html.Div(warning, className="warning-inline"),
            dcc.Graph(id="technical-chart", config={"displaylogo": False, "responsive": True}),
        ],
        className="panel",
    )


def pnl_layout() -> html.Div:
    account = SNAPSHOT["account"]
    realized = account.get("realized_trading_pnl")
    unrealized = account.get("unrealized_trading_pnl")
    total_economic = None
    if realized is not None and unrealized is not None:
        total_economic = Decimal(str(realized)) + Decimal(str(unrealized))
    realized_options = sum(
        (Decimal(str(cycle["realized_put_pnl"])) + Decimal(str(cycle["realized_call_pnl"]))
         for cycle in SNAPSHOT["cycles"]),
        Decimal("0"),
    )
    open_option_cashflow = sum(
        (Decimal(str(cycle["open_option_cashflow"])) for cycle in SNAPSHOT["cycles"]),
        Decimal("0"),
    )
    rows = []
    for cycle in SNAPSHOT["cycles"]:
        rows.append({
            "symbool": cycle["underlying"],
            "status": STATE_LABELS[cycle["state"]],
            "gerealiseerde opties": money(
                Decimal(str(cycle["realized_put_pnl"])) + Decimal(str(cycle["realized_call_pnl"]))
            ),
            "open optiecashflow": money(cycle["open_option_cashflow"]),
            "stock cashflow": money(cycle["stock_net_cashflow"]),
            "resterende aandelen": cycle["remaining_shares"],
        })
    return html.Div(
        [
            html.Div(
                [
                    metric_card("Gerealiseerde trading-P&L", money(realized), "broker history · opties + volledig gesloten aandelen + dividend"),
                    metric_card("Open MTM", money(unrealized), "broker snapshot · alleen als iedere actuele positie is gewaardeerd"),
                    metric_card("Economisch totaal", money(total_economic), "Gerealiseerd + open MTM; geen cashflowmaatstaf"),
                    metric_card("Gerealiseerde optie-P&L", money(realized_options), "Onderdeel van gerealiseerde trading-P&L"),
                    metric_card("Open optiecashflow", money(open_option_cashflow), "Conditioneel · niet als gerealiseerde winst geteld"),
                    metric_card("Externe netto-inleg", money(account.get("external_net_contributions")), "Niet als rendement geteld; — als USD niet bevestigd is"),
                ],
                className="metric-grid",
            ),
            html.Div(
                "Stock cashflow van een open assignment is kapitaalbeslag, geen gerealiseerd verlies. Alleen een volledig gesloten aandelenlot telt mee in gerealiseerde trading-P&L.",
                className="explanation",
            ),
            html.Section(
                [html.H2("P&L per Wheel-cyclus"), data_table(rows, [
                    ("Symbool", "symbool"), ("Status", "status"),
                    ("Gerealiseerde opties", "gerealiseerde opties"),
                    ("Open optiecashflow", "open optiecashflow"),
                    ("Stock cashflow", "stock cashflow"),
                    ("Resterende aandelen", "resterende aandelen"),
                ])],
                className="panel",
            ),
        ]
    )


def data_quality_layout() -> html.Div:
    severity_counts = {severity: 0 for severity in ("HARD_STOP", "WARNING", "INFO")}
    for item in SNAPSHOT["reconciliation_issues"]:
        severity_counts[item["severity"]] = severity_counts.get(item["severity"], 0) + 1
    source_rows = []
    for source in SNAPSHOT["sources"]:
        effective = effective_source(source)
        source_rows.append({
            "bron": source["kind"],
            "capture": source["quality"],
            "actueel": effective["effective_quality"],
            "cutoff": source["cutoff_at"],
            "leeftijd": effective["age"],
            "waarschuwingen": len(source.get("warnings") or []),
        })
    issue_rows = [{
        "ernst": item["severity"],
        "type": item["kind"],
        "cyclus": item.get("cycle_id") or "—",
        "uitleg": item["message"],
    } for item in SNAPSHOT["reconciliation_issues"]]
    return html.Div(
        [
            html.Div(
                [
                    metric_card("Harde stops", str(severity_counts["HARD_STOP"]), "Moet 0 zijn voor promotie"),
                    metric_card("Waarschuwingen", str(severity_counts["WARNING"]), "Zichtbaar, maar niet automatisch blokkerend"),
                    metric_card("Informatie", str(severity_counts["INFO"]), "Provenance en bronverschillen"),
                    metric_card("Actuele posities", str(len(SNAPSHOT["positions"])), "broker snapshot ↔ broker history exact gekoppeld vóór activatie"),
                    metric_card(
                        "Scheduler readiness",
                        "JA" if HARDENING_REPORT.get("scheduler_ready") else "NEE",
                        "Technisch getest",
                    ),
                    metric_card(
                        "Automatische refresh",
                        "ACTIEF" if SCHEDULER_CONFIG.get("enabled") else "UIT",
                        (
                            f"{SCHEDULER_CONFIG.get('human_schedule')} · alleen fouten"
                            if SCHEDULER_CONFIG.get("enabled") else "Geen planning actief"
                        ),
                    ),
                    metric_card(
                        "Laatste schedulerproef",
                        str(SCHEDULE_STATUS.get("state") or "—"),
                        f"Pipeline: {SCHEDULE_STATUS.get('pipeline_state') or '—'} · retries {SCHEDULE_STATUS.get('retry_count', 0)}",
                    ),
                ],
                className="metric-grid four",
            ),
            html.Section([html.H2("Bronnen en freshness"), data_table(source_rows, [
                ("Bron", "bron"), ("Captured", "capture"), ("Nu", "actueel"),
                ("Cutoff", "cutoff"), ("Leeftijd", "leeftijd"),
                ("Bronwaarschuwingen", "waarschuwingen"),
            ])], className="panel"),
            html.Section([html.H2("Reconciliatiepunten"), data_table(issue_rows, [
                ("Ernst", "ernst"), ("Type", "type"), ("Cyclus", "cyclus"), ("Uitleg", "uitleg"),
            ])], className="panel"),
            html.Div("Een captured LIVE broker snapshot kan door ouderdom CACHED of STALE worden. Capturekwaliteit en huidige versheid blijven daarom afzonderlijk zichtbaar.", className="explanation"),
        ]
    )


def methodology_layout() -> html.Div:
    return html.Div(
        [
            html.H2("Break-evenlagen"),
            html.Div(
                [
                    metric_card("Brokerbasis", "Apart", "Niet hernoemen tot economische basis"),
                    metric_card("Locked break-even", "Gerealiseerd", "Afgesloten put- en callbenen"),
                    metric_card("Cashflow", "Conditioneel", "Inclusief credit van open optie"),
                    metric_card("Liquidation", "Uitvoerbaar", "Inclusief conservatieve buybackkosten"),
                ],
                className="metric-grid four",
            ),
            html.H2("Datakwaliteit"),
            html.Ul(
                [
                    html.Li("Elke bron heeft een type, kwaliteit en exacte cutoff."),
                    html.Li("Open optiecredit is cashflow, niet automatisch gerealiseerde winst."),
                    html.Li("Externe stortingen en opnames blijven buiten trading-P&L."),
                    html.Li("Tegenstrijdige posities worden UNRESOLVED; de app raadt niet."),
                    html.Li(
                        "Deze Fase-1-build gebruikt uitsluitend gesanitiseerde fixtures."
                        if IS_FIXTURE
                        else (
                            "Deze actieve weergave combineert broker history-historie, broker snapshot-accountstaat en vertraagde marktgeschiedenis met afzonderlijke provenance."
                            if IS_ACTIVE
                            else "Deze stagingweergave combineert broker history-historie, broker snapshot-accountstaat en vertraagde marktgeschiedenis met afzonderlijke provenance."
                        )
                    ),
                ]
            ),
        ],
        className="panel prose",
    )


def build_technical_figure(symbol: str) -> go.Figure:
    bars = read_market_bars(DB_PATH, symbol)
    dates = [bar["date"] for bar in bars]
    closes = [bar["close"] for bar in bars]
    lower, middle, upper = bollinger_bands(closes)
    sma50 = sma(closes, 50)
    sma200 = sma(closes, 200)
    rsi14 = rsi(closes)

    fig = make_subplots(
        rows=3,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.035,
        row_heights=[0.63, 0.14, 0.23],
    )
    fig.add_trace(go.Candlestick(x=dates, open=[b["open"] for b in bars], high=[b["high"] for b in bars], low=[b["low"] for b in bars], close=closes, name=symbol, increasing_line_color="#22c55e", decreasing_line_color="#ef4444"), row=1, col=1)
    fig.add_trace(go.Scatter(x=dates, y=upper, name="BB upper", line={"color": "#64748b", "width": 1}), row=1, col=1)
    fig.add_trace(go.Scatter(x=dates, y=middle, name="SMA20", line={"color": "#38bdf8", "width": 1.4}), row=1, col=1)
    fig.add_trace(go.Scatter(x=dates, y=lower, name="BB lower", line={"color": "#64748b", "width": 1}, fill="tonexty", fillcolor="rgba(100,116,139,0.08)"), row=1, col=1)
    fig.add_trace(go.Scatter(x=dates, y=sma50, name="SMA50", line={"color": "#f59e0b", "width": 1.2}), row=1, col=1)
    fig.add_trace(go.Scatter(x=dates, y=sma200, name="SMA200", line={"color": "#a78bfa", "width": 1.2}), row=1, col=1)
    fig.add_trace(go.Bar(x=dates, y=[b["volume"] for b in bars], name="Volume", marker_color="#334155"), row=2, col=1)
    fig.add_trace(go.Scatter(x=dates, y=rsi14, name="RSI14", line={"color": "#22d3ee", "width": 1.6}), row=3, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color="#ef4444", row=3, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color="#22c55e", row=3, col=1)

    cycle = next((item for item in SNAPSHOT["cycles"] if item["underlying"] == symbol and item["state"] not in {"CLOSED_PUT_ONLY", "CLOSED_CALLED_AWAY", "CLOSED_STOCK_SOLD"}), None)
    if cycle:
        overlays = [
            ("Originele strike", cycle.get("original_put_strike"), "#f97316"),
            ("Brokerbasis", cycle.get("broker_basis_per_share"), "#e2e8f0"),
            ("Locked BE", cycle.get("locked_break_even"), "#22c55e"),
            ("Cashflow BE", cycle.get("cashflow_break_even") or cycle.get("cashflow_put_break_even"), "#38bdf8"),
            ("Liquidation BE", cycle.get("liquidation_break_even"), "#e879f9"),
        ]
        open_call = next((leg for leg in cycle.get("option_legs", []) if leg["side"] == "CALL" and leg["state"] == "OPEN"), None)
        if open_call:
            overlays.append(("Open callstrike", open_call["strike"], "#facc15"))
        for label, value, color in overlays:
            if value is not None:
                fig.add_hline(y=float(value), line_dash="dash", line_color=color, annotation_text=label, annotation_position="top left", row=1, col=1)

    fig.update_layout(
        title=f"{symbol} · {SOURCE['kind']} ({SOURCE['quality']}) · cutoff {SOURCE['cutoff_at']}",
        template="plotly_dark",
        paper_bgcolor="#0f172a",
        plot_bgcolor="#0b1220",
        font={"color": "#cbd5e1"},
        height=760,
        margin={"l": 55, "r": 35, "t": 65, "b": 35},
        hovermode="x unified",
        legend={"orientation": "h", "y": 1.02, "x": 0},
        xaxis_rangeslider_visible=False,
    )
    fig.update_yaxes(title_text="Prijs ($)", row=1, col=1)
    fig.update_yaxes(title_text="Volume", row=2, col=1)
    fig.update_yaxes(title_text="RSI", range=[0, 100], row=3, col=1)
    return fig


def build_root_layout() -> html.Div:
    refresh_runtime_state()
    return html.Div(
        [
            html.Header(
                [
                    html.Div([html.Div("Wheel", className="brand-mark"), html.Div([html.H1("Wheel Dashboard"), html.P("Lokale managementweergave · read-only analyse")])], className="brand"),
                    html.Div(PHASE_LABEL, className="phase-badge"),
                ],
                className="app-header",
            ),
            html.Div(
                [
                    html.Strong("Datakwaliteit: "),
                    html.Span(SOURCE_SUMMARY),
                    html.Span(
                        "Synthetische testdata — geen handelsbeslissingen."
                        if IS_FIXTURE
                        else (
                            "Actieve lokale bron — read-only analyse; orders blijven handmatig in broker."
                            if IS_ACTIVE
                            else "Stagingacceptatie — nog niet de geactiveerde standaardbron."
                        ),
                        className="source-warning",
                    ),
                    html.Span(REFRESH_STATUS, className="source-warning") if REFRESH_STATUS else None,
                    html.Span(HARDENING_STATUS, className="source-warning") if HARDENING_STATUS else None,
                ],
                className="source-strip",
            ),
            dcc.Tabs(
                id="main-tabs",
                value="overview",
                children=[
                    dcc.Tab(label="Portefeuille", value="overview", children=overview_layout()),
                    dcc.Tab(label="Wheel-cycli", value="cycles", children=cycle_layout()),
                    dcc.Tab(label="P&L & cashflow", value="pnl", children=pnl_layout()),
                    dcc.Tab(label="Techniek", value="technicals", children=technical_layout()),
                    dcc.Tab(label="Datakwaliteit", value="quality", children=data_quality_layout()),
                    dcc.Tab(label="Methodiek", value="methodology", children=methodology_layout()),
                ],
            ),
            html.Footer([html.Span("Namespace: wheel-dashboard · opslag: SQLite WAL"), html.Span("Geen broker-writepad · geen orderfuncties")], className="app-footer"),
        ],
        className="app-shell",
    )


def create_app() -> Dash:
    assets_folder = Path(__file__).resolve().parents[1] / "assets"
    app = Dash(
        __name__,
        title="Wheel Dashboard",
        suppress_callback_exceptions=True,
        assets_folder=str(assets_folder),
        update_title=None,
    )
    app.index_string = """<!DOCTYPE html><html><head>{%metas%}<title>{%title%}</title><link rel="manifest" href="/assets/manifest.webmanifest"><meta name="theme-color" content="#0b1220">{%favicon%}{%css%}</head><body>{%app_entry%}<footer>{%config%}{%scripts%}{%renderer%}</footer></body></html>"""
    app.layout = build_root_layout

    @app.server.get("/healthz")
    def healthz():
        return {"status": "ok", "namespace": "wheel-dashboard", "mode": "read-only"}, 200

    @app.callback(Output("cycle-detail", "children"), Input("cycle-select", "value"))
    def render_cycle(cycle_id: str):
        cycle = next(item for item in SNAPSHOT["cycles"] if item["cycle_id"] == cycle_id)
        if Decimal(cycle["remaining_shares"]) > 0:
            metrics = html.Div(
                [
                    metric_card("Brokerbasis", money(cycle.get("broker_basis_per_share")), "Door broker gereconcilieerd"),
                    metric_card("Locked break-even", money(cycle.get("locked_break_even")), "Alleen vastliggende optie-P&L"),
                    metric_card("Locked + dividend", money(cycle.get("locked_break_even_with_dividends")), "Dividend apart zichtbaar"),
                    metric_card("Cashflow break-even", money(cycle.get("cashflow_break_even")), "Open optiecredit; conditioneel"),
                    metric_card("Liquidation break-even", money(cycle.get("liquidation_break_even")), "Inclusief actuele close cost"),
                ],
                className="metric-grid five",
            )
        else:
            metrics = html.Div(
                [
                    metric_card("Open put cash BE", money(cycle.get("cashflow_put_break_even")), "Strike minus ontvangen netto cash"),
                    metric_card("Gerealiseerde put-P&L", money(cycle["realized_put_pnl"]), "Alleen afgesloten benen"),
                    metric_card("Open optiecashflow", money(cycle["open_option_cashflow"]), "Niet automatisch gerealiseerd"),
                ],
                className="metric-grid",
            )
        legs = [
            {"type": leg["side"], "contract": leg["occ_symbol"], "strike": money(leg["strike"]), "expiratie": leg["expiration"], "contracten": leg["contracts"], "status": leg["state"], "parent": leg.get("parent_leg_id") or "—"}
            for leg in cycle["option_legs"]
        ]
        return html.Div(
            [
                html.Div([html.Div([html.H2(f'{cycle["underlying"]} · {cycle["cycle_id"]}'), html.P(STATE_LABELS[cycle["state"]], className="cycle-state")]), html.Div([html.Span(f'Thesis: {cycle["thesis_state"]}', className="pill"), html.Span(f'Confidence: {cycle["confidence"]}', className="pill")])], className="cycle-heading"),
                metrics,
                html.Section([html.H2("Optiebenen"), data_table(legs, [("Type", "type"), ("Contract", "contract"), ("Strike", "strike"), ("Expiratie", "expiratie"), ("Aantal", "contracten"), ("Status", "status"), ("Vorige leg", "parent")])], className="panel"),
                html.Div("Let op: cashflow-break-even kan premie van een nog open contract bevatten. Liquidation break-even corrigeert daarvoor met een conservatieve close cost.", className="explanation"),
            ]
        )

    @app.callback(Output("technical-chart", "figure"), Input("symbol-select", "value"))
    def render_chart(symbol: str):
        return build_technical_figure(symbol)

    return app


app = create_app()
server = app.server

if __name__ == "__main__":
    app.run(
        debug=False,
        host=os.environ.get("WHEEL_DASHBOARD_HOST", "127.0.0.1"),
        port=int(os.environ.get("WHEEL_DASHBOARD_PORT", "8050")),
    )
