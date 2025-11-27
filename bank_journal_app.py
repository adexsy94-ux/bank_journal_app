import io
from typing import List, Dict

import pandas as pd
import streamlit as st


# -----------------------------
# Helper functions
# -----------------------------

def create_template_df() -> pd.DataFrame:
    """
    Create an empty template DataFrame with only the necessary columns:
    account type, account, start date, details, debit, credit.
    """
    columns = [
        "account type",
        "account",
        "start date",
        "details",
        "debit",
        "credit",
    ]
    df = pd.DataFrame(columns=columns)
    return df


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Strip spaces and lower-case columns to make them easier to reference.
    """
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    return df


def to_number(series: pd.Series) -> pd.Series:
    """
    Convert a series to numeric, removing commas and handling blanks.
    """
    return pd.to_numeric(
        series.astype(str).str.replace(",", "").str.strip(),
        errors="coerce"
    ).fillna(0)


def classify_cash_flow_type(main_account_type: str) -> str:
    """
    Classify cash flow according to IFRS-style sections based on the MAIN account type
    driving the transaction (not the bank's own account type).

    - Investing: Non-current assets, fixed assets, etc.
    - Financing: Equity, share capital, reserves, long-term / non-current liabilities.
    - Operating: All other (revenue, cost of sales, expenses, working capital, etc.)
    """
    if not main_account_type:
        return "Operating"

    t = main_account_type.strip().lower()

    # Investing activities
    investing_keywords = [
        "non current asset", "non-current asset", "noncurrent asset",
        "non current assets", "non-current assets", "noncurrent assets",
        "fixed asset", "fixed assets", "investment property", "investment"
    ]
    if any(k in t for k in investing_keywords):
        return "Investing"

    # Financing activities
    financing_keywords = [
        "equity", "share capital", "capital", "retained earnings", "reserves",
        "non current liability", "non-current liability", "noncurrent liability",
        "non current liabilities", "non-current liabilities", "noncurrent liabilities",
        "long term liability", "long-term liability", "long term liabilities", "long-term liabilities",
        "loan", "borrowings"
    ]
    if any(k in t for k in financing_keywords):
        return "Financing"

    # Default: Operating (income, cost of sales, expenses, current assets/liabilities, etc.)
    return "Operating"


def generate_journal_rows(
    df: pd.DataFrame,
    bank_account_name: str,
    bank_account_type: str,
    suspense_account_name: str,
    suspense_account_type: str
) -> pd.DataFrame:
    """
    Given a normalized (and possibly filtered) bank statement dataframe,
    generate 4-leg journal entries using Option B (via Suspense).

    For each row:
      - If DEBIT > 0 -> money OUT (payment)
      - If CREDIT > 0 -> money IN (receipt)

    Structure (payment / outflow):
      1. Dr Main Account   / Cr Suspense
      2. Dr Suspense       / Cr Bank

    Structure (receipt / inflow):
      1. Dr Suspense       / Cr Main Account
      2. Dr Bank           / Cr Suspense

    For IFRS cashflow: bank legs are tagged with "Cash Flow Category":
      Operating / Investing / Financing based on MAIN account type.
    """
    rows: List[Dict] = []

    # Expected column names after normalization
    col_account_type = "account type"
    col_account = "account"
    col_date = "start date"
    col_details = "details"
    col_debit = "debit"
    col_credit = "credit"

    # Ensure numeric debit/credit
    if col_debit in df.columns:
        df[col_debit] = to_number(df[col_debit])
    else:
        df[col_debit] = 0

    if col_credit in df.columns:
        df[col_credit] = to_number(df[col_credit])
    else:
        df[col_credit] = 0

    for _, row in df.iterrows():
        account_type = str(row.get(col_account_type, "")).strip()
        account_name = str(row.get(col_account, "")).strip()
        date_val = row.get(col_date, "")
        details = str(row.get(col_details, "")).strip()

        debit = float(row.get(col_debit, 0) or 0)
        credit = float(row.get(col_credit, 0) or 0)

        # Skip rows with no amount
        if debit == 0 and credit == 0:
            continue

        # Decide direction based on which side has amount
        if debit > 0 and credit == 0:
            direction = "out"   # payment / money leaving bank
            amount = debit
        elif credit > 0 and debit == 0:
            direction = "in"    # receipt / money coming into bank
            amount = credit
        else:
            # Mixed or inconsistent row – skip for safety.
            continue

        # Short narration helper
        base_narration = details if details else "Bank transaction"
        short_narr = base_narration[:80]

        # Cash flow category based on main account type
        main_type_for_cf = account_type if account_type else ("Expense" if direction == "out" else "Income")
        cf_category = classify_cash_flow_type(main_type_for_cf)

        # --------- PAYMENTS (DEBIT > 0) ----------
        if direction == "out":
            # Leg 1: Dr Main Account (no cashflow tag here)
            rows.append(
                {
                    "Date": date_val,
                    "Transaction / Details": base_narration,
                    "Leg": 1,
                    "Account Type": account_type if account_type else "Expense",
                    "Account": account_name if account_name else "Main Account",
                    "Dr Amount": amount,
                    "Cr Amount": 0.0,
                    "Narration": f"Record {account_name or 'expense'} for {short_narr}",
                    "Cash Flow Category": "",
                }
            )

            # Leg 2: Cr Suspense
            rows.append(
                {
                    "Date": date_val,
                    "Transaction / Details": base_narration,
                    "Leg": 2,
                    "Account Type": suspense_account_type,
                    "Account": suspense_account_name,
                    "Dr Amount": 0.0,
                    "Cr Amount": amount,
                    "Narration": "Temporary posting of payment to suspense",
                    "Cash Flow Category": "",
                }
            )

            # Leg 3: Dr Suspense
            rows.append(
                {
                    "Date": date_val,
                    "Transaction / Details": base_narration,
                    "Leg": 3,
                    "Account Type": suspense_account_type,
                    "Account": suspense_account_name,
                    "Dr Amount": amount,
                    "Cr Amount": 0.0,
                    "Narration": "Clear suspense against bank movement",
                    "Cash Flow Category": "",
                }
            )

            # Leg 4: Cr Bank (this is the CASH OUTFLOW)
            rows.append(
                {
                    "Date": date_val,
                    "Transaction / Details": base_narration,
                    "Leg": 4,
                    "Account Type": bank_account_type,
                    "Account": bank_account_name,
                    "Dr Amount": 0.0,
                    "Cr Amount": amount,
                    "Narration": f"Bank payment for {short_narr}",
                    "Cash Flow Category": cf_category,
                }
            )

        # --------- RECEIPTS (CREDIT > 0) ----------
        else:  # direction == "in"
            # Leg 1: Dr Suspense
            rows.append(
                {
                    "Date": date_val,
                    "Transaction / Details": base_narration,
                    "Leg": 1,
                    "Account Type": suspense_account_type,
                    "Account": suspense_account_name,
                    "Dr Amount": amount,
                    "Cr Amount": 0.0,
                    "Narration": "Temporary posting of receipt to suspense",
                    "Cash Flow Category": "",
                }
            )

            # Leg 2: Cr Main Account
            rows.append(
                {
                    "Date": date_val,
                    "Transaction / Details": base_narration,
                    "Leg": 2,
                    "Account Type": account_type if account_type else "Income",
                    "Account": account_name if account_name else "Main Account",
                    "Dr Amount": 0.0,
                    "Cr Amount": amount,
                    "Narration": f"Record {account_name or 'income'} for {short_narr}",
                    "Cash Flow Category": "",
                }
            )

            # Leg 3: Dr Bank (this is the CASH INFLOW)
            rows.append(
                {
                    "Date": date_val,
                    "Transaction / Details": base_narration,
                    "Leg": 3,
                    "Account Type": bank_account_type,
                    "Account": bank_account_name,
                    "Dr Amount": amount,
                    "Cr Amount": 0.0,
                    "Narration": "Bank receipt from suspense",
                    "Cash Flow Category": cf_category,
                }
            )

            # Leg 4: Cr Suspense
            rows.append(
                {
                    "Date": date_val,
                    "Transaction / Details": base_narration,
                    "Leg": 4,
                    "Account Type": suspense_account_type,
                    "Account": suspense_account_name,
                    "Dr Amount": 0.0,
                    "Cr Amount": amount,
                    "Narration": "Clear suspense after bank receipt",
                    "Cash Flow Category": "",
                }
            )

    journal_df = pd.DataFrame(rows)
    # Sort by date & leg to keep clean
    if not journal_df.empty:
        journal_df = journal_df.sort_values(by=["Date", "Leg"]).reset_index(drop=True)
    return journal_df


def load_uploaded_file(uploaded_file) -> pd.DataFrame:
    """
    Robust reader for CSV / Excel uploaded via Streamlit.
    - Excel: use pandas.read_excel directly.
    - CSV: try several encodings to avoid 'utf-8' codec errors.
    """
    file_name = uploaded_file.name.lower()

    # Excel files
    if file_name.endswith(".xlsx") or file_name.endswith(".xls"):
        uploaded_file.seek(0)
        return pd.read_excel(uploaded_file)

    # CSV files – read bytes then try multiple encodings
    raw_bytes = uploaded_file.read()
    encodings_to_try = ["utf-8", "utf-8-sig", "cp1252", "latin1"]

    last_error = None
    for enc in encodings_to_try:
        try:
            text = raw_bytes.decode(enc)
            df = pd.read_csv(io.StringIO(text))
            return df
        except UnicodeDecodeError as e:
            last_error = e
            continue
        except Exception as e:
            last_error = e
            continue

    # If we get here, all attempts failed
    raise RuntimeError(f"Could not decode file with tried encodings {encodings_to_try}. Last error: {last_error}")


# -----------------------------
# Reporting helpers (from journal)
# -----------------------------

def ensure_journal_dates(journal_df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure 'Date' column in journal is a proper datetime (extra column Date_dt).
    """
    df = journal_df.copy()
    df["Date_dt"] = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True)
    return df


