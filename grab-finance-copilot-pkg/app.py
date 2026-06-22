"""
app.py — GrabFi entry point.
Sidebar: data provenance, integrity badge, year filter, model selector.
Three tabs: Finance & Flux | GrabFi Copilot | Governance & Scale.
"""
import os

import streamlit as st
from dotenv import load_dotenv

# Load .env relative to this file so it works regardless of CWD
_HERE = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(_HERE, ".env"), override=True)


def _bootstrap_cloud_secrets() -> None:
    """On Streamlit Community Cloud there is no .env or service-account file. Pull
    Vertex config from st.secrets and materialise the SA JSON to a temp file so the
    standard ADC path (GOOGLE_APPLICATION_CREDENTIALS) works unchanged. Local runs
    with a .env are unaffected (env already set, or no secrets.toml -> no-op)."""
    try:
        for key in ("VERTEX_PROJECT_ID", "VERTEX_LOCATION", "COPILOT_MODEL"):
            if key in st.secrets and not os.environ.get(key):
                os.environ[key] = str(st.secrets[key])
        cred_path = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS", "")
        if (not cred_path or not os.path.exists(cred_path)) and "gcp_service_account" in st.secrets:
            import json
            import tempfile
            sa = dict(st.secrets["gcp_service_account"])
            fd, path = tempfile.mkstemp(suffix=".json")
            with os.fdopen(fd, "w") as handle:
                json.dump(sa, handle)
            os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = path
            os.environ.setdefault("VERTEX_PROJECT_ID", sa.get("project_id", ""))
    except Exception:
        pass  # no secrets configured (e.g. local without secrets.toml) -> rely on .env


_bootstrap_cloud_secrets()

from core import data_loader as dl
from core import peer_data_loader as pdl
from views import dashboard, copilot_view, governance

st.set_page_config(
    page_title="GrabFi",
    page_icon="📊",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Sidebar ────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("## 📊 GrabFi")
    st.caption("Grab + 4 listed peers · FY2023–FY2025")
    st.divider()

    # Integrity badge
    checks = dl.consistency_report()
    peer_checks = pdl.validation_report()
    n_pass = sum(1 for _, ok, _ in checks if ok)
    peer_pass = sum(1 for _, ok, _ in peer_checks if ok)
    total_pass = n_pass + peer_pass
    total_checks = len(checks) + len(peer_checks)
    badge_color = "green" if total_pass == total_checks else "red"
    st.markdown(
        f"**Auditable Trail** &nbsp; "
        f":{badge_color}[{total_pass}/{total_checks} validation checks passed]"
    )
    with st.expander("View checks"):
        st.caption("Grab financial tie-outs")
        for name, ok, detail in checks:
            icon = "✅" if ok else "❌"
            st.caption(f"{icon} {name} — {detail}")
        st.caption("Peer dataset validation")
        for company, ok, detail in peer_checks:
            icon = "✅" if ok else "❌"
            st.caption(f"{icon} {company} — {detail}")

    st.divider()

    # Year filter
    year_options = dl.YEARS
    selected_year = st.selectbox(
        "Reference year (KPIs & metrics)",
        year_options,
        index=len(year_options) - 1,
    )

    # Model selector
    configured_model = os.environ.get("COPILOT_MODEL", "gemini-3.5-flash")
    model_options = {f"{configured_model} (configured)": configured_model}
    if configured_model != "gemini-2.5-flash":
        model_options["gemini-2.5-flash (fallback)"] = "gemini-2.5-flash"
    selected_model_label = st.selectbox("Copilot model", list(model_options.keys()))
    selected_model = model_options[selected_model_label]

    # _bootstrap_cloud_secrets() already copies VERTEX_PROJECT_ID from st.secrets into
    # the environment, so reading the env var is sufficient — and avoids the
    # StreamlitSecretNotFoundError that st.secrets raises when no secrets.toml exists
    # (the offline / retrieval-only path on a clean clone).
    vertex_project = os.environ.get("VERTEX_PROJECT_ID", "")
    if vertex_project:
        st.success("agent mode active")
    else:
        st.warning("No VERTEX_PROJECT_ID — running in retrieval-only mode")

    st.divider()

    # Data provenance
    st.markdown("**Data provenance**")
    m = dl.meta()
    with st.expander("Grab sources"):
        for yr, src in m["sources"].items():
            st.markdown(f"**{yr}** — [{src['form']}, filed {src['filed']}]({src['url']})")
    with st.expander("Peer official IR sources"):
        for company in pdl.PEER_FILES:
            peer = pdl.load_peer(company)
            st.markdown(f"**{company}**")
            for yr, src in peer["sources"].items():
                st.markdown(f"- [{yr}: released {src['released']}]({src['url']})")
    st.caption(f"Currency: {m['currency']}")
    for note in m.get("notes", []):
        st.caption(note.replace("$", r"\$"))

# ── Tabs ───────────────────────────────────────────────────────────────────────
tab1, tab2, tab3 = st.tabs([
    "📊 Finance & Flux",
    "🤖 GrabFi Copilot",
    "🛡️ Governance & Scale",
])

with tab1:
    dashboard.render(selected_year)

with tab2:
    copilot_view.render(selected_model)

with tab3:
    governance.render()
