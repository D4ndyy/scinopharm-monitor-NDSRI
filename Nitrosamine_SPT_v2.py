import streamlit as st
import pandas as pd
import requests
import pdfplumber
import io
import re
import warnings
from bs4 import BeautifulSoup
import urllib3
import json

# 忽略 SSL 警告
warnings.filterwarnings("ignore")
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

# --- 頁面設定 ---
st.set_page_config(page_title="ScinoPharm Nitrosamine Monitor", layout="wide")
st.title(" ScinoPharm Nitrosamine Monitor (v7.8 History Tracking)")
st.markdown("""
###  v2 功能更新：
1.  **歷史追蹤 (Tracking)**：上傳上次的 Excel 報表，程式會自動比對並標記出本次新增的資料 (Status: ★ NEW)。
2.  **EMA 抓取修復 **：保留 EMA 多分頁讀取與寬鬆表頭判定。
3.  **其他修正**：保留 FDA 抓取、化學基團過濾等功能。
""")

# ==========================================
# 0. 定義通用字與雜訊 (Stop Words)
# ==========================================
STOP_WORDS = {
    "ACID", "SODIUM", "POTASSIUM", "CALCIUM", "MAGNESIUM", "HYDROCHLORIDE",
    "HCL", "HYDROBROMIDE", "HBR", "ACETATE", "TARTRATE", "CITRATE", "MALEATE",
    "FUMARATE", "MESYLATE", "SUCCINATE", "PHOSPHATE", "SULFATE", "BASE",
    "BENZOATE", "PAMOATE", "ESTOLATE", "GLUCEPTATE", "GLUCONATE", "LACTATE",
    "STEARATE", "ETHYL", "METHYL", "PROPYL", "BUTYL", "PHENYL", "BENZYL",
    "ESTER", "USP", "EP", "BP", "JP", "TABLETS", "CAPSULES", "INJECTION",
    "SOLUTION", "ORAL", "EXTENDED", "RELEASE", "API", "NAME", "PRODUCT",
    "DRUG", "SUBSTANCE", "UNKNOWN", "AND", "WITH", "FORM", "TYPE", "CLASS",
    "GRADE", "GROUP", "PART", "COMPOUND", "IMPURITY", "NEW", "NAB",
    "CHAIN", "SIDE", "FULL", "PROTECTED", "FRAGMENT"
}

# ==========================================
# 1. 核心函數: 產品清單來源
# ==========================================


@st.cache_data(ttl=3600)
def get_scinopharm_apis_auto():
    base_url = "https://www.scinopharm.com"
    target_url = "https://www.scinopharm.com/tw/products-detail/commercialAPI/"

    REAL_HEADERS = {
        "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept":
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Referer": "https://www.scinopharm.com/"
    }

    product_dict = {}
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
                                            name = clean_api_name(val)
                                            if name not in product_dict:
                                                product_dict[name] = "N/A"
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
                                            name = clean_api_name(candidate)
                                            if name not in product_dict:
                                                product_dict[name] = "N/A"

            except requests.exceptions.RequestException as e:
                debug_logs.append(f"❌ 網路請求失敗: {e}")
            except Exception as e:
                debug_logs.append(f"❌ 解析過程錯誤: {e}")

    except Exception as e:
        debug_logs.append(f"❌ 初始連線失敗: {e}")

    result_list = [{'name': k, 'spt': v} for k, v in product_dict.items()]
    return sorted(result_list, key=lambda x: x['name']), debug_logs


