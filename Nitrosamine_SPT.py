import streamlit as st
import pandas as pd
import requests
import pdfplumber
import io
import re
import warnings
from bs4 import BeautifulSoup
import urllib3

# 忽略 SSL 警告
warnings.filterwarnings("ignore")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 頁面設定 ---
st.set_page_config(page_title="ScinoPharm Nitrosamine Monitor (Fixed)",
                   layout="wide")
st.title("🧪 ScinoPharm Nitrosamine Monitor")
st.markdown("此工具用於解析神隆藥品清單 PDF，並提取 API 名稱與相關資訊。")

# ==========================================
# 0. 定義通用字與雜訊 (Stop Words)
# ==========================================
STOP_WORDS = {
    "ACID", "SODIUM", "POTASSIUM", "CALCIUM", "MAGNESIUM", "HYDROCHLORIDE",
    "HCL", "HYDROBROMIDE", "HBR", "ACETATE", "TARTRATE", "CITRATE", "MALEATE",
    "FUMARATE", "MESYLATE", "SUCCINATE", "PHOSPHATE", "SULFATE", "BASE", "USP",
    "EP", "BP", "JP", "TABLETS", "CAPSULES", "INJECTION", "SOLUTION", "ORAL",
    "EXTENDED", "RELEASE", "API", "NAME", "PRODUCT", "DRUG", "SUBSTANCE",
    "UNKNOWN", "AND", "WITH"
}


# ==========================================
# 1. 核心函數: 神隆 PDF 解析
# ==========================================
@st.cache_data(ttl=3600)
def get_scinopharm_apis():
    base_url = "https://www.scinopharm.com"
    target_url = "https://www.scinopharm.com/tw/products-detail/commercialAPI/"

    REAL_HEADERS = {
        "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept":
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Referer": "https://www.scinopharm.com/"
    }

    product_list = set()
    debug_logs = []

    try:
        r = requests.get(target_url,
                         headers=REAL_HEADERS,
                         verify=False,
                         timeout=15)
        soup = BeautifulSoup(r.text, 'html.parser')

        target_keywords = ["下載產品清單", "下載藥物主檔申請列表"]
        pdf_links = []

        for a in soup.find_all('a', href=True):
            if any(k in a.get_text(strip=True) for k in target_keywords):
                full_link = a['href']
                if not full_link.startswith("http"):
                    full_link = base_url + full_link if full_link.startswith(
                        "/") else base_url + "/" + full_link
                pdf_links.append(full_link)

        if not pdf_links:
            pdf_links.append("https://www.scinopharm.com/tw/download/43/")
            debug_logs.append("⚠️ 未在頁面找到連結，使用預設 ID 43 進行嘗試。")

        for link in pdf_links:
            debug_logs.append(f"處理連結: {link}")
            try:
                pdf_resp = requests.get(link,
                                        headers=REAL_HEADERS,
                                        verify=False,
                                        timeout=15)
                pdf_resp.raise_for_status()

                if not pdf_resp.content.startswith(b'%PDF-'):
                    debug_logs.append(f"❌ 略過: 下載內容不是 PDF (可能是 HTML 錯誤頁面)。")
                    continue

                debug_logs.append("✅ 格式驗證成功，開始解析...")

                with pdfplumber.open(io.BytesIO(pdf_resp.content)) as pdf:
                    for page in pdf.pages:
                        tables = page.extract_tables()
                        found_in_table = False
                        if tables:
                            for table in tables:
                                for row in table:
                                    if row and len(row) > 0:
                                        val = str(row[0]).strip()
                                        if is_valid_api_name(val):
                                            product_list.add(
                                                clean_api_name(val))
                                            found_in_table = True

                        if not found_in_table:
                            text = page.extract_text()
                            if text:
                                lines = text.split('\n')
                                for line in lines:
                                    parts = re.split(r'\s{2,}', line.strip())
                                    if parts:
                                        candidate = parts[0]
                                        if is_valid_api_name(candidate):
                                            product_list.add(
                                                clean_api_name(candidate))

            except requests.exceptions.RequestException as e:
                debug_logs.append(f"❌ 網路請求失敗: {e}")
            except Exception as e:
                debug_logs.append(f"❌ 解析過程錯誤: {e}")

    except Exception as e:
        debug_logs.append(f"❌ 初始連線失敗: {e}")

    return sorted(list(product_list)), debug_logs


