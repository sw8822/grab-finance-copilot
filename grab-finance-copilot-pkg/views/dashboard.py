"""
views/dashboard.py — Layer 1: Finance & Flux Analysis.
All numbers sourced exclusively from core.data_loader. No literals.
"""
from __future__ import annotations

import streamlit as st
import plotly.graph_objects as go
import pandas as pd

from core import data_loader as dl
from core import peer_data_loader as pdl
from core.copilot import ask
from views.copilot_view import _escape_currency_markdown

GRAB_GREEN = "#00B14F"
SEGMENT_COLORS = {
    "Deliveries": "#00B14F",
    "Mobility": "#0066CC",
    "Financial Services": "#FF6B35",
    "Others": "#9B59B6",
}

# "Explain this number" → grounded NL question routed to the same Copilot engine.
EXPLAIN_Q = {
    "revenue": "What was Grab's revenue in {y} and how did it change from {p}?",
    "adjusted_ebitda": "What was Grab's adjusted EBITDA in {y} and what drove the change from {p}?",
    "profit_for_year": "What was Grab's net profit in {y} compared with {p}?",
    "operating_profit": "What was Grab's operating profit in {y} versus {p}?",
    "adjusted_free_cash_flow": "What was Grab's adjusted free cash flow in {y}?",
    "on_demand_gmv": "What was Grab's On-Demand GMV in {y} and how did it grow?",
    "group_mtus": "How many monthly transacting users did Grab have in {y}?",
}


def _fmt(v: float, unit: str = "USD M") -> str:
    if unit == "USD M":
        if abs(v) >= 1000:
            return f"${v/1000:.1f}B"
        return f"${v:.0f}M"
    if unit == "M users":
        return f"{v:.1f}M"
    if unit == "%":
        return f"{v:.1f}%"
    return f"{v:,.1f} {unit}".strip()


def _fmt_prose(v: float) -> str:
    """Currency text safe for Streamlit Markdown (a raw dollar opens math mode)."""
    return _fmt(v).replace("$", "USD ")


def _yoy_delta(val: float, prev: float | None, unit: str = "USD M") -> str | None:
    """YoY delta for a KPI card. Uses % for clean positive series, but when a value
    is non-positive a percentage reads misleadingly (e.g. loss→profit shows +138%),
    so we show the absolute swing instead."""
    if prev is None or prev == 0:
        return None
    swing = val - prev
    if prev <= 0 or val <= 0:
        return f"{'+' if swing >= 0 else '-'}{_fmt(abs(swing), unit)} YoY"
    return f"{(val - prev) / abs(prev) * 100:+.1f}% YoY"


PEER_COLORS = {
    "Grab": GRAB_GREEN, "Uber": "#0066CC", "Lyft": "#FF6B35",
    "DoorDash": "#9B59B6", "Sea": "#16A085",
}


def _group_series(metric: str) -> dict[str, dict[str, float]]:
    """company -> {year: value} for a canonical peer group metric, pulled via the
    same deterministic, cited peer adapter the Copilot uses (no hardcoded literals)."""
    out: dict[str, dict[str, float]] = {}
    for company in pdl.COMPANIES:
        series = {}
        for y in dl.YEARS:
            raw = pdl.get_fact(company, "group", metric, y)
            if raw is not None:
                series[y] = float(raw["value"])
        if series:
            out[company] = series
    return out