def parse_uploaded_file(uploaded_file):
    product_dict = {}
    logs = []

    try:
        if uploaded_file.name.endswith('.csv'):
            try:
                df = pd.read_csv(uploaded_file)
            except:
                uploaded_file.seek(0)
                df = pd.read_csv(uploaded_file, encoding='cp1252')
        else:
            df = pd.read_excel(uploaded_file)

        logs.append(f"📄 讀取欄位: {list(df.columns)}")

        spt_col = None
        for col in df.columns:
            if "spt" in str(col).lower():
                spt_col = col
                break

        if spt_col:
            logs.append(f"✅ 找到 SPT 欄位: '{spt_col}'")
        else:
            logs.append("⚠️ 未找到含有 'SPT' 的欄位，將顯示為 N/A")

        target_col = None
        target_col_2 = None
        possible_names = [
            'product', 'api', 'name', 'drug', 'item', 'substance', '產品', '藥名',
            '品項'
        ]

        for col in df.columns:
            if any(p == str(col).lower() for p in possible_names):
                target_col = col
                break
        if not target_col:
            for col in df.columns:
                if any(p in str(col).lower() for p in possible_names):
                    target_col = col
                    break

        if target_col and "product" in str(target_col).lower():
            for col in df.columns:
                if str(col) != str(target_col) and "product" in str(
                        col).lower() and ("1" in str(col) or "2" in str(col)):
                    target_col_2 = col
                    break

        if not target_col:
            target_col = df.columns[0]
            logs.append(f"⚠️ 未找到明確的產品欄位，使用第一欄: '{target_col}'")
        else:
            logs.append(f"✅ 找到主產品欄位: '{target_col}'")
            if target_col_2:
                logs.append(f"✅ 找到副產品欄位 (將合併): '{target_col_2}'")

        for _, row in df.iterrows():
            val1 = str(row[target_col]).strip()
            name_str = val1

            if target_col_2:
                val2 = row[target_col_2]
                if pd.notna(val2) and str(val2).strip() != '' and str(
                        val2).strip().lower() != 'nan':
                    name_str = f"{val1} {str(val2).strip()}"

            if name_str.lower() == 'nan' or not name_str:
                continue

            cleaned_name = clean_api_name(name_str)

            is_generic_compound = False
            if "compound" in cleaned_name.lower():
                remain = cleaned_name.lower().replace("compound", "").strip()
                if re.fullmatch(r'[a-z0-9\s\-\.]*', remain):
                    is_generic_compound = True

            if is_generic_compound:
                continue

            if len(cleaned_name) > 2:
                spt_val = "N/A"
                if spt_col:
                    raw_spt = row[spt_col]
                    if pd.notna(raw_spt):
                        spt_val = str(raw_spt).strip()

                if cleaned_name not in product_dict:
                    product_dict[cleaned_name] = spt_val

        logs.append(f"✅ 成功處理 {len(product_dict)} 筆產品資料。")

    except Exception as e:
        logs.append(f"❌ 檔案讀取失敗: {str(e)}")

    result_list = [{'name': k, 'spt': v} for k, v in product_dict.items()]
    return sorted(result_list, key=lambda x: x['name']), logs


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

    headers = {
        "User-Agent":
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept":
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8"
    }

    logs = []
    found_date = "N/A"

    try:
        session = requests.Session()
        r = session.get(url, headers=headers, verify=False, timeout=30)
        r.raise_for_status()
        raw_html = r.text
        
        soup = BeautifulSoup(raw_html, 'html.parser')
        text_content = soup.get_text(" ", strip=True)

        # 嘗試抓取 "Content current as of:"
        date_match = re.search(r"Content current as of:.*?([\d]{2}/[\d]{2}/[\d]{4})", text_content, re.IGNORECASE)
        if date_match:
            found_date = date_match.group(1).strip()
            logs.append(f"📅 FDA Updated Date Found: {found_date}")
        else:
            logs.append("⚠️ FDA Updated Date not found.")

        all_tables_data = []

        json_pattern = re.compile(r'data\s*:\s*(\[\s*\{.*\}\s*\])', re.DOTALL)
        matches = json_pattern.findall(raw_html)

        if matches:
            logs.append(
                f"Strategy 1 (JSON Regex): Found {len(matches)} potential JSON data blocks."
            )
            for i, match in enumerate(matches):
                try:
                    clean_match = match.strip()
                    json_data = json.loads(clean_match)
                    if isinstance(json_data, list) and len(json_data) > 0:
                        df = pd.DataFrame(json_data)
                        df = df.reset_index(drop=True)
                        all_tables_data.append(df)
                        logs.append(f"JSON Block {i} parsed: {len(df)} rows.")
                except:
                    pass

        soup = BeautifulSoup(raw_html, 'html.parser')
        tables = soup.find_all('table')

        for i, table in enumerate(tables):
            try:
                headers_list = []
                thead = table.find('thead')
                if thead:
                    headers_list = [
                        th.get_text(strip=True) for th in thead.find_all('th')
                    ]

                if not headers_list:
                    first_row = table.find('tr')
                    if first_row:
                        headers_list = [
                            td.get_text(strip=True)
                            for td in first_row.find_all(['td', 'th'])
                        ]

                if headers_list:
                    headers_list = [
                        h if h else f"Unnamed_{j}"
                        for j, h in enumerate(headers_list)
                    ]
                    seen = set()
                    new_headers = []
                    for h in headers_list:
                        c = h
                        count = 1
                        while c in seen:
                            c = f"{h}_{count}"
                            count += 1
                        seen.add(c)
                        new_headers.append(c)
                    headers_list = new_headers

                rows_data = []
                tbody = table.find('tbody')
                data_rows = tbody.find_all('tr') if tbody else table.find_all(
                    'tr')

                start_idx = 0
                if not thead and data_rows:
                    start_idx = 1

                for row in data_rows[start_idx:]:
                    cols = row.find_all('td')
                    if not cols: continue
                    rows_data.append([td.get_text(strip=True) for td in cols])

                if headers_list and rows_data:
                    max_len = len(headers_list)
                    clean_rows = []
                    for row in rows_data:
                        if len(row) < max_len:
                            row.extend([None] * (max_len - len(row)))
                        elif len(row) > max_len:
                            row = row[:max_len]
                        clean_rows.append(row)

                    df = pd.DataFrame(clean_rows, columns=headers_list)
                    df = df.reset_index(drop=True)
                    all_tables_data.append(df)
                    logs.append(
                        f"HTML Table {i} parsed successfully with {len(df)} rows."
                    )
            except Exception as e:
                logs.append(f"Manual parse failed for table {i}: {e}")

        valid_dfs = []
        for df in all_tables_data:
            df.columns = [
                str(c).strip().replace('\n', ' ') for c in df.columns
            ]

            rename_map = {}
            has_critical_data = False

            for col in df.columns:
                c_lower = col.lower()
                if any(k in c_lower for k in ['nitrosamine', 'impurity']):
                    rename_map[col] = 'Nitrosamine'
                    has_critical_data = True
                elif any(k in c_lower for k in ['limit', 'ai', 'intake']):
                    rename_map[col] = 'Limit'
                elif any(k in c_lower for k in ['note', 'comment', 'remark']):
                    rename_map[col] = 'Notes'
                elif any(k in c_lower
                         for k in ['source', 'drug', 'product', 'api']):
                    rename_map[col] = 'Source'
                elif any(k in c_lower for k in ['iupac', 'chemical']):
                    rename_map[col] = 'IUPAC'

            if has_critical_data:
                df = df.rename(columns=rename_map)
                for req_col in [
                        'Nitrosamine', 'Limit', 'Source', 'Notes', 'IUPAC'
                ]:
                    if req_col not in df.columns:
                        df[req_col] = pd.NA

                df = df.reset_index(drop=True)
                valid_dfs.append(df)

        if valid_dfs:
            target_dfs = valid_dfs[:2]
            final_df = pd.concat(target_dfs, ignore_index=True)
            final_df = final_df.reset_index(drop=True)
            final_df = final_df.reset_index(drop=True)
            return final_df, found_date, logs

        return pd.DataFrame(), found_date, logs

    except requests.exceptions.RequestException as e:
        return pd.DataFrame(), "N/A", [f"Network Error: {e}"]
    except Exception as e:
        return pd.DataFrame(), "N/A", [f"General Error: {e}"]


