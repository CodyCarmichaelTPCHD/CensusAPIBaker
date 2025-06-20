# app.py  –  Pierce County ACS quick-pull with optional demographic break-downs
# ===========================================================================

import streamlit as st
import pandas as pd
import requests, textwrap, json, re
from functools import lru_cache, cache
from typing import List

# ── SETTINGS ──────────────────────────────────────────────────────────
API_KEY = "f6ba77c8a37e5c068c2d7a0020f3b56899318771"
YEAR    = 2023
STATE   = "53"         # Washington
PIERCE  = "053"        # Pierce County

# Core indicators — keep your originals
BASE_VARS = {
    "HS diploma 25+ (%)"            : ("DP02_0062PE", "profile"),
    "Disability (%)"                : ("S1810_C02_001E", "subject"),
    "Speak English < very well (%)" : ("DP02_0110PE", "profile"),
    "Poverty <100% FPL (%)"         : ("S1701_C03_001E", "subject"),
    "Median household income ($)"   : ("S1901_C01_012E", "subject"),
    "Housing cost ≥30% income (%)"  : ("DP04_0138PE", "profile"),
    "Unemployment rate 16+ (%)"     : ("S2301_C04_001E", "subject"),
    "Households with no vehicle (%)": ("DP04_0058PE", "profile"),
    "Insurance coverage (%)"        : ("S2701_C03_001E", "subject"),
}

# Indicators that have detailed rows in their subject table
#   indicator      : (group_id, numerator‐label fragment, universe keyword)
DETAILED = {
    "Disability (%)"        : ("S1810", "With a disability", "civilian"),
    "Poverty <100% FPL (%)" : ("S1701", "Below poverty level", "population"),
    "Unemployment rate 16+ (%)": ("S2301", "Unemployment rate", "population"),
    "Insurance coverage (%)"   : ("S2701", "No health insurance coverage", "civilian"),
}

URL_HITS: List[str] = []

# ── GEO CLAUSE ─────────────────────────────────────────────────────────
def geo_clause(level: str) -> str | None:
    if level == "County (Pierce)":
        return f"for=county:{PIERCE}&in=state:{STATE}"
    if level == "Tract (Pierce)":
        return f"for=tract:*&in=state:{STATE}%20county:{PIERCE}"
    zips = st.session_state.get("zcta_list", "").replace(" ", "")
    return f"for=zip%20code%20tabulation%20area:{zips}" if zips else None

# ── FETCH WITH SIMPLE CACHE ────────────────────────────────────────────
@lru_cache(maxsize=256)
def fetch(url: str) -> pd.DataFrame:
    r = requests.get(url, timeout=20)
    r.raise_for_status()
    j = r.json()
    df = pd.DataFrame(j[1:], columns=j[0])
    return df.loc[:, ~df.columns.duplicated()]

# ── SUBJECT-TABLE METADATA (cached once per group) ────────────────────
@cache
def get_labels(group_id: str) -> dict[str, str]:
    meta_url = f"https://api.census.gov/data/{YEAR}/acs/acs5/subject/groups/{group_id}.json"
    variables = requests.get(meta_url).json()["variables"]
    return {k: v["label"] for k, v in variables.items()}

# ── BUILD VARIABLE LIST GIVEN USER BREAK-DOWN CHOICES ─────────────────
def select_vars(group_id: str, measure_fragment: str,
                want_age: bool, want_sex: bool, want_race: bool) -> list[str]:
    labels = get_labels(group_id)
    out = []
    for var, lab in labels.items():
        if not var.endswith("E"):          # estimates only
            continue
        if measure_fragment not in lab:
            continue

        keep = False
        if     want_age  and "!!AGE!!"   in lab: keep = True
        if not keep and want_sex  and "!!SEX!!"   in lab: keep = True
        if not keep and want_race and ("!!RACE"   in lab or "!!ETHNICITY" in lab): keep = True
        if keep:
            out.append(var)

    # always return at least the first column (overall)
    return out or [f"{group_id}_001E"]