def _exec_summary(gf, y, p) -> list[str]:
    """Three grounded one-liners derived entirely from the dataset — no hardcoded
    figures. Reads the reference year (y) vs prior (p); segments resolved dynamically."""
    def v(m, yr): return float(gf.loc[m, yr])
    seg_eb = dl.segment_frame("segment_adj_ebitda")
    margins = dl.segment_margins()
    raw_seg = dl._raw()["segments"]
    rev, net, eb = v("revenue", y), v("profit_for_year", y), v("adjusted_ebitda", y)
    top = seg_eb[y].astype(float).idxmax()
    bottom = seg_eb[y].astype(float).idxmin()

    if p:
        g = (rev - v("revenue", p)) / abs(v("revenue", p)) * 100
        ebp = v("adjusted_ebitda", p)
        ebg = (eb - ebp) / abs(ebp) * 100 if ebp else 0
        first = net > 0 and v("profit_for_year", p) <= 0
        l1 = (f"Revenue {_fmt_prose(rev)} ({g:+.0f}% YoY); "
              + (f"**first full year of net profit** at {_fmt_prose(net)} (vs {_fmt_prose(v('profit_for_year', p))} in {p})."
                 if first else f"net result {_fmt_prose(net)}."))
        l2 = (f"{top} led profitability — segment Adjusted EBITDA {_fmt_prose(float(seg_eb.loc[top, y]))} "
              f"at {float(margins.loc[top, y]):.1f}% margin; Group Adjusted EBITDA {_fmt_prose(eb)} ({ebg:+.0f}% YoY).")
        if "loan_portfolio" in raw_seg.get(bottom, {}):
            loan, loanp = float(raw_seg[bottom]["loan_portfolio"][y]), float(raw_seg[bottom]["loan_portfolio"][p])
            lg = (loan - loanp) / abs(loanp) * 100 if loanp else 0
            l3 = (f"{bottom} is the funded growth bet — loan book {_fmt_prose(loan)} ({lg:+.0f}% YoY), "
                  f"segment EBITDA {_fmt_prose(float(seg_eb.loc[bottom, y]))}.")
        else:
            l3 = f"{bottom} is the main drag at segment EBITDA {_fmt_prose(float(seg_eb.loc[bottom, y]))}."
    else:
        l1 = f"Revenue {_fmt_prose(rev)}; net result {_fmt_prose(net)}; Group Adjusted EBITDA {_fmt_prose(eb)}."
        l2 = (f"{top} led profitability — segment Adjusted EBITDA {_fmt_prose(float(seg_eb.loc[top, y]))} "
              f"at {float(margins.loc[top, y]):.1f}% margin.")
        l3 = f"{bottom} is the main drag at segment EBITDA {_fmt_prose(float(seg_eb.loc[bottom, y]))}."
    return [l1, l2, l3]