@st.cache_data(ttl=86400)
def get_ema_data():
    base_url = "https://www.ema.europa.eu"
    page_url = "https://www.ema.europa.eu/en/human-regulatory-overview/post-authorisation/pharmacovigilance-post-authorisation/referral-procedures-human-medicines/nitrosamine-impurities/nitrosamine-impurities-guidance-marketing-authorisation-holders"

    log_messages = []
    found_date = "N/A"

    try:
        headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
        r = requests.get(page_url, headers=headers, verify=False)
        soup = BeautifulSoup(r.text, 'html.parser')

        # 嘗試抓取 EMA 日期
        # 常見格式: "First published: 21/09/2020", "Last updated: 23/10/2023"
        # 尋找含有 published 或 updated 的文字區塊
        date_patterns = [
            r"(?:First published|Last updated|Published).*?(\d{2}/\d{2}/\d{4})",
            r"(\d{2}\s+[A-Za-z]+\s+\d{4})"
        ]

        text_content = soup.get_text(" ", strip=True)
        # 簡單過濾一下，只找 date 附近的
        
        ema_date_match = None
        # 優先找 "Last updated"
        last_updated_node = soup.find(string=re.compile(r"Last updated", re.IGNORECASE))
        if last_updated_node:
             parent_text = last_updated_node.parent.get_text(strip=True)
             # Extract date from this text
             m = re.search(r"(\d{2}/\d{2}/\d{4})", parent_text)
             if m:
                 found_date = m.group(1)
        
        if found_date == "N/A":
             # Fallback to general text search
             m = re.search(r"(?:Last updated|First published).*?(\d{2}/\d{2}/\d{4})", text_content, re.IGNORECASE)
             if m:
                 found_date = m.group(1)

        if found_date != "N/A":
             log_messages.append(f"📅 EMA Date Found: {found_date}")
        else:
             log_messages.append("⚠️ EMA Date not found.")


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

            xls = pd.read_excel(io.BytesIO(file_resp.content),
                                sheet_name=None,
                                header=None)

            all_sheets_data = []

            for sheet_name, temp_df in xls.items():
                log_messages.append(f"Analyzing EMA Sheet: {sheet_name}")

                best_idx = 0
                max_score = 0
                keywords = [
                    "nitrosamine", "limit", "intake", "substance", "ng/day",
                    "iupac", "impurity", "structure", "cas", "source",
                    "ai (ng/day)"
                ]

                scan_rows = min(30, len(temp_df))
                for idx in range(scan_rows):
                    row = temp_df.iloc[idx]
                    row_text = " ".join(
                        [str(x).lower() for x in row if pd.notna(x)])
                    score = sum(1 for k in keywords if k in row_text)
                    if score > max_score:
                        max_score = score
                        best_idx = idx

                if max_score == 0 and len(temp_df) < 5:
                    log_messages.append(
                        f"  -> Skipping small/irrelevant sheet: {sheet_name}")
                    continue

                new_header = temp_df.iloc[best_idx]
                df = temp_df.iloc[best_idx + 1:].copy()
                df.columns = new_header
                df.columns = [
                    str(c).strip().replace('\n', ' ') for c in df.columns
                ]

                df = df.reset_index(drop=True)
                all_sheets_data.append(df)
                log_messages.append(
                    f"  -> Added table from {sheet_name} with {len(df)} rows.")

            if all_sheets_data:
                final_df = pd.concat(all_sheets_data, ignore_index=True)
                final_df = final_df.reset_index(drop=True)
                return final_df, found_date, log_messages

            return pd.DataFrame(), found_date, log_messages

        return pd.DataFrame(), found_date, ["No link found"]
    except Exception as e:
        return pd.DataFrame(), "N/A", [str(e)]


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
        if "COMPOUND" in scino_clean:
            return False, ""
        core_tokens = {scino_clean}

    row_text = " ".join(
        [str(val).upper() for val in row_series.values if pd.notna(val)])

    for token in core_tokens:
        pattern = r'\b' + re.escape(token) + r'\b'
        if re.search(pattern, row_text):
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
            sheet.set_column(0, 9, 20)

    return output.getvalue()