def is_valid_api_name(text):
    if not text: return False
    text = text.lower()
    ignore = [
        "api name", "regulatory", "therapeutic", "page", "scinopharm",
        "download", "date", "status", "product"
    ]
    if any(x in text for x in ignore): return False
    if len(text) < 3: return False
    if not re.search(r'[a-zA-Z]', text): return False
    return True


def clean_api_name(text):
    text = re.sub(r'\s*\(.*?\)', '', text)
    text = text.replace('®', '').replace('™', '').replace('*', '')
    return text.strip()


# ==========================================
# 2. 爬蟲函數: USFDA & EMA
# ==========================================
@st.cache_data(ttl=86400)
def get_fda_data():
    url = "https://www.fda.gov/regulatory-information/search-fda-guidance-documents/cder-nitrosamine-impurity-acceptable-intake-limits"
    try:
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url, headers=headers, verify=False)
        dfs = pd.read_html(io.StringIO(r.text))

        # 合併所有表格 (v5.8 邏輯保持)
        valid_dfs = []
        for df in dfs:
            df.columns = [
                str(c).strip().replace('\n', ' ') for c in df.columns
            ]
            headers_str = " ".join([c.lower() for c in df.columns])
            if "nitrosamine" in headers_str or "limit" in headers_str or "ai" in headers_str:
                valid_dfs.append(df)

        if valid_dfs:
            final_df = pd.concat(valid_dfs, ignore_index=True)
            return final_df

        return pd.DataFrame()
    except Exception as e:
        return pd.DataFrame()


@st.cache_data(ttl=86400)
def get_ema_data():
    base_url = "https://www.ema.europa.eu"
    page_url = "https://www.ema.europa.eu/en/human-regulatory-overview/post-authorisation/pharmacovigilance-post-authorisation/referral-procedures-human-medicines/nitrosamine-impurities/nitrosamine-impurities-guidance-marketing-authorisation-holders"

    log_messages = []

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        r = requests.get(page_url, headers=headers, verify=False)
        soup = BeautifulSoup(r.text, 'html.parser')

        target_link = None
        for a in soup.find_all('a', href=True):
            href = a['href']
            text = a.get_text(strip=True).lower()
            if "xlsx" in href and ("appendix" in text or "limit" in text):
                target_link = href
                break

        if not target_link:
            for a in soup.find_all('a', href=True):
                if "xlsx" in a['href']:
                    target_link = a['href']
                    break

        if target_link:
            if not target_link.startswith("http"):
                target_link = base_url + target_link

            file_resp = requests.get(target_link,
                                     headers=headers,
                                     verify=False)

            temp_df = pd.read_excel(io.BytesIO(file_resp.content),
                                    header=None,
                                    nrows=30)

            best_idx = 0
            max_score = 0
            keywords = [
                "nitrosamine", "limit", "intake", "substance", "ng/day",
                "iupac", "impurity", "structure", "cas", "source",
                "ai (ng/day)"
            ]

            for idx, row in temp_df.iterrows():
                row_text = " ".join(
                    [str(x).lower() for x in row if pd.notna(x)])
                score = sum(1 for k in keywords if k in row_text)

                if score > max_score:
                    max_score = score
                    best_idx = idx

            log_messages.append(
                f"Header Scoring: Selected Row {best_idx} with score {max_score}"
            )

            df = pd.read_excel(io.BytesIO(file_resp.content), header=best_idx)
            df.columns = [
                str(c).strip().replace('\n', ' ') for c in df.columns
            ]

            return df, log_messages
        return pd.DataFrame(), ["No link found"]
    except Exception as e:
        return pd.DataFrame(), [str(e)]