def build_trial_balance(journal_df: pd.DataFrame) -> pd.DataFrame:
    """
    Trial balance: list of all ledger balances.
    Each account shows ONE balance only:
      - If net is debit → Debit column
      - If net is credit → Credit column
    """
    tb_raw = (
        journal_df
        .groupby(["Account Type", "Account"], as_index=False)
        .agg(
            Total_Debit=("Dr Amount", "sum"),
            Total_Credit=("Cr Amount", "sum"),
        )
    )

    # Net = Dr - Cr (positive => debit balance; negative => credit balance)
    tb_raw["Net"] = tb_raw["Total_Debit"] - tb_raw["Total_Credit"]

    tb_raw["Debit"] = tb_raw["Net"].apply(lambda x: x if x > 0 else 0.0)
    tb_raw["Credit"] = tb_raw["Net"].apply(lambda x: -x if x < 0 else 0.0)

    tb = tb_raw[["Account Type", "Account", "Debit", "Credit"]].copy()

    # Optional: add totals row
    total_debit = tb["Debit"].sum()
    total_credit = tb["Credit"].sum()
    totals_row = pd.DataFrame(
        {
            "Account Type": ["TOTAL"],
            "Account": [""],
            "Debit": [total_debit],
            "Credit": [total_credit],
        }
    )
    tb = pd.concat([tb, totals_row], ignore_index=True)

    return tb