# ==========================================
# 主程式 UI
# ==========================================

# --- Sidebar: 選擇資料來源 ---
st.sidebar.header("⚙️ 設定 (Settings)")
source_mode = st.sidebar.radio(
    "選擇產品清單來源 (Source):",
    ("🌐 自動爬取神隆官網 (Auto-Scrape)", "📂 手動上傳清單 (Manual Upload)"))

# 【新增功能 v7.8】歷史比對檔案上傳
st.sidebar.markdown("---")
st.sidebar.subheader("📜 歷史追蹤 (History Tracking)")
history_file = st.sidebar.file_uploader("上傳上次的結果 (Optional)", type=['xlsx'])

api_list = []
log_msgs = []
ready_to_run = False

if source_mode == "🌐 自動爬取神隆官網 (Auto-Scrape)":
    st.sidebar.info("程式將自動連線至 scinopharm.com 下載最新的 PDF 產品列表。")
    if st.sidebar.button("載入官網資料", type="primary"):
        with st.spinner("正在連線至神隆官網..."):
            api_list, log_msgs = get_scinopharm_apis_auto()
            if api_list:
                st.session_state['api_list'] = api_list
                st.session_state['log_msgs'] = log_msgs
                st.success(f"成功載入 {len(api_list)} 筆產品！")
            else:
                st.error("未找到產品，請檢查連線或改用手動上傳。")

    if 'api_list' in st.session_state and st.session_state['api_list']:
        api_list = st.session_state['api_list']
        log_msgs = st.session_state['log_msgs']
        ready_to_run = True