# ==========================================
# 3. 核心比對邏輯 (Smart Match)
# ==========================================
def smart_match(scino_api, row_series):
    scino_clean = scino_api.upper().replace("-", " ").strip()
    scino_tokens = set(scino_clean.split())
    core_tokens = {
        t
        for t in scino_tokens if t not in STOP_WORDS and len(t) > 2
    }

    if not core_tokens:
        core_tokens = {scino_clean}

    row_text = " ".join(
        [str(val).upper() for val in row_series.values if pd.notna(val)])

    for token in core_tokens:
        if token in row_text:
            return True, row_text

    return False, ""


def get_display_col(df_columns, keyword_list):
    if isinstance(keyword_list, str):
        keyword_list = [keyword_list]

    cols = {c.lower(): c for c in df_columns}

    for kw in keyword_list:
        kw = kw.lower()
        if kw == 'name':
            for c_lower, c_orig in cols.items():
                if c_lower == 'name':
                    return c_orig

        for c_lower, c_orig in cols.items():
            if kw in c_lower:
                return c_orig
    return None


# ==========================================
# 4. Excel 生成
# ==========================================
def generate_excel(match_df, fda_raw, ema_raw):
    output = io.BytesIO()
    with pd.ExcelWriter(output, engine='xlsxwriter') as writer:
        match_df.to_excel(writer, sheet_name='Summary_Match', index=False)
        fda_raw.to_excel(writer, sheet_name='Raw_FDA_Data', index=False)
        ema_raw.to_excel(writer, sheet_name='Raw_EMA_Data', index=False)

        workbook = writer.book
        for sheet in writer.sheets.values():
            sheet.set_column(0, 8, 20)

    return output.getvalue()