def render(selected_year: str) -> None:
    st.header("Finance & Flux Analysis", divider="green")
    st.caption(
        f"Data shown is **annual** ({dl.YEARS[0]}–{dl.YEARS[-1]}) from public filings. In production "
        "for Grab's internal financials I'd also offer **monthly and quarterly** cadence — finer driver "
        "and seasonality analysis for executives. The app is data-driven on periods, so finer cadence is "
        "a data change, not a re-architecture."
    )

    gf = dl.group_frame()
    years = dl.YEARS
    prev_year = years[years.index(selected_year) - 1] if years.index(selected_year) > 0 else None

    # ── 7.0 Auto-generated executive summary (grounded, no literals) ───────────
    with st.container(border=True):
        st.markdown(f"**Executive summary — {selected_year}**")
        for line in _exec_summary(gf, selected_year, prev_year):
            st.markdown(f"- {line}")
        st.caption("Auto-generated from the dataset — every figure is sourced, none hardcoded.")

    # ── 7.1 KPI cards ─────────────────────────────────────────────────────────
    st.subheader("Key Performance Indicators")
    kpi_defs = [
        ("Revenue", "revenue", "USD M"),
        ("Adjusted EBITDA", "adjusted_ebitda", "USD M"),
        ("Net Profit/(Loss)", "profit_for_year", "USD M"),
        ("Operating Profit/(Loss)", "operating_profit", "USD M"),
        ("Adj Free Cash Flow", "adjusted_free_cash_flow", "USD M"),
    ]
    opm = dl.operating_metrics_frame()
    op_kpi = [
        ("On-Demand GMV", "on_demand_gmv", "USD M"),
        ("Group MTUs", "group_mtus", "M users"),
    ]

    cols = st.columns(len(kpi_defs) + len(op_kpi))
    for i, (label, key, unit) in enumerate(kpi_defs):
        val = float(gf.loc[key, selected_year])
        prev_val = float(gf.loc[key, prev_year]) if prev_year else None
        cols[i].metric(label, _fmt(val, unit), _yoy_delta(val, prev_val, unit))
        if key in EXPLAIN_Q and cols[i].button("explain", key=f"ex_{key}",
                                               help="Ask the grounded Copilot to explain this"):
            st.session_state["explain"] = (key, label)

    for j, (label, key, unit) in enumerate(op_kpi):
        c = cols[len(kpi_defs) + j]
        val = float(opm.loc[key, selected_year])
        prev_val = float(opm.loc[key, prev_year]) if prev_year else None
        c.metric(label, _fmt(val, unit), _yoy_delta(val, prev_val, unit))
        if key in EXPLAIN_Q and c.button("explain", key=f"ex_{key}",
                                         help="Ask the grounded Copilot to explain this"):
            st.session_state["explain"] = (key, label)

    # First year net profit crosses positive — derived from the data, not pinned.
    first_profit_year = next(
        (years[i] for i in range(len(years))
         if float(gf.loc["profit_for_year", years[i]]) > 0
         and (i == 0 or float(gf.loc["profit_for_year", years[i - 1]]) <= 0)),
        None,
    )
    if selected_year == first_profit_year and prev_year:
        net_profit_v = float(gf.loc["profit_for_year", selected_year])
        operating_profit = float(gf.loc["operating_profit", selected_year])
        prior_operating_profit = float(gf.loc["operating_profit", prev_year])
        st.success(
            f"**{selected_year} inflection:** First full year of net profit ({_fmt_prose(net_profit_v)}) — "
            f"operating profit turned positive ({_fmt_prose(operating_profit)} vs "
            f"{_fmt_prose(prior_operating_profit)} in {prev_year})."
        )

    # ── 7.1b "Explain this number" → grounded Copilot answer, inline ───────────
    if "explain" in st.session_state:
        ekey, elabel = st.session_state["explain"]
        eq = EXPLAIN_Q[ekey].format(y=selected_year, p=prev_year or selected_year)
        with st.container(border=True):
            head = st.columns([9, 1])
            head[0].markdown(f"**Copilot · {elabel}** — _{eq}_")
            if head[1].button("✕", key="ex_close"):
                del st.session_state["explain"]
                st.rerun()
            cache_key = (ekey, selected_year)
            if st.session_state.get("explain_key") != cache_key:
                with st.spinner("Asking the grounded Copilot…"):
                    st.session_state["explain_resp"] = ask(eq)
                st.session_state["explain_key"] = cache_key
            resp = st.session_state["explain_resp"]
            st.markdown(_escape_currency_markdown(resp.answer))
            v = resp.verification
            if v is not None and v.ok:
                st.success(f"✓ Verified — {v.checked} figure(s) grounded in tool facts")
            elif v is not None:
                st.error(f"✗ Blocked — ungrounded {sorted(set(v.ungrounded))}; verified facts shown instead")
            elif resp.mode == "retrieval_only":
                st.caption("retrieval-only mode — deterministic facts (set VERTEX_PROJECT_ID for narrative)")
            if resp.citations:
                with st.expander("Sources"):
                    for src in resp.citations:
                        st.caption(f"• {src}")
            st.caption("Same grounded engine as the GrabFi Copilot tab — Layer 1 → Layer 2.")

    st.divider()

    # ── 7.2 Revenue & profitability trend ─────────────────────────────────────
    st.subheader("Revenue & Profitability Trend")
    rev = [float(gf.loc["revenue", y]) for y in years]
    net_profit = [float(gf.loc["profit_for_year", y]) for y in years]
    adj_ebitda = [float(gf.loc["adjusted_ebitda", y]) for y in years]

    fig_trend = go.Figure()
    fig_trend.add_trace(go.Bar(
        x=years, y=rev, name="Revenue", marker_color=GRAB_GREEN,
        text=[_fmt(v) for v in rev], textposition="outside",
    ))
    fig_trend.add_trace(go.Scatter(
        x=years, y=adj_ebitda, name="Adjusted EBITDA", mode="lines+markers",
        line=dict(color="#0066CC", width=2), yaxis="y2",
        text=[_fmt(v) for v in adj_ebitda],
    ))
    fig_trend.add_trace(go.Scatter(
        x=years, y=net_profit, name="Net Profit/(Loss)", mode="lines+markers",
        line=dict(color="#FF6B35", width=2, dash="dot"), yaxis="y2",
        text=[_fmt(v) for v in net_profit],
    ))
    # Annotate the first profitable year (derived, not pinned)
    if first_profit_year:
        _fp_idx = years.index(first_profit_year)
        fig_trend.add_annotation(
            x=first_profit_year, y=net_profit[_fp_idx], text="First net profit ✓",
            showarrow=True, arrowhead=2, ax=60, ay=-40,
            font=dict(color=GRAB_GREEN, size=12), yref="y2",
        )
    fig_trend.update_layout(
        yaxis=dict(title="Revenue (USD M)"),
        yaxis2=dict(title="Profit / EBITDA (USD M)", overlaying="y", side="right"),
        legend=dict(orientation="h", y=-0.15),
        height=420,
        plot_bgcolor="white",
    )
    st.plotly_chart(fig_trend, width="stretch")
    st.divider()

    # ── 7.2b Profitability margins & operating leverage ───────────────────────
    st.subheader("Profitability & Operating Leverage")
    col_m, col_c = st.columns(2)
    with col_m:
        st.markdown("**Margin trend (% of revenue)**")
        margin_defs = [
            ("Adj EBITDA margin", "adjusted_ebitda", "#0066CC"),
            ("Operating margin", "operating_profit", GRAB_GREEN),
            ("Net margin", "profit_for_year", "#FF6B35"),
        ]
        fig_mar = go.Figure()
        for name, key, color in margin_defs:
            yvals = [float(gf.loc[key, y]) / float(gf.loc["revenue", y]) * 100 for y in years]
            fig_mar.add_trace(go.Scatter(
                x=years, y=yvals, name=name, mode="lines+markers+text",
                line=dict(color=color, width=2),
                text=[f"{v:.1f}%" for v in yvals], textposition="top center",
            ))
        fig_mar.add_hline(y=0, line=dict(color="#999", width=1, dash="dot"))
        fig_mar.update_layout(height=350, yaxis_title="% of revenue",
                              legend=dict(orientation="h", y=-0.25), plot_bgcolor="white")
        st.plotly_chart(fig_mar, width="stretch")
    with col_c:
        st.markdown("**Cost structure (% of revenue)**")
        cost_defs = [
            ("Cost of revenue", "cost_of_revenue", "#7F8C8D"),
            ("Sales & marketing", "sales_marketing_expense", "#FF6B35"),
            ("General & admin", "general_admin_expense", "#9B59B6"),
            ("Research & dev", "research_dev_expense", "#0066CC"),
        ]
        fig_cost = go.Figure()
        for name, key, color in cost_defs:
            yvals = [abs(float(gf.loc[key, y])) / float(gf.loc["revenue", y]) * 100 for y in years]
            fig_cost.add_trace(go.Scatter(
                x=years, y=yvals, name=name, mode="lines+markers",
                line=dict(color=color, width=2),
                text=[f"{v:.0f}%" for v in yvals], textposition="top center",
            ))
        fig_cost.update_layout(height=350, yaxis_title="% of revenue",
                               legend=dict(orientation="h", y=-0.3), plot_bgcolor="white")
        st.plotly_chart(fig_cost, width="stretch")
    _infl = first_profit_year or "the latest year"
    st.caption(
        f"Margins cross zero in {_infl} — the profitability inflection. Meanwhile every major cost line "
        "falls as a share of revenue (operating leverage): that gap is what turned Grab profitable. "
        "All figures are group P&L lines from the dataset."
    )
    st.divider()

    # ── 7.2c P&L waterfall (reference year) ───────────────────────────────────
    st.subheader(f"P&L Waterfall — {selected_year}")
    gy = lambda k: float(gf.loc[k, selected_year])
    rev_v, cor = gy("revenue"), gy("cost_of_revenue")
    sm, ga, rd = gy("sales_marketing_expense"), gy("general_admin_expense"), gy("research_dev_expense")
    op, nfi, tax, net = gy("operating_profit"), gy("net_finance_income"), gy("income_tax_expense"), gy("profit_for_year")
    other_op = op - (rev_v + cor + sm + ga + rd)        # impairment + restructuring + other operating
    other_nonop = net - (op + nfi + tax)                # associates / FX / other below the line
    pl_steps = [
        ("Revenue", rev_v, "absolute"),
        ("Cost of revenue", cor, "relative"),
        ("Sales & marketing", sm, "relative"),
        ("General & admin", ga, "relative"),
        ("Research & dev", rd, "relative"),
        ("Other operating", other_op, "relative"),
        ("Operating profit", op, "total"),
        ("Net finance income", nfi, "relative"),
        ("Income tax", tax, "relative"),
        ("Other non-operating", other_nonop, "relative"),
        ("Net profit", net, "total"),
    ]
    fig_pl = go.Figure(go.Waterfall(
        x=[s[0] for s in pl_steps],
        measure=[s[2] for s in pl_steps],
        y=[s[1] for s in pl_steps],
        text=[(f"${v:,.0f}M" if m == "total"
               else f"{'+' if v >= 0 else '-'}${abs(v):,.0f}M") for _, v, m in pl_steps],
        textposition="outside",
        connector=dict(line=dict(color="#888", width=1, dash="dot")),
        increasing=dict(marker=dict(color="#33CC70")),
        decreasing=dict(marker=dict(color="#FF6B35")),
        totals=dict(marker=dict(color=GRAB_GREEN)),
    ))
    fig_pl.update_layout(
        height=430, plot_bgcolor="white", xaxis_tickangle=-25, yaxis_title="USD M",
        title=f"From revenue to net profit ({selected_year})",
    )
    st.plotly_chart(fig_pl, width="stretch")
    st.caption(
        "'Other operating' aggregates net impairment, restructuring, and other operating items; "
        "'Other non-operating' captures associates/FX/other below operating profit. Both are explicit "
        "balancing items so the bridge ties exactly to reported Operating profit and Net profit."
    )
    st.divider()

    # ── 7.3 Segment mix ───────────────────────────────────────────────────────
    st.subheader("Segment Mix")

    seg_rev = dl.segment_frame("revenue")
    seg_ebitda = dl.segment_frame("segment_adj_ebitda")
    seg_margins = dl.segment_margins()
    segments = list(seg_rev.index)

    col_a, col_b = st.columns(2)

    with col_a:
        st.markdown("**Segment Revenue (USD M)**")
        fig_rev = go.Figure()
        for seg in segments:
            fig_rev.add_trace(go.Bar(
                name=seg, x=years,
                y=[float(seg_rev.loc[seg, y]) for y in years],
                marker_color=SEGMENT_COLORS.get(seg, "#888"),
            ))
        fig_rev.update_layout(
            barmode="stack", height=340, showlegend=True,
            legend=dict(orientation="h", y=-0.2), plot_bgcolor="white",
        )
        st.plotly_chart(fig_rev, width="stretch")

    with col_b:
        st.markdown("**Segment Adjusted EBITDA (USD M)**")
        fig_ebitda = go.Figure()
        for seg in segments:
            fig_ebitda.add_trace(go.Bar(
                name=seg, x=years,
                y=[float(seg_ebitda.loc[seg, y]) for y in years],
                marker_color=SEGMENT_COLORS.get(seg, "#888"),
            ))
        fig_ebitda.update_layout(
            barmode="relative", height=340, showlegend=True,
            legend=dict(orientation="h", y=-0.2), plot_bgcolor="white",
        )
        st.plotly_chart(fig_ebitda, width="stretch")

    st.markdown("**Segment Adj EBITDA Margin (% of GMV/Revenue)**")
    fig_margin = go.Figure()
    for seg in ["Mobility", "Deliveries"]:
        fig_margin.add_trace(go.Scatter(
            x=years, y=[float(seg_margins.loc[seg, y]) for y in years],
            name=f"{seg} (% GMV)", mode="lines+markers+text",
            line=dict(color=SEGMENT_COLORS[seg], width=2),
            text=[f"{seg_margins.loc[seg, y]:.1f}%" for y in years],
            textposition="top center",
        ))
    fig_margin.add_trace(go.Scatter(
        x=years, y=[float(seg_margins.loc["Financial Services", y]) for y in years],
        name="Financial Services (% Rev)", mode="lines+markers+text",
        line=dict(color=SEGMENT_COLORS["Financial Services"], width=2, dash="dot"),
        text=[f"{seg_margins.loc['Financial Services', y]:.1f}%" for y in years],
        textposition="bottom center",
    ))
    fig_margin.update_layout(
        yaxis_title="Adj EBITDA margin %", height=320,
        legend=dict(orientation="h", y=-0.2), plot_bgcolor="white",
    )
    st.plotly_chart(fig_margin, width="stretch")
    st.divider()

    # ── 7.4 Flux / bridge waterfall ───────────────────────────────────────────
    st.subheader("Flux / Bridge Analysis")

    c1, c2 = st.columns(2)
    with c1:
        metric_choice = st.selectbox(
            "Metric", ["adjusted_ebitda", "revenue"],
            format_func=lambda x: "Adjusted EBITDA" if x == "adjusted_ebitda" else "Revenue",
        )
    with c2:
        _pairs = [f"{a} → {b}" for a, b in zip(dl.YEARS, dl.YEARS[1:])]
        pair_choice = st.selectbox("Year pair", list(reversed(_pairs)))
    start_yr, end_yr = pair_choice.replace(" ", "").split("→")

    steps = dl.flux_bridge(metric_choice, start_yr, end_yr)
    raw = dl._raw()
    group_node = raw["group"][metric_choice]

    driver_detail: dict[str, tuple[float | None, float | None]] = {}
    for step in steps[1:-1]:
        if step.label in raw["segments"]:
            field = "revenue" if metric_choice == "revenue" else "segment_adj_ebitda"
            node = raw["segments"][step.label][field]
            driver_detail[step.label] = (float(node[start_yr]), float(node[end_yr]))
        elif step.label == "Regional corp costs":
            node = raw["group"]["regional_corporate_costs"]
            driver_detail[step.label] = (float(node[start_yr]), float(node[end_yr]))

    x_labels = [s.label for s in steps]
    values = [s.value for s in steps]
    n = len(steps)

    measures = ["absolute"] + ["relative"] * (n - 2) + ["total"]
    text_labels = [f"${v:+,.0f}M" if i not in (0, n - 1) else f"${v:,.0f}M"
                   for i, v in enumerate(values)]
    hover_detail = []
    for i, step in enumerate(steps):
        if i in (0, n - 1):
            hover_detail.append(f"Reported total: USD {step.value:,.0f}M")
        else:
            start_value, end_value = driver_detail[step.label]
            hover_detail.append(
                f"{start_yr}: USD {start_value:,.0f}M<br>"
                f"{end_yr}: USD {end_value:,.0f}M<br>"
                f"Change: USD {step.value:+,.0f}M"
            )
    fig_water = go.Figure(go.Waterfall(
        x=x_labels, measure=measures, y=values,
        text=text_labels, textposition="outside",
        customdata=hover_detail,
        hovertemplate="%{x}<br>%{customdata}<extra></extra>",
        connector=dict(line=dict(color="#888", width=1, dash="dot")),
        increasing=dict(marker=dict(color="#33CC70")),
        decreasing=dict(marker=dict(color="#FF6B35")),
        totals=dict(marker=dict(color=GRAB_GREEN)),
    ))
    fig_water.update_layout(
        title=f"{pair_choice} — {metric_choice.replace('_', ' ').title()} bridge (USD M)",
        height=420, plot_bgcolor="white",
        xaxis_tickangle=-20,
    )
    st.plotly_chart(fig_water, width="stretch")

    # Audit drill-down table
    driver_rows = []
    source_text = (
        f"{dl.source_citation(start_yr, group_node)}; "
        f"{dl.source_citation(end_yr, group_node)}"
    )
    for s in steps[1:-1]:  # skip start & end totals
        start_value, end_value = driver_detail[s.label]
        driver_rows.append({
            "Driver": s.label,
            f"{start_yr} (USD M)": start_value,
            f"{end_yr} (USD M)": end_value,
            "Contribution (USD M)": f"${s.value:+,.0f}M",
            "Source": source_text,
        })
    if driver_rows:
        st.caption("Auditable driver detail:")
        st.dataframe(pd.DataFrame(driver_rows), hide_index=True, width="stretch")

    st.divider()

    # ── 7.45 Peer benchmark ────────────────────────────────────────────────────
    st.subheader("Peer Benchmark — Grab vs Listed Platforms")
    st.caption(
        "Grab vs Uber, Lyft, DoorDash, and Sea — every figure comes from each "
        "company's official FY2023–FY2025 investor-relations releases. Revenue is a "
        "reported, comparable GAAP/IFRS line; Adjusted EBITDA is each company's own "
        "non-GAAP definition, so margins are directional context, not a strict ranking."
    )

    rev_series = _group_series("revenue")
    ebitda_series = _group_series("adjusted_ebitda")
    companies = [c for c in pdl.COMPANIES if c in rev_series and c in ebitda_series]
    first = dl.YEARS[0]
    # Years present for EVERY loaded company — apples-to-apples for the scatter.
    # Peers can lag Grab, so we use the latest COMMON year, not the global latest.
    common = [y for y in dl.YEARS
              if all(y in rev_series[c] and y in ebitda_series[c] for c in companies)]
    last = common[-1] if common else dl.YEARS[-1]
    prior = common[-2] if len(common) >= 2 else None

    # Rule-of-40-style positioning: revenue growth vs Adjusted EBITDA margin (latest common FY)
    st.markdown(f"**Growth vs Profitability — {last}**")
    if prior is None:
        st.caption("Need at least two reporting years common to all loaded companies for this view.")
    else:
        fig_pos = go.Figure()
        max_rev = max(rev_series[c][last] for c in companies)
        growths = []
        for c in companies:
            growth = (rev_series[c][last] - rev_series[c][prior]) / abs(rev_series[c][prior]) * 100
            margin = ebitda_series[c][last] / rev_series[c][last] * 100
            growths.append(growth)
            is_grab = c == "Grab"
            fig_pos.add_trace(go.Scatter(
                x=[growth], y=[margin], mode="markers+text",
                marker=dict(
                    size=max(16.0, (rev_series[c][last] / max_rev) * 70),
                    color=PEER_COLORS[c], line=dict(color="#222", width=2 if is_grab else 0),
                    opacity=0.95 if is_grab else 0.6,
                ),
                text=[f"<b>{c}</b>" if is_grab else c], textposition="top center", name=c,
                customdata=[[rev_series[c][last], rev_series[c][last] - rev_series[c][prior]]],
                hovertemplate=(f"<b>{c}</b><br>Revenue growth: %{{x:.1f}}%<br>"
                               "Adj EBITDA margin: %{y:.1f}%<br>"
                               "Revenue: $%{customdata[0]:,.0f}M<extra></extra>"),
            ))
        lo, hi = min(growths + [0.0]) - 5, max(growths) + 5
        fig_pos.add_trace(go.Scatter(
            x=[lo, hi], y=[40 - lo, 40 - hi], mode="lines", name="Rule of 40",
            line=dict(color="#999", width=1, dash="dash"), hoverinfo="skip",
        ))
        fig_pos.update_layout(
            xaxis_title=f"Revenue growth % ({prior}→{last})",
            yaxis_title=f"Adjusted EBITDA margin % ({last})",
            height=440, plot_bgcolor="white", showlegend=False,
        )
        st.plotly_chart(fig_pos, width="stretch")
        st.caption(
            "Dashed line = the 'Rule of 40' (revenue growth % + margin % = 40); points above it "
            "clear the bar. Bubble size ∝ revenue scale."
        )

    col_g, col_m = st.columns(2)
    with col_g:
        st.markdown(f"**Revenue growth (indexed to 100 at {first})**")
        fig_idx = go.Figure()
        for c in companies:
            cys = [y for y in dl.YEARS if y in rev_series[c]]   # each company's own years
            base = rev_series[c][cys[0]]
            fig_idx.add_trace(go.Scatter(
                x=cys, y=[rev_series[c][y] / base * 100 for y in cys],
                name=c, mode="lines+markers",
                line=dict(color=PEER_COLORS[c], width=3 if c == "Grab" else 1.5),
            ))
        fig_idx.update_layout(
            height=340, yaxis_title=f"Revenue (indexed, {first}=100)",
            legend=dict(orientation="h", y=-0.25), plot_bgcolor="white",
        )
        st.plotly_chart(fig_idx, width="stretch")
    with col_m:
        st.markdown("**Adjusted EBITDA margin (% of revenue)**")
        fig_mar = go.Figure()
        for c in companies:
            cys = [y for y in dl.YEARS if y in rev_series[c] and y in ebitda_series[c]]
            fig_mar.add_trace(go.Scatter(
                x=cys, y=[ebitda_series[c][y] / rev_series[c][y] * 100 for y in cys],
                name=c, mode="lines+markers",
                line=dict(color=PEER_COLORS[c], width=3 if c == "Grab" else 1.5),
            ))
        fig_mar.update_layout(
            height=340, yaxis_title="Adj EBITDA margin %",
            legend=dict(orientation="h", y=-0.25), plot_bgcolor="white",
        )
        st.plotly_chart(fig_mar, width="stretch")

    with st.expander("Sources (official investor-relations releases)"):
        for c in companies:
            raw = pdl.get_fact(c, "group", "revenue", last)
            if raw:
                st.caption(f"• {raw['citation']}")

    st.divider()

    # ── 7.5 Auditable trail panel ──────────────────────────────────────────────
    st.subheader("Auditable Trail")

    checks = dl.consistency_report()
    for name, ok, detail in checks:
        icon = "✅" if ok else "❌"
        st.markdown(f"{icon} **{name}**")
        st.caption(f"   {detail}")

    st.markdown("**Peer Copilot dataset validation**")
    for company, ok, detail in pdl.validation_report():
        icon = "✅" if ok else "❌"
        st.markdown(f"{icon} **{company}**")
        st.caption(f"   {detail}")

    restatement = dl.meta()["restatements"]["deliveries_segment_adj_ebitda_fy2023"]
    st.info(
        f"**{restatement['period']} Deliveries restatement:** Originally reported "
        f"Segment Adjusted EBITDA of **{_fmt_prose(restatement['original_value'])}** was recast to "
        f"**{_fmt_prose(restatement['recast_value'])}**. {restatement['reason']} "
        f"Source: {restatement['controlling_source']} comparative release."
    )
    afcf_restatement = dl.meta()["restatements"]["adjusted_free_cash_flow_fy2024"]
    st.info(
        f"**{afcf_restatement['period']} Adjusted Free Cash Flow restatement:** "
        f"{_fmt_prose(afcf_restatement['original_value'])} was recast to "
        f"{_fmt_prose(afcf_restatement['recast_value'])}. {afcf_restatement['reason']} "
        f"Source: {afcf_restatement['controlling_source']} comparative release."
    )