# ── PULL A DETAILED TABLE ─────────────────────────────────────────────
def pull_detailed(indicator: str, geo: str,
                  want_age: bool, want_sex: bool, want_race: bool) -> pd.DataFrame:
    group_id, frag, _ = DETAILED[indicator]
    vars_wanted = select_vars(group_id, frag, want_age, want_sex, want_race)
    url = (f"https://api.census.gov/data/{YEAR}/acs/acs5/subject"
           f"?get=NAME,{','.join(vars_wanted)}&{geo}&key={API_KEY}")
    URL_HITS.append(url)
    df = fetch(url)

    # human-friendly names
    labels = get_labels(group_id)
    rename = {v: re.sub(r"Estimate!!|\w+!!", "", labels[v]).strip()
              for v in vars_wanted}
    rename = {k: f"{indicator.split('(')[0].strip()} – {v}" for k, v in rename.items()}
    return df.rename(columns=rename)

# ── SIMPLE ONE-SHOT PULL FOR NON-DETAILED VARIABLES ───────────────────
def pull_simple(code: str, dataset: str, geo: str) -> pd.DataFrame:
    base = f"https://api.census.gov/data/{YEAR}/acs/acs5/{dataset}"
    url  = f"{base}?get=NAME,{code}&{geo}&key={API_KEY}"
    URL_HITS.append(url)
    return fetch(url)

# ── SIDEBAR UI ────────────────────────────────────────────────────────
st.title("Pierce County ACS Quick-Pull")

geo_level = st.sidebar.selectbox("Geography level",
                                 ["County (Pierce)", "Tract (Pierce)", "ZCTA (custom list)"])
if geo_level.startswith("ZCTA"):
    st.sidebar.text_input("Comma-separated ZCTA codes", key="zcta_list")

default_pick = ["HS diploma 25+ (%)", "Median household income ($)",
                "Unemployment rate 16+ (%)"]
chosen = st.sidebar.multiselect("Indicators", list(BASE_VARS), default=default_pick)

with st.sidebar.expander("Break down by … (adds extra columns)"):
    want_age  = st.checkbox("Age")
    want_sex  = st.checkbox("Sex")
    want_race = st.checkbox("Race / Ethnicity")

# ── RUN BUTTON ────────────────────────────────────────────────────────
if st.sidebar.button("Run"):
    URL_HITS.clear()
    geo = geo_clause(geo_level)
    if not geo:
        st.error("Enter at least one ZCTA.")
        st.stop()

    frames = []
    for ind in chosen:
        if ind in DETAILED and (want_age or want_sex or want_race):
            frames.append(pull_detailed(ind, geo, want_age, want_sex, want_race))
        else:
            code, ds = BASE_VARS[ind]
            frames.append(pull_simple(code, ds, geo))

    df = (pd.concat(frames, axis=1)
            .loc[:, ~pd.concat(frames, axis=1).columns.duplicated()])

    # clean up the base columns’ names
    rename_base = {code: f"{lab} ({code})"
                   for lab, (code, _) in BASE_VARS.items()
                   if code in df.columns}
    df = df.rename(columns=rename_base)

    # ── DISPLAY ───────────────────────────────────────
    st.subheader("Results")
    st.dataframe(df, use_container_width=True)

    st.download_button("Download CSV",
                       df.to_csv(index=False).encode(),
                       file_name="acs_pull.csv", mime="text/csv")

    st.subheader("Exact API URLs hit")
    st.code("\n".join(URL_HITS), language="bash")

    # helper snippets
    py_urls = ", ".join(repr(u) for u in URL_HITS)
    r_urls  = ", ".join(json.dumps(u) for u in URL_HITS)

    py_code = textwrap.dedent(f"""
        import requests, pandas as pd
        urls = [{py_urls}]
        dfs  = [pd.DataFrame(r.json()[1:], columns=r.json()[0])
                for u in urls
                for r in [requests.get(u)]]
        out = pd.concat(dfs, axis=1)\
                 .loc[:, ~pd.concat(dfs, axis=1).columns.duplicated()]
    """).strip()

    r_code = textwrap.dedent(f"""
        library(httr); library(jsonlite); library(dplyr); library(purrr)
        urls <- c({r_urls})
        dfs  <- map(urls, function(u) {{
            res <- content(GET(u), as = "text")
            j   <- fromJSON(res)
            names(j) <- j[1, ]
            as_tibble(j[-1, ])
        }})
        out <- reduce(dfs, full_join, by = "NAME")
    """).strip()

    st.subheader("Copy-and-paste helper code")
    st.markdown("**Python**")
    st.code(py_code, language="python")
    st.markdown("**R**")
    st.code(r_code, language="r")