# ==========================================
# 主程式 UI
# ==========================================
if st.button("🚀 執行最大化比對 ", type="primary"):

    status_box = st.status("正在處理中...", expanded=True)

    # 1. ScinoPharm
    status_box.write("📥 下載神隆 PDF...")
    scino_apis, scino_logs = get_scinopharm_apis()

    if len(scino_apis) > 0:
        status_box.write(f"✅ 神隆 API: {len(scino_apis)} 筆")

    # 2. FDA / EMA
    status_box.write("🌍 下載 FDA / EMA 資料庫...")
    fda_df = get_fda_data()
    ema_df, ema_logs = get_ema_data()
    status_box.write(f"✅ FDA: {len(fda_df)} 筆, EMA: {len(ema_df)} 筆")

    # 3. 比對
    status_box.write("🔍 執行比對...")
    match_results = []

    # --- FDA 比對 ---
    if not fda_df.empty:
        nitro_col = get_display_col(fda_df.columns, 'nitrosamine')
        limit_col = get_display_col(fda_df.columns,
                                    ['limit', 'intake', 'ng/day'])
        iupac_col = get_display_col(fda_df.columns, ['iupac', 'chemical name'])
        source_col = get_display_col(fda_df.columns, 'source')
        drug_col = get_display_col(fda_df.columns, 'drug')

        # 【修正】只抓 Notes，移除 Surrogate
        note_col = get_display_col(fda_df.columns,
                                   ['note', 'comment', 'remark'])

        ref_col = source_col if source_col else drug_col

        for _, row in fda_df.iterrows():
            for my_api in scino_apis:
                is_match, _ = smart_match(my_api, row)
                if is_match:
                    match_results.append({
                        "Source":
                        "USFDA",
                        "ScinoPharm Product":
                        my_api,
                        "Nitrosamine Impurity":
                        row[nitro_col] if nitro_col else "Check Row",
                        "IUPAC Name":
                        row[iupac_col] if iupac_col else "N/A",
                        "Limit (AI)":
                        row[limit_col] if limit_col else "N/A",
                        "Notes":
                        row[note_col] if note_col else "N/A",  # 只顯示 Notes
                        "Matched in Column":
                        ref_col if ref_col else "Full Row Match",
                        "Reference Value":
                        row[ref_col] if ref_col else "See Raw Data"
                    })

    # --- EMA 比對 ---
    if not ema_df.empty:
        nitro_col = get_display_col(ema_df.columns,
                                    ['name', 'nitrosamine', 'impurity'])
        limit_col = get_display_col(ema_df.columns,
                                    ['ai (ng/day)', 'limit', 'intake', 'ai'])
        iupac_col = get_display_col(ema_df.columns, ['iupac', 'chemical name'])
        source_col = get_display_col(ema_df.columns, 'source')
        drug_col = get_display_col(ema_df.columns,
                                   ['substance', 'api', 'product', 'active'])

        # 【修正】只抓 Notes，移除 Surrogate
        note_col = get_display_col(ema_df.columns,
                                   ['note', 'comment', 'remark'])

        ref_col = source_col if source_col else drug_col

        for _, row in ema_df.iterrows():
            for my_api in scino_apis:
                is_match, _ = smart_match(my_api, row)
                if is_match:
                    match_results.append({
                        "Source":
                        "EMA",
                        "ScinoPharm Product":
                        my_api,
                        "Nitrosamine Impurity":
                        row[nitro_col] if nitro_col
                        and pd.notna(row[nitro_col]) else "Check Row",
                        "IUPAC Name":
                        row[iupac_col]
                        if iupac_col and pd.notna(row[iupac_col]) else "N/A",
                        "Limit (AI)":
                        row[limit_col] if limit_col else "N/A",
                        "Notes":
                        row[note_col] if note_col and pd.notna(row[note_col])
                        else "N/A",  # 只顯示 Notes
                        "Matched in Column":
                        ref_col if ref_col else "Full Row Match",
                        "Reference Value":
                        row[ref_col] if ref_col else "See Raw Data"
                    })

    status_box.update(label="執行完成！", state="complete", expanded=False)

    # --- 結果顯示 ---
    st.divider()

    if match_results:
        final_df = pd.DataFrame(match_results).drop_duplicates()

        # 調整欄位順序 (改為 Notes)
        cols_order = [
            "Source", "ScinoPharm Product", "Nitrosamine Impurity",
            "IUPAC Name", "Limit (AI)", "Notes", "Reference Value"
        ]
        cols_order = [c for c in cols_order if c in final_df.columns]
        final_df = final_df[cols_order]

        st.subheader(f"📊 比對結果 (共 {len(final_df)} 筆)")
        st.dataframe(final_df, use_container_width=True, height=500)

        excel_data = generate_excel(final_df, fda_df, ema_df)
        st.download_button(
            label="📥 下載完整 Excel 報表",
            data=excel_data,
            file_name='ScinoPharm_Nitrosamine_Analysis_v5.9.xlsx',
            mime=
            'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            type="primary")
    else:
        st.warning("⚠️ 沒有比對到結果。")

    # --- Debug Logs 區塊 ---
    with st.expander("🛠️ Debug Logs (EMA 欄位檢查)"):
        st.info(f"神隆產品數: {len(scino_apis)}")

        st.markdown("---")
        if not fda_df.empty:
            st.write("🔍 FDA Detected Columns (After Merge):")
            st.write(fda_df.columns.tolist())
            st.write(
                f"- Note Col: {get_display_col(fda_df.columns, ['note', 'comment', 'remark'])}"
            )

        st.markdown("---")
        if not ema_df.empty:
            st.write("🔍 EMA Detected Columns:")
            st.write(
                f"- Nitrosamine Col: {get_display_col(ema_df.columns, ['name', 'nitrosamine', 'impurity'])}"
            )
            st.write(
                f"- Limit Col: {get_display_col(ema_df.columns, ['ai (ng/day)', 'limit', 'intake', 'ai'])}"
            )
            st.write(
                f"- IUPAC Col: {get_display_col(ema_df.columns, ['iupac', 'chemical name'])}"
            )
            st.write(
                f"- Note Col: {get_display_col(ema_df.columns, ['note', 'comment', 'remark'])}"
            )
        else:
            st.error("⚠️ EMA 資料未載入")