else:
    st.sidebar.info("請上傳 Excel (.xlsx) 或 CSV 檔。支援 'SPT' 欄位自動讀取。")
    uploaded_file = st.sidebar.file_uploader("上傳產品清單", type=['xlsx', 'csv'])

    if uploaded_file:
        api_list, log_msgs = parse_uploaded_file(uploaded_file)
        if api_list:
            st.sidebar.success(f"✅ 已讀取 {len(api_list)} 筆資料")
            ready_to_run = True
            with st.expander("預覽匯入清單 (前 5 筆)"):
                st.write(api_list[:5])
        else:
            st.sidebar.error("❌ 無法讀取資料，請檢查檔案格式。")

# --- 主畫面 ---

if ready_to_run:
    st.subheader(
        f"目前監控清單: {len(api_list)} 項產品 ({'自動爬取' if source_mode.startswith('🌐') else '手動匯入'})"
    )

    if st.button("🚀 開始執行比對 (Start Analysis)", type="primary"):
        status_box = st.status("正在分析中...", expanded=True)

        # 2. FDA / EMA
        status_box.write("🌍 下載 FDA / EMA 資料庫...")
        fda_df, fda_date, fda_logs = get_fda_data()
        ema_df, ema_date, ema_logs = get_ema_data()

        if not fda_df.empty:
            status_box.write(
                f"✅ FDA: {len(fda_df)} 筆 (已過濾僅 Table 1 & 2), EMA: {len(ema_df)} 筆"
            )
        else:
            status_box.write(f"⚠️ FDA: 0 筆 (抓取失敗), EMA: {len(ema_df)} 筆")
            log_msgs.extend(fda_logs)

        # 3. 比對
        status_box.write("🔍 執行比對...")
        match_results = []

        # --- FDA 比對 ---
        if not fda_df.empty:
            nitro_col = get_display_col(
                fda_df.columns, ['Nitrosamine', 'nitrosamine', 'impurity'])
            limit_col = get_display_col(fda_df.columns,
                                        ['Limit', 'limit', 'ai'])
            iupac_col = get_display_col(fda_df.columns, ['IUPAC', 'iupac'])
            source_col = get_display_col(fda_df.columns, ['Source', 'source'])
            note_col = get_display_col(fda_df.columns,
                                       ['Notes', 'note', 'comment'])

            ref_col = source_col

            for _, row in fda_df.iterrows():
                for my_api_obj in api_list:
                    my_api_name = my_api_obj['name']
                    my_api_spt = my_api_obj['spt']

                    is_match, _ = smart_match(my_api_name, row)
                    if is_match:
                        match_results.append({
                            "Source":
                            "USFDA",
                            "ScinoPharm Product":
                            my_api_name,
                            "SPT Project num":
                            my_api_spt,
                            "Nitrosamine Impurity":
                            row[nitro_col] if nitro_col else "Check Row",
                            "IUPAC Name":
                            row[iupac_col] if iupac_col else "N/A",
                            "Limit (AI)":
                            row[limit_col] if limit_col else "N/A",
                            "Notes":
                            row[note_col] if note_col else "N/A",
                            "Updated date": fda_date,
                            "Matched in Column":
                            ref_col if ref_col else "Full Row Match",
                            "Reference Value":
                            row[ref_col] if ref_col else "See Raw Data"
                        })

        # --- EMA 比對 ---
        if not ema_df.empty:
            nitro_col = get_display_col(ema_df.columns,
                                        ['name', 'nitrosamine', 'impurity'])
            limit_col = get_display_col(
                ema_df.columns, ['ai (ng/day)', 'limit', 'intake', 'ai'])
            iupac_col = get_display_col(ema_df.columns,
                                        ['iupac', 'chemical name'])
            source_col = get_display_col(ema_df.columns, ['source'])
            drug_col = get_display_col(
                ema_df.columns, ['substance', 'api', 'product', 'active'])
            note_col = get_display_col(ema_df.columns,
                                       ['note', 'comment', 'remark'])
            ref_col = source_col if source_col else drug_col

            for _, row in ema_df.iterrows():
                for my_api_obj in api_list:
                    my_api_name = my_api_obj['name']
                    my_api_spt = my_api_obj['spt']

                    is_match, _ = smart_match(my_api_name, row)
                    if is_match:
                        match_results.append({
                            "Source":
                            "EMA",
                            "ScinoPharm Product":
                            my_api_name,
                            "SPT Project num":
                            my_api_spt,
                            "Nitrosamine Impurity":
                            row[nitro_col] if nitro_col
                            and pd.notna(row[nitro_col]) else "Check Row",
                            "IUPAC Name":
                            row[iupac_col] if iupac_col
                            and pd.notna(row[iupac_col]) else "N/A",
                            "Limit (AI)":
                            row[limit_col] if limit_col else "N/A",
                            "Notes":
                            row[note_col]
                            if note_col and pd.notna(row[note_col]) else "N/A",
                            "Updated date": ema_date,
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

            # 【新增功能 v7.8】歷史比對邏輯
            final_df['Status'] = ""  # 預設為空

            if history_file:
                try:
                    # 讀取舊檔案 (預設讀取 Summary_Match 分頁，若無則讀第一頁)
                    try:
                        old_df = pd.read_excel(history_file,
                                               sheet_name='Summary_Match')
                    except:
                        old_df = pd.read_excel(history_file)

                    # 建立指紋集合: SPT編號 + 雜質名稱 (去除空白與大小寫以確保比對準確)
                    # 如果沒有 SPT 欄位，則改用 產品名稱 + 雜質名稱
                    old_fingerprints = set()

                    spt_col_name = None
                    for c in old_df.columns:
                        if 'spt' in c.lower():
                            spt_col_name = c
                            break

                    nitro_col_name = None
                    for c in old_df.columns:
                        if 'nitrosamine' in c.lower(
                        ) and 'impurity' in c.lower():
                            nitro_col_name = c
                            break

                    if nitro_col_name:
                        for _, row in old_df.iterrows():
                            # 組合指紋 Key
                            key_part1 = str(row[spt_col_name]).strip().upper(
                            ) if spt_col_name else str(row[0]).strip().upper()
                            key_part2 = str(
                                row[nitro_col_name]).strip().upper()
                            old_fingerprints.add(f"{key_part1}|{key_part2}")

                    # 比對新資料
                    new_count = 0
                    for idx, row in final_df.iterrows():
                        key_part1 = str(row['SPT Project num']).strip().upper(
                        ) if 'SPT Project num' in row else str(
                            row['ScinoPharm Product']).strip().upper()
                        key_part2 = str(
                            row['Nitrosamine Impurity']).strip().upper()
                        current_fp = f"{key_part1}|{key_part2}"

                        if current_fp not in old_fingerprints:
                            final_df.at[idx, 'Status'] = "★ NEW"
                            new_count += 1

                    if new_count > 0:
                        st.warning(f"🔔 發現 {new_count} 筆新資料！已標記為 '★ NEW'")
                    else:
                        st.info("✅ 與歷史紀錄相比，無新增資料。")

                except Exception as e:
                    st.error(f"歷史檔案比對失敗: {e}")

            # 調整欄位順序 (Status 放最前)
            cols_order = [
                "Status", "Source", "ScinoPharm Product", "SPT Project num",
                "Nitrosamine Impurity", "IUPAC Name", "Limit (AI)", "Notes",
                "Updated date", "Reference Value"
            ]
            cols_order = [c for c in cols_order if c in final_df.columns]
            final_df = final_df[cols_order]

            # 根據 Status 排序，新發現的放前面
            final_df = final_df.sort_values(
                by=['Status', 'ScinoPharm Product'], ascending=[False, True])

            st.subheader(f"📊 比對結果 (共 {len(final_df)} 筆)")

            # 使用 style highlight 新資料
            def highlight_new(row):
                return ['background-color: #ffffcc'] * len(
                    row) if row['Status'] == '★ NEW' else [''] * len(row)

            st.dataframe(final_df.style.apply(highlight_new, axis=1),
                         use_container_width=True,
                         height=500)

            excel_data = generate_excel(final_df, fda_df, ema_df)
            st.download_button(
                label="📥 下載完整 Excel 報表",
                data=excel_data,
                file_name='ScinoPharm_Nitrosamine_Analysis_v7.8.xlsx',
                mime=
                'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                type="primary")
        else:
            st.warning("⚠️ 沒有比對到結果。")
else:
    st.info("👈 請在左側側邊欄選擇資料來源並載入資料。")

# --- Debug Logs ---
with st.expander("🛠️ Debug Logs"):
    if log_msgs:
        for msg in log_msgs:
            st.text(msg)
    else:
        st.text("尚無紀錄")