def build_gl_detail(journal_df: pd.DataFrame) -> pd.DataFrame:
    """
    General ledger detail with running balance per account.
    Debit = +, Credit = -.
    """
    df = ensure_journal_dates(journal_df)
    df = df.sort_values(["Account", "Date_dt", "Leg"]).copy()
    df["Amount_Signed"] = df["Dr Amount"] - df["Cr Amount"]

    # Running balance by account
    df["Running_Balance"] = df.groupby("Account")["Amount_Signed"].cumsum()
    return df


def build_income_statement(tb_with_totals: pd.DataFrame):
    """
    Income Statement with:
    - Revenue
    - Cost of Sales
    - Gross Profit
    - Operating Expenses
    - Net Profit

    Uses Account Type classification:
    - Revenue: account type contains 'income', 'revenue', 'sales'
      BUT explicitly excludes cost-of-sales accounts.
    - Cost of Sales: account type contains 'cost of sales', 'cogs', 'cost of goods sold'
    - Expenses: account type contains 'expense'
    """
    # Remove totals row if present
    tb = tb_with_totals[tb_with_totals["Account Type"] != "TOTAL"].copy()

    # Net impact: Credit - Debit
    tb["NetImpact"] = tb["Credit"] - tb["Debit"]
    tb["Account Type Lower"] = tb["Account Type"].astype(str).str.lower()

    # Identify cost of sales first
    cos_mask = tb["Account Type Lower"].str.contains(
        "cost of sales|cogs|cost of goods sold"
    )

    # Revenue (exclude any accounts tagged as cost of sales)
    revenue_mask_raw = tb["Account Type Lower"].str.contains(
        "income|revenue|sales"
    )
    revenue_mask = revenue_mask_raw & ~cos_mask

    # Expenses (operating etc.)
    expense_mask = tb["Account Type Lower"].str.contains("expense")

    revenue_df = tb[revenue_mask].copy()
    cos_df = tb[cos_mask].copy()
    expense_df = tb[expense_mask].copy()

    # Totals (positive for reporting)
    total_revenue = float(revenue_df["NetImpact"].sum() or 0)
    total_cos = float((-(cos_df["NetImpact"])).sum() or 0)
    total_expenses = float((-(expense_df["NetImpact"])).sum() or 0)

    gross_profit = total_revenue - total_cos
    net_profit = gross_profit - total_expenses

    summary_rows = [
        {"Line": "Total Revenue", "Amount": total_revenue},
        {"Line": "Cost of Sales", "Amount": total_cos},
        {"Line": "Gross Profit", "Amount": gross_profit},
        {"Line": "Operating Expenses", "Amount": total_expenses},
        {"Line": "Net Profit / (Loss)", "Amount": net_profit},
    ]
    summary_df = pd.DataFrame(summary_rows)

    return revenue_df, cos_df, expense_df, summary_df


