import streamlit as st
import pandas as pd
import requests
from bs4 import BeautifulSoup
import re
from io import BytesIO
import matplotlib.pyplot as plt
import urllib.parse

# ----------------------------------------------------
# 1. Constants & Helpers
# ----------------------------------------------------

DUCK_URL = "https://duckduckgo.com/html/"

DEMO_DATA = pd.DataFrame({
    "question": ["Diet type", "Alcohol consumption", "Smoking status", "Pet ownership"],
    "response": ["Omnivore", "Never", "Former", "Dog"],
    "biosample_id": ["AGP123456"] * 4,
    "accession_id": ["ACC-98765"] * 4,
    "source_url": ["demo://agp/demo1"] * 4
})


def sanitize_sheet_name(name):
    name = re.sub(r'[:\\/?*\[\]]', '-', str(name))
    name = name.strip()[:31]
    return name if name else "sheet"


def ddg_search(topic, limit=20):
    query = f"site:microbio.me americangut survey \"{topic}\""
    params = {"q": query}

    r = requests.get(DUCK_URL, params=params, headers={"User-Agent": "AGP Python App"})
    soup = BeautifulSoup(r.text, "html.parser")

    results = []
    for a in soup.select(".result__a")[:limit]:
        results.append({
            "title": a.get_text(strip=True),
            "url": a.get("href")
        })
    return pd.DataFrame(results)


def fetch_page(url):
    r = requests.get(url, headers={"User-Agent": "AGP Python App"})
    r.raise_for_status()
    return BeautifulSoup(r.text, "html.parser")


def parse_survey_page(soup, url):

    text = soup.get_text(" ", strip=True)

    biosample = re.search(r"Biosample[:\t ]+([A-Za-z0-9_-]+)", text, re.I)
    accession = re.search(r"Accession[:\t ]+([A-Za-z0-9._-]+)", text, re.I)
    sample_id = re.search(r"(Sample ID|Barcode)[:\t ]+([A-Za-z0-9._-]+)", text, re.I)

    biosample_id = (biosample.group(1) if biosample else
                    sample_id.group(2) if sample_id else None)
    accession_id = accession.group(1) if accession else None

    # Find largest table
    tables = soup.find_all("table")
    survey = None

    if tables:
        best = max(tables, key=lambda t: len(t.find_all("tr")))
        rows = []
        for tr in best.find_all("tr"):
            cols = [c.get_text(strip=True) for c in tr.find_all(["td", "th"])]
            if cols:
                rows.append(cols)
        df = pd.DataFrame(rows)
        if df.shape[1] >= 2:
            df = df.iloc[:, :2]
            df.columns = ["question", "response"]
            survey = df

    # Fallback if no table
    if survey is None:
        items = []
        for tag in soup.find_all(["p", "li"]):
            if ":" in tag.get_text():
                q, r = tag.get_text().split(":", 1)
                items.append([q.strip(), r.strip()])

        survey = pd.DataFrame(items, columns=["question", "response"]) if items else \
            pd.DataFrame(columns=["question", "response"])

    survey["biosample_id"] = biosample_id
    survey["accession_id"] = accession_id
    survey["source_url"] = url

    return survey


def scrape_topic(topic, max_pages=15):
    pages = ddg_search(topic, limit=max_pages)

    if pages.empty:
        return DEMO_DATA.copy()

    rows = []
    for _, row in pages.iterrows():
        try:
            soup = fetch_page(row["url"])
            parsed = parse_survey_page(soup, row["url"])
            rows.append(parsed)
        except Exception:
            pass

    if rows:
        return pd.concat(rows, ignore_index=True)
    else:
        return DEMO_DATA.copy()


def filter_responses(df, contains, n):
    contains = contains.lower()
    keep = df[df["response"].str.lower().str.contains(contains, na=False)]

    keep["id"] = keep["biosample_id"].fillna("") + "_" + \
                 keep["accession_id"].fillna("") + "_" + \
                 keep["source_url"].fillna("")

    ids = keep["id"].unique()[:n]
    return keep[keep["id"].isin(ids)]


def export_excel(df):
    import xlsxwriter

    output = BytesIO()
    wb = xlsxwriter.Workbook(output, {"in_memory": True})

    for ident, group in df.groupby("id"):
        sheet = sanitize_sheet_name(ident)
        ws = wb.add_worksheet(sheet)
        ws.write_row(0, 0, ["question", "response", "biosample_id", "accession_id", "source_url"])
        for i, row in group.iterrows():
            ws.write_row(i + 1, 0, row.tolist())

    wb.close()
    output.seek(0)
    return output


# ----------------------------------------------------
# 2. Streamlit UI
# ----------------------------------------------------

st.title("🧬 American Gut Survey Scraper — Python Version")
st.write("Educational use only. This replicates the behavior of a Shiny app.")

st.sidebar.header("Search Settings")
topic = st.sidebar.text_input("Topic keyword", "diet")
max_pages = st.sidebar.slider("Max pages to scrape", 5, 50, 20, 5)
demo_mode = st.sidebar.checkbox("Use Demo Mode (no internet)", value=True)

st.sidebar.header("Filter Individuals")
resp_filter = st.sidebar.text_input("Response contains", "omnivore")
n_indiv = st.sidebar.slider("Number of individuals", 1, 50, 10)

run = st.sidebar.button("Scrape & Filter")

st.sidebar.header("Download Format")
fmt = st.sidebar.selectbox("Format", ["xlsx", "csv", "txt"])

# ----------------------------------------------------
# 3. Run scraper
# ----------------------------------------------------

if run:
    if demo_mode:
        st.success("Loaded demo data!")
        df_raw = DEMO_DATA.copy()
    else:
        with st.spinner("Scraping..."):
            df_raw = scrape_topic(topic, max_pages=max_pages)

    st.subheader("Summary")
    st.write(f"Pages parsed: **{df_raw['source_url'].nunique()}**")
    st.write(f"Rows: **{len(df_raw)}**")
    st.write(f"Individuals detected: **{df_raw['biosample_id'].nunique()}**")

    # Filter
    df = filter_responses(df_raw.copy(), resp_filter, n_indiv)

    st.subheader("Filtered Results")
    st.dataframe(df)

    # ------------------------------------------------
    # Plot
    # ------------------------------------------------
    st.subheader("Response Distribution")

    if not df.empty:
        qnames = df["question"].unique()
        selected_q = st.selectbox("Select question", qnames)

        plot_df = df[df["question"].str.contains(selected_q, case=False)]
        plot_df = plot_df["response"].value_counts()

        fig, ax = plt.subplots(figsize=(7,4))
        plot_df.plot(kind="bar", ax=ax)
        ax.set_title(f"Responses for: {selected_q}")
        st.pyplot(fig)

    # ------------------------------------------------
    # Download handler
    # ------------------------------------------------
    st.subheader("Download Data")

    if fmt == "xlsx":
        file_data = export_excel(df)
        st.download_button("Download Excel", file_data, file_name="agp.xlsx")
    elif fmt == "csv":
        st.download_button("Download CSV", df.to_csv(index=False), file_name="agp.csv")
    else:
        st.download_button("Download TXT", df.to_csv(index=False, sep="\t"), file_name="agp.txt")