def build_balance_sheet(tb_with_totals: pd.DataFrame) -> Dict[str, pd.DataFrame]:
    """
    Statement of Financial Position classification:

    - Current Assets
    - Non-Current Assets (we'll use as Property and Equipment block)
    - Current Liabilities
    - Non-Current Liabilities
    - Equity

    Uses flexible substring rules on Account Type:
    - Any type containing 'asset' is an Asset
      * If it also contains 'non current', 'non-current', 'noncurrent' or 'fixed' → Non-Current Asset
      * Otherwise → Current Asset
    - Any type containing 'liab' is a Liability
      * If it also contains 'non current', 'non-current', 'noncurrent', 'long term', 'long-term' → Non-Current Liability
      * Otherwise → Current Liability
    - Any type containing 'equity', 'capital', 'retained', 'reserve', 'share capital' → Equity
    - Everything else → Other
    """
    tb = tb_with_totals[tb_with_totals["Account Type"] != "TOTAL"].copy()
    tb["Account Type Lower"] = tb["Account Type"].astype(str).str.lower()

    def classify(row):
        t = row["Account Type Lower"]

        # Assets
        if "asset" in t:
            if any(k in t for k in ["non current", "non-current", "noncurrent", "fixed"]):
                return "Non-Current Asset"
            else:
                return "Current Asset"

        # Liabilities
        if "liab" in t:
            if any(k in t for k in ["non current", "non-current", "noncurrent", "long term", "long-term"]):
                return "Non-Current Liability"
            else:
                return "Current Liability"

        # Equity
        if any(k in t for k in ["equity", "capital", "retained", "reserve", "share capital"]):
            return "Equity"

        # All others (income, expenses, cost of sales, etc.)
        return "Other"

    tb["BS_Category"] = tb.apply(classify, axis=1)

    # For display, compute balances:
    # Assets: Debit - Credit
    # Liabilities & Equity: Credit - Debit
    def bs_balance(row):
        if row["BS_Category"] in ["Current Asset", "Non-Current Asset"]:
            return row["Debit"] - row["Credit"]
        elif row["BS_Category"] in ["Current Liability", "Non-Current Liability", "Equity"]:
            return row["Credit"] - row["Debit"]
        else:
            return row["Credit"] - row["Debit"]

    tb["Balance"] = tb.apply(bs_balance, axis=1)

    result = {
        "current_assets": tb[tb["BS_Category"] == "Current Asset"].copy(),
        "noncurrent_assets": tb[tb["BS_Category"] == "Non-Current Asset"].copy(),
        "current_liabilities": tb[tb["BS_Category"] == "Current Liability"].copy(),
        "noncurrent_liabilities": tb[tb["BS_Category"] == "Non-Current Liability"].copy(),
        "equity": tb[tb["BS_Category"] == "Equity"].copy(),
        "other": tb[tb["BS_Category"] == "Other"].copy(),
    }
    return result


def build_cashbook_ifrs(
    journal_df: pd.DataFrame,
    bank_account_name: str,
    opening_balance: float
) -> (pd.DataFrame, pd.DataFrame):
    """
    IFRS-style cashflow based on bank account movements.

    - Only rows where Account == bank_account_name
    - Uses 'Cash Flow Category' (Operating / Investing / Financing) from bank legs.
    - Computes:
        * Net cash from operating, investing, financing
        * Net increase/decrease in cash
        * Closing bank balance = opening + net_cash_movement
        * Running cash balance per transaction
    """
    df = ensure_journal_dates(journal_df)

    cb = df[df["Account"] == bank_account_name].copy()
    cb = cb.sort_values(["Date_dt", "Leg"])

    if cb.empty:
        return cb, pd.DataFrame()

    # Movement (+ for inflow, - for outflow)
    cb["Cash_Movement"] = cb["Dr Amount"] - cb["Cr Amount"]

    # Use Cash Flow Category if present, else default to Operating
    if "Cash Flow Category" in cb.columns:
        cf_cat = cb["Cash Flow Category"].fillna("")
        cf_cat = cf_cat.replace("", "Operating")
        cb["Cash Flow Category"] = cf_cat
    else:
        cb["Cash Flow Category"] = "Operating"

    # Running cash balance including opening balance
    cb["Running_Cash_Balance"] = opening_balance + cb["Cash_Movement"].cumsum()

    # Summaries by section
    operating_total = cb.loc[cb["Cash Flow Category"] == "Operating", "Cash_Movement"].sum()
    investing_total = cb.loc[cb["Cash Flow Category"] == "Investing", "Cash_Movement"].sum()
    financing_total = cb.loc[cb["Cash Flow Category"] == "Financing", "Cash_Movement"].sum()

    net_change = operating_total + investing_total + financing_total
    closing_balance_calc = opening_balance + net_change

    # Last running balance from movements
    closing_balance_running = cb["Running_Cash_Balance"].iloc[-1]

    diff = closing_balance_calc - closing_balance_running

    summary_rows = [
        {"Line": "Net cash from Operating activities", "Amount": operating_total},
        {"Line": "Net cash from Investing activities", "Amount": investing_total},
        {"Line": "Net cash from Financing activities", "Amount": financing_total},
        {"Line": "Net increase / (decrease) in cash", "Amount": net_change},
        {"Line": "Opening bank balance", "Amount": opening_balance},
        {"Line": "Closing bank balance (Opening + Net cash)", "Amount": closing_balance_calc},
        {"Line": "Closing bank balance (from running ledger)", "Amount": closing_balance_running},
        {"Line": "Difference (should be 0)", "Amount": diff},
    ]
    summary_df = pd.DataFrame(summary_rows)

    cb_display = cb[
        [
            "Date",
            "Transaction / Details",
            "Leg",
            "Account",
            "Dr Amount",
            "Cr Amount",
            "Cash_Movement",
            "Running_Cash_Balance",
            "Cash Flow Category",
            "Narration",
        ]
    ].copy()

    return cb_display, summary_df


# -----------------------------
# Streamlit UI
# -----------------------------

def main():
    st.set_page_config(page_title="Bank → Journal & Financial Reports", layout="wide")
    st.title("Bank Statement → 4-Leg Journal & Financial Reports")

    st.markdown(
        """
This app converts your bank statement (in your template format) into **4-leg journal entries**
using a **Suspense Account** in between (Option B), then builds:

- 📄 Journal (4-leg)
- 📘 General Ledger
- 📊 Trial Balance (ledger balances only)
- 📈 Income Statement (Revenue, Cost of Sales, Gross Profit, Expenses, Net Profit)
- 📗 Statement of Financial Position (Balance Sheet – template style)
- 💵 IFRS-style Cashflow (Operating, Investing, Financing) + reconciled bank balance
        """
    )

    st.markdown("---")

    # Sidebar settings
    st.sidebar.header("Settings")

    bank_account_name = st.sidebar.text_input(
        "Bank account name",
        value="PROVIDUS BANK - MAIN",
        help="This will appear as the bank account in the journal and cashflow."
    )
    bank_account_type = st.sidebar.text_input(
        "Bank account type",
        value="Current Asset",
        help="Recommended: 'Current Asset'."
    )
    suspense_account_name = st.sidebar.text_input(
        "Suspense account name",
        value="Suspense Account",
        help="This is the clearing account used in the 4-leg postings."
    )
    suspense_account_type = st.sidebar.text_input(
        "Suspense account type",
        value="Other",
        help="Type of the suspense account (e.g. 'Other')."
    )

    opening_bank_balance = st.sidebar.number_input(
        "Opening bank balance for period",
        min_value=-1_000_000_000.0,
        max_value=1_000_000_000.0,
        value=0.0,
        step=1000.0,
        help="Bank balance at the start of the reporting period (for cashflow reconciliation)."
    )

    # Manual closing balance input
    closing_bank_balance_manual = st.sidebar.number_input(
        "Closing bank balance for period (per bank statement)",
        min_value=-1_000_000_000.0,
        max_value=1_000_000_000.0,
        value=0.0,
        step=1000.0,
        help="Closing bank balance from your bank statement for this period."
    )

    st.sidebar.markdown("---")
    st.sidebar.header("CAC / Equity Inputs")

    number_of_shares = st.sidebar.number_input(
        "Number of issued shares",
        min_value=0,
        value=0,
        step=1,
        help="Total issued shares as per CAC."
    )
    nominal_value_per_share = st.sidebar.number_input(
        "Nominal value per share",
        min_value=0.0,
        value=1.0,
        step=0.5,
        help="Par/nominal value per share (e.g. ₦1)."
    )
    share_premium_other = st.sidebar.number_input(
        "Share premium / other CAC equity",
        min_value=0.0,
        value=0.0,
        step=1000.0,
        help="Additional paid-in capital, reserves etc."
    )
    computed_share_capital = number_of_shares * nominal_value_per_share

    # -------------------------
    # Template Download
    # -------------------------
    st.subheader("1️⃣ Download Bank Statement Template")

    template_df = create_template_df()
    csv_buffer = io.StringIO()
    template_df.to_csv(csv_buffer, index=False)
    st.download_button(
        label="Download template (CSV)",
        data=csv_buffer.getvalue(),
        file_name="bank_statement_template.csv",
        mime="text/csv",
        help="Use this structure or copy/paste your Excel bank statement into this format."
    )

    st.markdown("**Template columns:**")
    st.dataframe(template_df.head(0))

    st.markdown("---")

    # -------------------------
    # File Upload
    # -------------------------
    st.subheader("2️⃣ Upload Completed Bank Statement (CSV or Excel)")

    uploaded_file = st.file_uploader(
        "Upload your bank statement file (CSV / XLSX / XLS)",
        type=["csv", "xlsx", "xls"]
    )

    if uploaded_file is not None:
        try:
            df_raw = load_uploaded_file(uploaded_file)
        except Exception as e:
            st.error(f"Error reading file: {e}")
            return

        st.markdown("### 📄 Raw Uploaded Data (first 10 rows)")
        st.dataframe(df_raw.head(10))

        # Normalize columns
        df_norm = normalize_columns(df_raw)

        # -------------------------
        # Date Filter Section - YOU choose the date(s)
        # -------------------------
        st.markdown("---")
        st.subheader("3️⃣ Choose Date(s) for Journal Preparation")

        filtered_df = df_norm

        if "start date" in df_norm.columns:
            parsed_dates = pd.to_datetime(
                df_norm["start date"],
                errors="coerce",
                dayfirst=True
            )

            valid_mask = parsed_dates.notna()
            if valid_mask.any():
                min_date = parsed_dates[valid_mask].min().date()
                max_date = parsed_dates[valid_mask].max().date()

                st.write(f"Detected date range in file: **{min_date}** to **{max_date}**")

                filter_mode = st.radio(
                    "Filter mode",
                    options=["Single date", "Date range"],
                    index=0,
                    help="Choose whether to prepare journal for one specific date or a full range."
                )

                if filter_mode == "Single date":
                    selected_date = st.date_input(
                        "Select the exact date to prepare journal for",
                        value=min_date,
                        min_value=min_date,
                        max_value=max_date,
                        help="Only rows with this exact date will be converted."
                    )
                    mask = parsed_dates.dt.date == selected_date
                    filtered_df = df_norm[mask].copy()

                else:  # "Date range"
                    date_range = st.date_input(
                        "Select date range to include",
                        value=(min_date, max_date),
                        min_value=min_date,
                        max_value=max_date,
                        help="Rows within this date range will be converted."
                    )

                    if isinstance(date_range, tuple) and len(date_range) == 2:
                        start_filter, end_filter = date_range
                        mask = (parsed_dates.dt.date >= start_filter) & (parsed_dates.dt.date <= end_filter)
                        filtered_df = df_norm[mask].copy()
                    else:
                        filtered_df = df_norm.copy()

                st.markdown("#### 🗂 Data to be used for journal (first 10 rows)")
                st.dataframe(filtered_df.head(10))
            else:
                st.warning("Could not parse any valid dates from 'start date' column. Date filter disabled.")
        else:
            st.warning("Column 'start date' not found. Date filter disabled.")

        # -------------------------
        # Generate Journal from filtered data
        # -------------------------
        journal_df = generate_journal_rows(
            filtered_df,
            bank_account_name=bank_account_name,
            bank_account_type=bank_account_type,
            suspense_account_name=suspense_account_name,
            suspense_account_type=suspense_account_type,
        )

        st.markdown("---")
        st.subheader("4️⃣ Reports from Journal")

        if journal_df.empty:
            st.warning("No valid rows found to convert (check DEBIT/CREDIT, dates, and template columns).")
            return

        tab_journal, tab_gl, tab_tb, tab_is, tab_bs, tab_cf = st.tabs(
            ["📄 Journal", "📘 General Ledger", "📊 Trial Balance", "📈 Income Statement", "📗 Statement of Financial Position", "💵 Cashflow (IFRS)"]
        )

        # ------------- Journal Tab -------------
        with tab_journal:
            st.markdown("### Journal (4-leg entries)")
            st.dataframe(journal_df)

            out_csv_buffer = io.StringIO()
            journal_df.to_csv(out_csv_buffer, index=False)
            st.download_button(
                label="Download journal entries (CSV)",
                data=out_csv_buffer.getvalue(),
                file_name="journal_entries_4_leg.csv",
                mime="text/csv",
            )

        # ------------- General Ledger Tab -------------
        with tab_gl:
            st.markdown("### General Ledger (per account, with running balance)")
            gl_df = build_gl_detail(journal_df)

            accounts = ["(All)"] + sorted(gl_df["Account"].dropna().unique().tolist())
            selected_account = st.selectbox("Filter by account", accounts, index=0)

            gl_display = gl_df.copy()
            if selected_account != "(All)":
                gl_display = gl_display[gl_display["Account"] == selected_account]

            st.dataframe(
                gl_display[
                    [
                        "Date",
                        "Transaction / Details",
                        "Leg",
                        "Account Type",
                        "Account",
                        "Dr Amount",
                        "Cr Amount",
                        "Amount_Signed",
                        "Running_Balance",
                        "Narration",
                    ]
                ]
            )

            gl_csv = io.StringIO()
            gl_display.to_csv(gl_csv, index=False)
            st.download_button(
                label="Download GL (CSV)",
                data=gl_csv.getvalue(),
                file_name="general_ledger.csv",
                mime="text/csv",
            )

        # ------------- Trial Balance Tab -------------
        with tab_tb:
            st.markdown("### Trial Balance (ledger balances)")
            tb_df = build_trial_balance(journal_df)
            st.dataframe(tb_df)

            tb_csv = io.StringIO()
            tb_df.to_csv(tb_csv, index=False)
            st.download_button(
                label="Download Trial Balance (CSV)",
                data=tb_csv.getvalue(),
                file_name="trial_balance.csv",
                mime="text/csv",
            )

        # ------------- Income Statement Tab -------------
        with tab_is:
            st.markdown("### Income Statement (Profit or Loss)")

            tb_df = build_trial_balance(journal_df)
            revenue_df, cos_df, expense_df, summary_df = build_income_statement(tb_df)

            st.markdown("#### Revenue Accounts")
            st.dataframe(revenue_df)

            st.markdown("#### Cost of Sales Accounts")
            st.dataframe(cos_df)

            st.markdown("#### Operating Expense Accounts")
            st.dataframe(expense_df)

            st.markdown("#### Summary")
            st.dataframe(summary_df)

            is_csv = io.StringIO()
            summary_df.to_csv(is_csv, index=False)
            st.download_button(
                label="Download Income Statement Summary (CSV)",
                data=is_csv.getvalue(),
                file_name="income_statement_summary.csv",
                mime="text/csv",
            )

        # ------------- Balance Sheet Tab -------------
        with tab_bs:
            st.markdown("### Statement of Financial Position (Balance Sheet)")

            # Build TB and Income Statement (to get Net Income)
            tb_df = build_trial_balance(journal_df)
            _, _, _, is_summary_df = build_income_statement(tb_df)

            # Extract Net Income (Net Profit / Loss)
            net_income = 0.0
            try:
                net_row = is_summary_df[is_summary_df["Line"] == "Net Profit / (Loss)"]
                if not net_row.empty:
                    net_income = float(net_row["Amount"].iloc[0])
            except Exception:
                net_income = 0.0

            # Build BS classification
            bs_dict = build_balance_sheet(tb_df)

            current_assets = bs_dict["current_assets"]
            noncurrent_assets = bs_dict["noncurrent_assets"]
            current_liabilities = bs_dict["current_liabilities"]
            noncurrent_liabilities = bs_dict["noncurrent_liabilities"]
            equity_df = bs_dict["equity"]

            # --- Assets totals (template style) ---
            total_current_assets = current_assets["Balance"].sum() if not current_assets.empty else 0.0
            total_property_equipment = noncurrent_assets["Balance"].sum() if not noncurrent_assets.empty else 0.0
            total_other_assets = 0.0   # can be extended later if you have other asset category
            total_assets = total_current_assets + total_property_equipment + total_other_assets

            # --- Liabilities totals ---
            total_current_liab = current_liabilities["Balance"].sum() if not current_liabilities.empty else 0.0
            total_noncurrent_liab = noncurrent_liabilities["Balance"].sum() if not noncurrent_liabilities.empty else 0.0
            total_liab = total_current_liab + total_noncurrent_liab

            # --- Capital totals (from ledger only + net income) ---
            total_equity_ledger = equity_df["Balance"].sum() if not equity_df.empty else 0.0
            total_capital = total_equity_ledger + net_income

            total_liab_capital = total_liab + total_capital
            difference = total_assets - total_liab_capital

            # --- Build template-style layout like your example ---
            rows = []

            # ASSETS
            rows.append({"Section": "ASSETS", "Account": "", "Amount": ""})

            rows.append({"Section": "Current Assets", "Account": "", "Amount": ""})
            for _, r in current_assets.iterrows():
                rows.append(
                    {
                        "Section": "",
                        "Account": r["Account"],
                        "Amount": r["Balance"],
                    }
                )
            rows.append(
                {"Section": "Total Current Assets", "Account": "", "Amount": total_current_assets}
            )

            rows.append({"Section": "Property and Equipment", "Account": "", "Amount": ""})
            for _, r in noncurrent_assets.iterrows():
                rows.append(
                    {
                        "Section": "",
                        "Account": r["Account"],
                        "Amount": r["Balance"],
                    }
                )
            rows.append(
                {
                    "Section": "Total Property and Equipment",
                    "Account": "",
                    "Amount": total_property_equipment,
                }
            )

            rows.append({"Section": "Other Assets", "Account": "", "Amount": ""})
            # (No detailed other assets yet)
            rows.append({"Section": "Total Other Assets", "Account": "", "Amount": total_other_assets})

            rows.append({"Section": "Total Assets", "Account": "", "Amount": total_assets})

            # Blank line
            rows.append({"Section": "", "Account": "", "Amount": ""})

            # LIABILITIES & CAPITAL
            rows.append({"Section": "LIABILITIES AND CAPITAL", "Account": "", "Amount": ""})

            # Current Liabilities
            rows.append({"Section": "Current Liabilities", "Account": "", "Amount": ""})
            for _, r in current_liabilities.iterrows():
                rows.append(
                    {
                        "Section": "",
                        "Account": r["Account"],
                        "Amount": r["Balance"],
                    }
                )
            rows.append(
                {
                    "Section": "Total Current Liabilities",
                    "Account": "",
                    "Amount": total_current_liab,
                }
            )

            # Long-Term Liabilities (Non-Current)
            rows.append({"Section": "Long-Term Liabilities", "Account": "", "Amount": ""})
            for _, r in noncurrent_liabilities.iterrows():
                rows.append(
                    {
                        "Section": "",
                        "Account": r["Account"],
                        "Amount": r["Balance"],
                    }
                )
            rows.append(
                {
                    "Section": "Total Long-Term Liabilities",
                    "Account": "",
                    "Amount": total_noncurrent_liab,
                }
            )

            rows.append({"Section": "Total Liabilities", "Account": "", "Amount": total_liab})

            # Capital section
            rows.append({"Section": "Capital", "Account": "", "Amount": ""})

            # Equity accounts from ledger (if any)
            for _, r in equity_df.iterrows():
                rows.append(
                    {
                        "Section": "",
                        "Account": r["Account"],
                        "Amount": r["Balance"],
                    }
                )

            # Net Income line
            rows.append(
                {
                    "Section": "",
                    "Account": "Net Income",
                    "Amount": net_income,
                }
            )

            # Total capital (ledger equity + net income)
            rows.append(
                {
                    "Section": "Total Capital",
                    "Account": "",
                    "Amount": total_capital,
                }
            )

            rows.append(
                {
                    "Section": "Total Liabilities & Capital",
                    "Account": "",
                    "Amount": total_liab_capital,
                }
            )

            rows.append(
                {
                    "Section": "Difference (Assets - [Liab + Capital])",
                    "Account": "",
                    "Amount": difference,
                }
            )

            bs_layout_df = pd.DataFrame(rows, columns=["Section", "Account", "Amount"])

            st.dataframe(bs_layout_df)

            # Download button for template-style balance sheet
            bs_layout_csv = io.StringIO()
            bs_layout_df.to_csv(bs_layout_csv, index=False)
            st.download_button(
                label="Download Balance Sheet (template style, CSV)",
                data=bs_layout_csv.getvalue(),
                file_name="balance_sheet_template_style.csv",
                mime="text/csv",
            )

            # CAC / Equity inputs summary + suggested journal
            st.markdown("#### CAC / Equity Inputs Summary")
            eq_rows = [
                {"Line": "Number of shares", "Amount": number_of_shares},
                {"Line": "Nominal value per share", "Amount": nominal_value_per_share},
                {"Line": "Computed Share Capital (CAC)", "Amount": computed_share_capital},
                {"Line": "Share Premium / Other CAC Equity", "Amount": share_premium_other},
                {"Line": "Net Income (from Income Statement)", "Amount": net_income},
                {"Line": "Ledger Equity balances (sum of equity accounts)", "Amount": total_equity_ledger},
            ]
            eq_df = pd.DataFrame(eq_rows)
            st.dataframe(eq_df)

            st.markdown("#### Suggested CAC Opening Equity Journal (for your main books)")
            journal_suggestions = []

            if computed_share_capital > 0:
                journal_suggestions.append(
                    {
                        "Dr / Cr": "Dr",
                        "Account": "Retained Earnings / Capital Introduced",
                        "Amount": computed_share_capital,
                        "Narration": "To recognise issued share capital as per CAC"
                    }
                )
                journal_suggestions.append(
                    {
                        "Dr / Cr": "Cr",
                        "Account": "Share Capital",
                        "Amount": computed_share_capital,
                        "Narration": "To recognise issued share capital as per CAC"
                    }
                )

            if share_premium_other > 0:
                journal_suggestions.append(
                    {
                        "Dr / Cr": "Dr",
                        "Account": "Retained Earnings / Capital Introduced",
                        "Amount": share_premium_other,
                        "Narration": "To recognise share premium / other equity as per CAC"
                    }
                )
                journal_suggestions.append(
                    {
                        "Dr / Cr": "Cr",
                        "Account": "Share Premium / Other CAC Equity",
                        "Amount": share_premium_other,
                        "Narration": "To recognise share premium / other equity as per CAC"
                    }
                )

            if journal_suggestions:
                sug_df = pd.DataFrame(journal_suggestions)
                st.dataframe(sug_df)

                sug_csv = io.StringIO()
                sug_df.to_csv(sug_csv, index=False)
                st.download_button(
                    label="Download suggested CAC journal (CSV)",
                    data=sug_csv.getvalue(),
                    file_name="cac_opening_equity_journal.csv",
                    mime="text/csv",
                )
            else:
                st.info("No CAC amounts entered yet, so no suggested CAC journal.")

        # ------------- Cashflow (IFRS) Tab -------------
        with tab_cf:
            st.markdown(f"### IFRS-style Cashflow for {bank_account_name}")

            cashbook_df, cf_summary = build_cashbook_ifrs(
                journal_df,
                bank_account_name=bank_account_name,
                opening_balance=opening_bank_balance
            )

            if cashbook_df.empty:
                st.warning(f"No entries found for bank account '{bank_account_name}'. Check the account name in settings.")
            else:
                st.markdown("#### Detailed Bank Movements (with Cash Flow Category)")
                st.dataframe(cashbook_df)

                cf_csv = io.StringIO()
                cashbook_df.to_csv(cf_csv, index=False)
                st.download_button(
                    label="Download Cashbook (CSV)",
                    data=cf_csv.getvalue(),
                    file_name="cashbook_cashflow_ifrs.csv",
                    mime="text/csv",
                )

                # --- Use running ledger closing balance for reconciliation ---
                closing_calc = None
                try:
                    closing_calc_row = cf_summary[cf_summary["Line"] == "Closing bank balance (from running ledger)"]
                    if not closing_calc_row.empty:
                        closing_calc = float(closing_calc_row["Amount"].iloc[0])
                except Exception:
                    closing_calc = None

                # Build display summary including manual closing and difference vs manual
                cf_summary_display = cf_summary.copy()

                # Add manual closing balance row
                cf_summary_display = pd.concat(
                    [
                        cf_summary_display,
                        pd.DataFrame(
                            [
                                {
                                    "Line": "Closing bank balance (per bank statement/manual input)",
                                    "Amount": closing_bank_balance_manual,
                                }
                            ]
                        ),
                    ],
                    ignore_index=True,
                )

                # Add difference vs manual closing, if we have a computed closing
                if closing_calc is not None:
                    difference_manual = closing_calc - closing_bank_balance_manual
                    cf_summary_display = pd.concat(
                        [
                            cf_summary_display,
                            pd.DataFrame(
                                [
                                    {
                                        "Line": "Difference vs manual closing (computed - manual)",
                                        "Amount": difference_manual,
                                    }
                                ]
                            ),
                        ],
                        ignore_index=True,
                    )

                    if abs(difference_manual) < 0.01:
                        st.success(
                            f"✅ Computed closing bank balance matches the manual closing balance you entered "
                            f"(difference {difference_manual:,.2f})."
                        )
                    else:
                        st.error(
                            f"❌ Computed closing bank balance ({closing_calc:,.2f}) does NOT match "
                            f"manual closing balance ({closing_bank_balance_manual:,.2f}). "
                            f"Difference: {difference_manual:,.2f}."
                        )
                else:
                    st.info(
                        "Could not determine computed closing bank balance for comparison. "
                        "Check that the cashflow summary contains the closing balance line."
                    )

                st.markdown("#### Cashflow Summary (Operating / Investing / Financing)")
                st.dataframe(cf_summary_display)

                summary_csv = io.StringIO()
                cf_summary_display.to_csv(summary_csv, index=False)
                st.download_button(
                    label="Download Cashflow Summary (CSV)",
                    data=summary_csv.getvalue(),
                    file_name="cashflow_summary_ifrs.csv",
                    mime="text/csv",
                )


if __name__ == "__main__":
    main()
