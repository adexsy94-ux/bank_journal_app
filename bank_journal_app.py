import io
import re
from typing import List, Dict, Tuple, Any

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


def _clean_amount_text(x: Any) -> str:
    """
    Normalize common bank statement amount formats so they can be parsed reliably.

    Handles:
    - Currency symbols: ₦, NGN, etc.
    - Thousands separators: commas
    - Parentheses for negatives: (1,234.00)
    - DR/CR suffix/prefix: 1000DR, CR1000
    - Trailing minus: 1000-
    - Non-breaking spaces
    """
    if x is None:
        return ""
    s = str(x)

    # Normalize spaces (including NBSP)
    s = s.replace("\u00A0", " ").strip()

    if s == "" or s.lower() in {"nan", "none", "null"}:
        return ""

    # Remove currency words/symbols
    s = s.replace("₦", "")
    s = re.sub(r"\bNGN\b", "", s, flags=re.IGNORECASE)

    # Remove spaces
    s = s.replace(" ", "")

    # Detect parentheses negative
    is_paren_negative = s.startswith("(") and s.endswith(")")
    if is_paren_negative:
        s = s[1:-1]

    # Detect DR/CR markers (we don't use them for sign; we just strip them)
    s = re.sub(r"^(DR|CR)", "", s, flags=re.IGNORECASE)
    s = re.sub(r"(DR|CR)$", "", s, flags=re.IGNORECASE)

    # Trailing minus e.g. 1000-
    is_trailing_minus = s.endswith("-")
    if is_trailing_minus:
        s = s[:-1]

    # Remove commas
    s = s.replace(",", "")

    # Keep only valid numeric chars: digits and dot and minus
    s = re.sub(r"[^0-9.\-]", "", s)

    # Apply negative if parentheses or trailing minus
    if is_paren_negative or is_trailing_minus:
        if s and not s.startswith("-"):
            s = "-" + s

    return s


def parse_amount_cell(x: Any) -> Tuple[float, bool]:
    """
    Parse a single cell into float.
    Returns (value, is_valid).
    - is_valid False means it had content but could not be parsed.
    """
    raw = "" if x is None else str(x)
    raw_stripped = raw.replace("\u00A0", " ").strip()
    if raw_stripped == "" or raw_stripped.lower() in {"nan", "none", "null"}:
        return 0.0, True  # blank is fine

    cleaned = _clean_amount_text(x)
    if cleaned == "" or cleaned == "-" or cleaned == ".":
        return 0.0, False

    try:
        val = float(cleaned)
        return val, True
    except Exception:
        return 0.0, False


def series_to_number_with_audit(series: pd.Series, col_name: str) -> Tuple[pd.Series, pd.DataFrame]:
    """
    Convert a series to numeric with robust parsing and return an audit table
    for rows that failed parsing.
    """
    values = []
    bad_rows = []

    for idx, x in series.items():
        val, ok = parse_amount_cell(x)
        values.append(val)
        if not ok:
            bad_rows.append({"RowIndex": idx, "Column": col_name, "Original": x})

    out = pd.Series(values, index=series.index, dtype="float64")
    bad_df = pd.DataFrame(bad_rows)
    return out, bad_df


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

    investing_keywords = [
        "non current asset", "non-current asset", "noncurrent asset",
        "non current assets", "non-current assets", "noncurrent assets",
        "fixed asset", "fixed assets", "investment property", "investment"
    ]
    if any(k in t for k in investing_keywords):
        return "Investing"

    financing_keywords = [
        "equity", "share capital", "capital", "retained earnings", "reserves",
        "non current liability", "non-current liability", "noncurrent liability",
        "non current liabilities", "non-current liabilities", "noncurrent liabilities",
        "long term liability", "long-term liability", "long term liabilities", "long-term liabilities",
        "loan", "borrowings"
    ]
    if any(k in t for k in financing_keywords):
        return "Financing"

    return "Operating"


def generate_journal_rows_with_audit(
    df: pd.DataFrame,
    bank_account_name: str,
    bank_account_type: str,
    suspense_account_name: str,
    suspense_account_type: str
) -> Tuple[pd.DataFrame, Dict[str, Any], pd.DataFrame]:
    """
    Same journal generation, but returns:
    - journal_df
    - audit summary dict
    - skipped/problem rows dataframe (with reasons)

    Critical: does NOT silently drop rows without reporting why.
    """
    rows: List[Dict] = []
    skipped: List[Dict] = []

    col_account_type = "account type"
    col_account = "account"
    col_date = "start date"
    col_details = "details"
    col_debit = "debit"
    col_credit = "credit"

    df = df.copy()

    # Ensure columns exist
    if col_debit not in df.columns:
        df[col_debit] = ""
    if col_credit not in df.columns:
        df[col_credit] = ""

    # Parse with audit
    df["_debit_num"], bad_debit = series_to_number_with_audit(df[col_debit], "debit")
    df["_credit_num"], bad_credit = series_to_number_with_audit(df[col_credit], "credit")

    bad_amounts = pd.concat([bad_debit, bad_credit], ignore_index=True) if (not bad_debit.empty or not bad_credit.empty) else pd.DataFrame()

    total_rows = len(df)
    processed_rows = 0
    skipped_rows = 0

    skip_reason_counts = {
        "ZERO_AMOUNT": 0,
        "BOTH_DEBIT_AND_CREDIT_FILLED": 0,
        "AMOUNT_PARSE_ERROR": 0,
        "MIXED_OR_INCONSISTENT": 0,
    }

    for idx, row in df.iterrows():
        account_type = str(row.get(col_account_type, "")).strip()
        account_name = str(row.get(col_account, "")).strip()
        date_val = row.get(col_date, "")
        details = str(row.get(col_details, "")).strip()

        debit_raw = row.get(col_debit, "")
        credit_raw = row.get(col_credit, "")

        debit = float(row.get("_debit_num", 0.0) or 0.0)
        credit = float(row.get("_credit_num", 0.0) or 0.0)

        # Check whether original had content but parsed to 0 due to error
        debit_ok = True
        credit_ok = True
        # If it appears in bad_amounts for that idx/column, it failed parsing
        if not bad_amounts.empty:
            if ((bad_amounts["RowIndex"] == idx) & (bad_amounts["Column"] == "debit")).any():
                debit_ok = False
            if ((bad_amounts["RowIndex"] == idx) & (bad_amounts["Column"] == "credit")).any():
                credit_ok = False

        if not debit_ok or not credit_ok:
            skipped_rows += 1
            skip_reason_counts["AMOUNT_PARSE_ERROR"] += 1
            skipped.append({
                "RowIndex": idx,
                "Reason": "AMOUNT_PARSE_ERROR",
                "start date": date_val,
                "details": details,
                "debit_raw": debit_raw,
                "credit_raw": credit_raw,
                "debit_parsed": debit,
                "credit_parsed": credit,
            })
            continue

        if debit == 0 and credit == 0:
            skipped_rows += 1
            skip_reason_counts["ZERO_AMOUNT"] += 1
            skipped.append({
                "RowIndex": idx,
                "Reason": "ZERO_AMOUNT",
                "start date": date_val,
                "details": details,
                "debit_raw": debit_raw,
                "credit_raw": credit_raw,
                "debit_parsed": debit,
                "credit_parsed": credit,
            })
            continue

        # If both sides are filled, don't silently skip—report it
        if debit > 0 and credit > 0:
            skipped_rows += 1
            skip_reason_counts["BOTH_DEBIT_AND_CREDIT_FILLED"] += 1
            skipped.append({
                "RowIndex": idx,
                "Reason": "BOTH_DEBIT_AND_CREDIT_FILLED",
                "start date": date_val,
                "details": details,
                "debit_raw": debit_raw,
                "credit_raw": credit_raw,
                "debit_parsed": debit,
                "credit_parsed": credit,
            })
            continue

        # Decide direction
        if debit > 0 and credit == 0:
            direction = "out"
            amount = debit
        elif credit > 0 and debit == 0:
            direction = "in"
            amount = credit
        else:
            skipped_rows += 1
            skip_reason_counts["MIXED_OR_INCONSISTENT"] += 1
            skipped.append({
                "RowIndex": idx,
                "Reason": "MIXED_OR_INCONSISTENT",
                "start date": date_val,
                "details": details,
                "debit_raw": debit_raw,
                "credit_raw": credit_raw,
                "debit_parsed": debit,
                "credit_parsed": credit,
            })
            continue

        base_narration = details if details else "Bank transaction"
        short_narr = base_narration[:80]

        main_type_for_cf = account_type if account_type else ("Expense" if direction == "out" else "Income")
        cf_category = classify_cash_flow_type(main_type_for_cf)

        if direction == "out":
            # Leg 1: Dr Main
            rows.append({
                "Date": date_val,
                "Transaction / Details": base_narration,
                "Leg": 1,
                "Account Type": account_type if account_type else "Expense",
                "Account": account_name if account_name else "Main Account",
                "Dr Amount": amount,
                "Cr Amount": 0.0,
                "Narration": f"Record {account_name or 'expense'} for {short_narr}",
                "Cash Flow Category": "",
            })
            # Leg 2: Cr Suspense
            rows.append({
                "Date": date_val,
                "Transaction / Details": base_narration,
                "Leg": 2,
                "Account Type": suspense_account_type,
                "Account": suspense_account_name,
                "Dr Amount": 0.0,
                "Cr Amount": amount,
                "Narration": "Temporary posting of payment to suspense",
                "Cash Flow Category": "",
            })
            # Leg 3: Dr Suspense
            rows.append({
                "Date": date_val,
                "Transaction / Details": base_narration,
                "Leg": 3,
                "Account Type": suspense_account_type,
                "Account": suspense_account_name,
                "Dr Amount": amount,
                "Cr Amount": 0.0,
                "Narration": "Clear suspense against bank movement",
                "Cash Flow Category": "",
            })
            # Leg 4: Cr Bank
            rows.append({
                "Date": date_val,
                "Transaction / Details": base_narration,
                "Leg": 4,
                "Account Type": bank_account_type,
                "Account": bank_account_name,
                "Dr Amount": 0.0,
                "Cr Amount": amount,
                "Narration": f"Bank payment for {short_narr}",
                "Cash Flow Category": cf_category,
            })
        else:
            # Leg 1: Dr Suspense
            rows.append({
                "Date": date_val,
                "Transaction / Details": base_narration,
                "Leg": 1,
                "Account Type": suspense_account_type,
                "Account": suspense_account_name,
                "Dr Amount": amount,
                "Cr Amount": 0.0,
                "Narration": "Temporary posting of receipt to suspense",
                "Cash Flow Category": "",
            })
            # Leg 2: Cr Main
            rows.append({
                "Date": date_val,
                "Transaction / Details": base_narration,
                "Leg": 2,
                "Account Type": account_type if account_type else "Income",
                "Account": account_name if account_name else "Main Account",
                "Dr Amount": 0.0,
                "Cr Amount": amount,
                "Narration": f"Record {account_name or 'income'} for {short_narr}",
                "Cash Flow Category": "",
            })
            # Leg 3: Dr Bank
            rows.append({
                "Date": date_val,
                "Transaction / Details": base_narration,
                "Leg": 3,
                "Account Type": bank_account_type,
                "Account": bank_account_name,
                "Dr Amount": amount,
                "Cr Amount": 0.0,
                "Narration": "Bank receipt from suspense",
                "Cash Flow Category": cf_category,
            })
            # Leg 4: Cr Suspense
            rows.append({
                "Date": date_val,
                "Transaction / Details": base_narration,
                "Leg": 4,
                "Account Type": suspense_account_type,
                "Account": suspense_account_name,
                "Dr Amount": 0.0,
                "Cr Amount": amount,
                "Narration": "Clear suspense after bank receipt",
                "Cash Flow Category": "",
            })

        processed_rows += 1

    journal_df = pd.DataFrame(rows)
    if not journal_df.empty:
        journal_df = journal_df.sort_values(by=["Date", "Leg"]).reset_index(drop=True)

    skipped_df = pd.DataFrame(skipped)

    audit = {
        "total_statement_rows": total_rows,
        "processed_statement_rows": processed_rows,
        "skipped_statement_rows": skipped_rows,
        "skip_reason_counts": skip_reason_counts,
        "amount_parse_errors_count": int(skip_reason_counts["AMOUNT_PARSE_ERROR"]),
    }

    return journal_df, audit, skipped_df


def load_uploaded_file(uploaded_file) -> pd.DataFrame:
    """
    Robust reader for CSV / Excel uploaded via Streamlit.
    - Excel: use pandas.read_excel directly.
    - CSV: try several encodings to avoid 'utf-8' codec errors.
    """
    file_name = uploaded_file.name.lower()

    if file_name.endswith(".xlsx") or file_name.endswith(".xls"):
        uploaded_file.seek(0)
        return pd.read_excel(uploaded_file)

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

    raise RuntimeError(f"Could not decode file with tried encodings {encodings_to_try}. Last error: {last_error}")


# -----------------------------
# Reporting helpers (from journal)
# -----------------------------

def ensure_journal_dates(journal_df: pd.DataFrame) -> pd.DataFrame:
    df = journal_df.copy()
    df["Date_dt"] = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True)
    return df


def build_trial_balance(journal_df: pd.DataFrame) -> pd.DataFrame:
    tb_raw = (
        journal_df
        .groupby(["Account Type", "Account"], as_index=False)
        .agg(
            Total_Debit=("Dr Amount", "sum"),
            Total_Credit=("Cr Amount", "sum"),
        )
    )

    tb_raw["Net"] = tb_raw["Total_Debit"] - tb_raw["Total_Credit"]
    tb_raw["Debit"] = tb_raw["Net"].apply(lambda x: x if x > 0 else 0.0)
    tb_raw["Credit"] = tb_raw["Net"].apply(lambda x: -x if x < 0 else 0.0)

    tb = tb_raw[["Account Type", "Account", "Debit", "Credit"]].copy()

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
    df = ensure_journal_dates(journal_df)
    df = df.sort_values(["Account", "Date_dt", "Leg"]).copy()
    df["Amount_Signed"] = df["Dr Amount"] - df["Cr Amount"]
    df["Running_Balance"] = df.groupby("Account")["Amount_Signed"].cumsum()
    return df


def build_income_statement(tb_with_totals: pd.DataFrame):
    tb = tb_with_totals[tb_with_totals["Account Type"] != "TOTAL"].copy()

    tb["NetImpact"] = tb["Credit"] - tb["Debit"]
    tb["Account Type Lower"] = tb["Account Type"].astype(str).str.lower()

    cos_mask = tb["Account Type Lower"].str.contains("cost of sales|cogs|cost of goods sold")
    revenue_mask_raw = tb["Account Type Lower"].str.contains("income|revenue|sales")
    revenue_mask = revenue_mask_raw & ~cos_mask
    expense_mask = tb["Account Type Lower"].str.contains("expense")

    revenue_df = tb[revenue_mask].copy()
    cos_df = tb[cos_mask].copy()
    expense_df = tb[expense_mask].copy()

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
    tb = tb_with_totals[tb_with_totals["Account Type"] != "TOTAL"].copy()
    tb["Account Type Lower"] = tb["Account Type"].astype(str).str.lower()

    def classify(row):
        t = row["Account Type Lower"]
        if "asset" in t:
            if any(k in t for k in ["non current", "non-current", "noncurrent", "fixed"]):
                return "Non-Current Asset"
            else:
                return "Current Asset"
        if "liab" in t:
            if any(k in t for k in ["non current", "non-current", "noncurrent", "long term", "long-term"]):
                return "Non-Current Liability"
            else:
                return "Current Liability"
        if any(k in t for k in ["equity", "capital", "retained", "reserve", "share capital"]):
            return "Equity"
        return "Other"

    tb["BS_Category"] = tb.apply(classify, axis=1)

    def bs_balance(row):
        if row["BS_Category"] in ["Current Asset", "Non-Current Asset"]:
            return row["Debit"] - row["Credit"]
        elif row["BS_Category"] in ["Current Liability", "Non-Current Liability", "Equity"]:
            return row["Credit"] - row["Debit"]
        else:
            return row["Credit"] - row["Debit"]

    tb["Balance"] = tb.apply(bs_balance, axis=1)

    return {
        "current_assets": tb[tb["BS_Category"] == "Current Asset"].copy(),
        "noncurrent_assets": tb[tb["BS_Category"] == "Non-Current Asset"].copy(),
        "current_liabilities": tb[tb["BS_Category"] == "Current Liability"].copy(),
        "noncurrent_liabilities": tb[tb["BS_Category"] == "Non-Current Liability"].copy(),
        "equity": tb[tb["BS_Category"] == "Equity"].copy(),
        "other": tb[tb["BS_Category"] == "Other"].copy(),
    }


def append_opening_balance_journal(
    journal_df: pd.DataFrame,
    bank_account_name: str,
    bank_account_type: str,
    opening_balance: float
) -> pd.DataFrame:
    if journal_df.empty or abs(opening_balance) < 1e-9:
        return journal_df

    df_dates = ensure_journal_dates(journal_df)
    valid_dates = df_dates["Date_dt"].dropna()

    if valid_dates.empty:
        return journal_df

    earliest_dt = valid_dates.min()
    opening_date_value = earliest_dt

    offset_account_type = "Equity"
    offset_account_name = "Opening Balance Offset"

    rows: List[Dict] = []

    if opening_balance > 0:
        rows.append({
            "Date": opening_date_value,
            "Transaction / Details": "Opening bank balance",
            "Leg": 0,
            "Account Type": bank_account_type,
            "Account": bank_account_name,
            "Dr Amount": opening_balance,
            "Cr Amount": 0.0,
            "Narration": "Opening bank balance brought forward",
            "Cash Flow Category": "Opening Balance",
        })
        rows.append({
            "Date": opening_date_value,
            "Transaction / Details": "Opening bank balance",
            "Leg": 0,
            "Account Type": offset_account_type,
            "Account": offset_account_name,
            "Dr Amount": 0.0,
            "Cr Amount": opening_balance,
            "Narration": "Opening bank balance counterpart",
            "Cash Flow Category": "Opening Balance",
        })
    else:
        obal = abs(opening_balance)
        rows.append({
            "Date": opening_date_value,
            "Transaction / Details": "Opening bank balance (overdraft)",
            "Leg": 0,
            "Account Type": offset_account_type,
            "Account": offset_account_name,
            "Dr Amount": obal,
            "Cr Amount": 0.0,
            "Narration": "Opening overdraft counterpart",
            "Cash Flow Category": "Opening Balance",
        })
        rows.append({
            "Date": opening_date_value,
            "Transaction / Details": "Opening bank balance (overdraft)",
            "Leg": 0,
            "Account Type": bank_account_type,
            "Account": bank_account_name,
            "Dr Amount": 0.0,
            "Cr Amount": obal,
            "Narration": "Opening overdraft brought forward",
            "Cash Flow Category": "Opening Balance",
        })

    opening_df = pd.DataFrame(rows)
    combined = pd.concat([opening_df, journal_df], ignore_index=True)
    combined = combined.sort_values(by=["Date", "Leg"]).reset_index(drop=True)
    return combined


def build_cashbook_ifrs(
    journal_df: pd.DataFrame,
    bank_account_name: str,
    opening_balance: float  # kept in signature; opening comes from journal entry
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    df = ensure_journal_dates(journal_df)

    cb = df[df["Account"] == bank_account_name].copy()
    cb = cb.sort_values(["Date_dt", "Leg"])

    if cb.empty:
        return cb, pd.DataFrame()

    cb["Cash_Movement"] = cb["Dr Amount"] - cb["Cr Amount"]

    if "Cash Flow Category" in cb.columns:
        cf_cat = cb["Cash Flow Category"].fillna("")
        cf_cat = cf_cat.replace("", "Operating")
        cb["Cash Flow Category"] = cf_cat
    else:
        cb["Cash Flow Category"] = "Operating"

    cb["Running_Cash_Balance"] = cb["Cash_Movement"].cumsum()

    opening_journal_balance = cb.loc[
        cb["Cash Flow Category"] == "Opening Balance", "Cash_Movement"
    ].sum()

    operating_total = cb.loc[cb["Cash Flow Category"] == "Operating", "Cash_Movement"].sum()
    investing_total = cb.loc[cb["Cash Flow Category"] == "Investing", "Cash_Movement"].sum()
    financing_total = cb.loc[cb["Cash Flow Category"] == "Financing", "Cash_Movement"].sum()

    net_change = operating_total + investing_total + financing_total
    closing_balance_calc = opening_journal_balance + net_change

    closing_balance_running = cb["Running_Cash_Balance"].iloc[-1]
    diff = closing_balance_calc - closing_balance_running

    summary_rows = [
        {"Line": "Net cash from Operating activities", "Amount": operating_total},
        {"Line": "Net cash from Investing activities", "Amount": investing_total},
        {"Line": "Net cash from Financing activities", "Amount": financing_total},
        {"Line": "Net increase / (decrease) in cash", "Amount": net_change},
        {"Line": "Opening bank balance (per journal Opening Balance entry)", "Amount": opening_journal_balance},
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

✅ This version includes a **Processing Audit** so you can see how many statement lines were processed and why any were skipped.
        """
    )

    st.markdown("---")

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
        "Opening bank balance for period (per bank statement)",
        min_value=-1_000_000_000.0,
        max_value=1_000_000_000.0,
        value=0.0,
        step=1000.0,
        help="Bank balance at the start of the reporting period. This will be posted into the journal."
    )

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

    if uploaded_file is None:
        return

    try:
        df_raw = load_uploaded_file(uploaded_file)
    except Exception as e:
        st.error(f"Error reading file: {e}")
        return

    st.markdown("### 📄 Raw Uploaded Data (first 10 rows)")
    st.dataframe(df_raw.head(10))

    df_norm = normalize_columns(df_raw)

    # Hard date validation
    if "start date" in df_norm.columns:
        original_dates = df_norm["start date"]
        parsed_dates_full = pd.to_datetime(original_dates, errors="coerce", dayfirst=True)

        non_empty_mask = original_dates.astype(str).str.replace("\u00A0", " ").str.strip() != ""
        invalid_mask = non_empty_mask & parsed_dates_full.isna()

        if invalid_mask.any():
            invalid_rows = df_norm.loc[invalid_mask, ["start date"]].copy()
            invalid_rows["Row_Number_in_File"] = invalid_rows.index + 2
            st.error(
                "⚠️ Some rows have invalid dates in the 'start date' column. "
                "Please correct them in your Excel/CSV and re-upload."
            )
            st.markdown("Below are the first few problematic rows (Excel row number & value):")
            st.dataframe(invalid_rows[["Row_Number_in_File", "start date"]].head(50))
            st.stop()

    st.markdown("---")
    st.subheader("3️⃣ Choose Date(s) for Journal Preparation")

    filtered_df = df_norm

    if "start date" in df_norm.columns:
        parsed_dates = pd.to_datetime(df_norm["start date"], errors="coerce", dayfirst=True)
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
            else:
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
    # Processing Audit (BEFORE journal)
    # -------------------------
    st.markdown("---")
    st.subheader("✅ Processing Audit (How many lines were processed?)")

    # Compute filtered totals (using same robust parsing)
    if "debit" in filtered_df.columns:
        debit_nums, debit_bad = series_to_number_with_audit(filtered_df["debit"], "debit")
    else:
        debit_nums, debit_bad = pd.Series([0.0] * len(filtered_df), index=filtered_df.index), pd.DataFrame()

    if "credit" in filtered_df.columns:
        credit_nums, credit_bad = series_to_number_with_audit(filtered_df["credit"], "credit")
    else:
        credit_nums, credit_bad = pd.Series([0.0] * len(filtered_df), index=filtered_df.index), pd.DataFrame()

    filtered_total_debit = float(debit_nums.sum() or 0.0)
    filtered_total_credit = float(credit_nums.sum() or 0.0)
    filtered_net = filtered_total_credit - filtered_total_debit

    audit_top = pd.DataFrame([
        {"Metric": "Filtered statement rows", "Value": len(filtered_df)},
        {"Metric": "Filtered total debit (parsed)", "Value": filtered_total_debit},
        {"Metric": "Filtered total credit (parsed)", "Value": filtered_total_credit},
        {"Metric": "Filtered net (credit - debit)", "Value": filtered_net},
    ])
    st.dataframe(audit_top)

    bad_any = pd.concat([debit_bad, credit_bad], ignore_index=True) if (not debit_bad.empty or not credit_bad.empty) else pd.DataFrame()
    if not bad_any.empty:
        st.error("⚠️ Some debit/credit cells could NOT be parsed into numbers. Those rows can cause missing cashflow totals.")
        bad_any = bad_any.copy()
        bad_any["Row_Number_in_File"] = bad_any["RowIndex"] + 2
        st.dataframe(bad_any[["Row_Number_in_File", "Column", "Original"]].head(100))

        bad_csv = io.StringIO()
        bad_any.to_csv(bad_csv, index=False)
        st.download_button(
            label="Download amount-parse errors (CSV)",
            data=bad_csv.getvalue(),
            file_name="amount_parse_errors.csv",
            mime="text/csv",
        )

    # -------------------------
    # Generate Journal (WITH audit)
    # -------------------------
    journal_df, journal_audit, skipped_df = generate_journal_rows_with_audit(
        filtered_df,
        bank_account_name=bank_account_name,
        bank_account_type=bank_account_type,
        suspense_account_name=suspense_account_name,
        suspense_account_type=suspense_account_type,
    )

    # Append opening balance
    journal_df = append_opening_balance_journal(
        journal_df,
        bank_account_name=bank_account_name,
        bank_account_type=bank_account_type,
        opening_balance=opening_bank_balance,
    )

    # Show journal audit summary
    audit_summary_rows = [
        {"Metric": "Statement rows in filtered period", "Value": journal_audit["total_statement_rows"]},
        {"Metric": "Statement rows processed into journals", "Value": journal_audit["processed_statement_rows"]},
        {"Metric": "Statement rows skipped", "Value": journal_audit["skipped_statement_rows"]},
        {"Metric": "Skipped: ZERO_AMOUNT", "Value": journal_audit["skip_reason_counts"]["ZERO_AMOUNT"]},
        {"Metric": "Skipped: BOTH_DEBIT_AND_CREDIT_FILLED", "Value": journal_audit["skip_reason_counts"]["BOTH_DEBIT_AND_CREDIT_FILLED"]},
        {"Metric": "Skipped: AMOUNT_PARSE_ERROR", "Value": journal_audit["skip_reason_counts"]["AMOUNT_PARSE_ERROR"]},
        {"Metric": "Skipped: MIXED_OR_INCONSISTENT", "Value": journal_audit["skip_reason_counts"]["MIXED_OR_INCONSISTENT"]},
    ]
    st.dataframe(pd.DataFrame(audit_summary_rows))

    if not skipped_df.empty:
        st.warning("Some statement rows were skipped. Review below (this is often the exact reason for cashflow differences).")
        show_cols = [c for c in ["RowIndex", "Reason", "start date", "details", "debit_raw", "credit_raw", "debit_parsed", "credit_parsed"] if c in skipped_df.columns]
        view_df = skipped_df.copy()
        view_df["Row_Number_in_File"] = view_df["RowIndex"] + 2
        cols = ["Row_Number_in_File"] + [c for c in show_cols if c != "RowIndex"]
        st.dataframe(view_df[cols].head(200))

        skip_csv = io.StringIO()
        view_df.to_csv(skip_csv, index=False)
        st.download_button(
            label="Download skipped rows report (CSV)",
            data=skip_csv.getvalue(),
            file_name="skipped_rows_report.csv",
            mime="text/csv",
        )
    else:
        st.success("✅ No statement rows were skipped by the journal processor.")

    st.markdown("---")
    st.subheader("4️⃣ Reports from Journal")

    if journal_df.empty:
        st.warning("No valid rows found to convert (check DEBIT/CREDIT, dates, and template columns).")
        return

    # Precompute shared reports
    gl_df_all = build_gl_detail(journal_df)
    tb_df_all = build_trial_balance(journal_df)
    revenue_df_all, cos_df_all, expense_df_all, is_summary_df_all = build_income_statement(tb_df_all)
    bs_dict_all = build_balance_sheet(tb_df_all)
    cashbook_df_all, cf_summary_all = build_cashbook_ifrs(
        journal_df,
        bank_account_name=bank_account_name,
        opening_balance=opening_bank_balance
    )

    # Balance Sheet layout build
    current_assets_all = bs_dict_all["current_assets"]
    noncurrent_assets_all = bs_dict_all["noncurrent_assets"]
    current_liabilities_all = bs_dict_all["current_liabilities"]
    noncurrent_liabilities_all = bs_dict_all["noncurrent_liabilities"]
    equity_df_all = bs_dict_all["equity"]

    total_current_assets_all = current_assets_all["Balance"].sum() if not current_assets_all.empty else 0.0
    total_property_equipment_all = noncurrent_assets_all["Balance"].sum() if not noncurrent_assets_all.empty else 0.0
    total_other_assets_all = 0.0
    total_assets_all = total_current_assets_all + total_property_equipment_all + total_other_assets_all

    total_current_liab_all = current_liabilities_all["Balance"].sum() if not current_liabilities_all.empty else 0.0
    total_noncurrent_liab_all = noncurrent_liabilities_all["Balance"].sum() if not noncurrent_liabilities_all.empty else 0.0
    total_liab_all = total_current_liab_all + total_noncurrent_liab_all

    net_income_all = 0.0
    try:
        net_row_all = is_summary_df_all[is_summary_df_all["Line"] == "Net Profit / (Loss)"]
        if not net_row_all.empty:
            net_income_all = float(net_row_all["Amount"].iloc[0])
    except Exception:
        net_income_all = 0.0

    total_equity_ledger_all = equity_df_all["Balance"].sum() if not equity_df_all.empty else 0.0
    total_capital_all = total_equity_ledger_all + net_income_all
    total_liab_capital_all = total_liab_all + total_capital_all
    difference_all = total_assets_all - total_liab_capital_all

    bs_rows_all = []
    bs_rows_all.append({"Section": "ASSETS", "Account": "", "Amount": ""})
    bs_rows_all.append({"Section": "Current Assets", "Account": "", "Amount": ""})
    for _, r in current_assets_all.iterrows():
        bs_rows_all.append({"Section": "", "Account": r["Account"], "Amount": r["Balance"]})
    bs_rows_all.append({"Section": "Total Current Assets", "Account": "", "Amount": total_current_assets_all})

    bs_rows_all.append({"Section": "Property and Equipment", "Account": "", "Amount": ""})
    for _, r in noncurrent_assets_all.iterrows():
        bs_rows_all.append({"Section": "", "Account": r["Account"], "Amount": r["Balance"]})
    bs_rows_all.append({"Section": "Total Property and Equipment", "Account": "", "Amount": total_property_equipment_all})

    bs_rows_all.append({"Section": "Other Assets", "Account": "", "Amount": ""})
    bs_rows_all.append({"Section": "Total Other Assets", "Account": "", "Amount": total_other_assets_all})
    bs_rows_all.append({"Section": "Total Assets", "Account": "", "Amount": total_assets_all})

    bs_rows_all.append({"Section": "", "Account": "", "Amount": ""})
    bs_rows_all.append({"Section": "LIABILITIES AND CAPITAL", "Account": "", "Amount": ""})

    bs_rows_all.append({"Section": "Current Liabilities", "Account": "", "Amount": ""})
    for _, r in current_liabilities_all.iterrows():
        bs_rows_all.append({"Section": "", "Account": r["Account"], "Amount": r["Balance"]})
    bs_rows_all.append({"Section": "Total Current Liabilities", "Account": "", "Amount": total_current_liab_all})

    bs_rows_all.append({"Section": "Long-Term Liabilities", "Account": "", "Amount": ""})
    for _, r in noncurrent_liabilities_all.iterrows():
        bs_rows_all.append({"Section": "", "Account": r["Account"], "Amount": r["Balance"]})
    bs_rows_all.append({"Section": "Total Long-Term Liabilities", "Account": "", "Amount": total_noncurrent_liab_all})

    bs_rows_all.append({"Section": "Total Liabilities", "Account": "", "Amount": total_liab_all})

    bs_rows_all.append({"Section": "Capital", "Account": "", "Amount": ""})
    for _, r in equity_df_all.iterrows():
        bs_rows_all.append({"Section": "", "Account": r["Account"], "Amount": r["Balance"]})
    bs_rows_all.append({"Section": "", "Account": "Net Income", "Amount": net_income_all})
    bs_rows_all.append({"Section": "Total Capital", "Account": "", "Amount": total_capital_all})
    bs_rows_all.append({"Section": "Total Liabilities & Capital", "Account": "", "Amount": total_liab_capital_all})
    bs_rows_all.append({"Section": "Difference (Assets - [Liab + Capital])", "Account": "", "Amount": difference_all})

    bs_layout_df_all = pd.DataFrame(bs_rows_all, columns=["Section", "Account", "Amount"])

    eq_rows_all = [
        {"Line": "Number of shares", "Amount": number_of_shares},
        {"Line": "Nominal value per share", "Amount": nominal_value_per_share},
        {"Line": "Computed Share Capital (CAC)", "Amount": computed_share_capital},
        {"Line": "Share Premium / Other CAC Equity", "Amount": share_premium_other},
        {"Line": "Net Income (from Income Statement)", "Amount": net_income_all},
        {"Line": "Ledger Equity balances (sum of equity accounts)", "Amount": total_equity_ledger_all},
    ]
    eq_df_all = pd.DataFrame(eq_rows_all)

    tab_journal, tab_gl, tab_tb, tab_is, tab_bs, tab_cf = st.tabs(
        ["📄 Journal", "📘 General Ledger", "📊 Trial Balance", "📈 Income Statement", "📗 Statement of Financial Position", "💵 Cashflow (IFRS)"]
    )

    with tab_journal:
        st.markdown("### Journal (4-leg entries, including Opening Balance if provided)")
        st.dataframe(journal_df)

        out_csv_buffer = io.StringIO()
        journal_df.to_csv(out_csv_buffer, index=False)
        st.download_button(
            label="Download journal entries (CSV)",
            data=out_csv_buffer.getvalue(),
            file_name="journal_entries_4_leg.csv",
            mime="text/csv",
        )

    with tab_gl:
        st.markdown("### General Ledger (per account, with running balance)")

        accounts = ["(All)"] + sorted(gl_df_all["Account"].dropna().unique().tolist())
        selected_account = st.selectbox("Filter by account", accounts, index=0)

        gl_display = gl_df_all.copy()
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

    with tab_tb:
        st.markdown("### Trial Balance (ledger balances)")
        st.dataframe(tb_df_all)

        tb_csv = io.StringIO()
        tb_df_all.to_csv(tb_csv, index=False)
        st.download_button(
            label="Download Trial Balance (CSV)",
            data=tb_csv.getvalue(),
            file_name="trial_balance.csv",
            mime="text/csv",
        )

    with tab_is:
        st.markdown("### Income Statement (Profit or Loss)")

        st.markdown("#### Revenue Accounts")
        st.dataframe(revenue_df_all)

        st.markdown("#### Cost of Sales Accounts")
        st.dataframe(cos_df_all)

        st.markdown("#### Operating Expense Accounts")
        st.dataframe(expense_df_all)

        st.markdown("#### Summary")
        st.dataframe(is_summary_df_all)

        is_csv = io.StringIO()
        is_summary_df_all.to_csv(is_csv, index=False)
        st.download_button(
            label="Download Income Statement Summary (CSV)",
            data=is_csv.getvalue(),
            file_name="income_statement_summary.csv",
            mime="text/csv",
        )

    with tab_bs:
        st.markdown("### Statement of Financial Position (Balance Sheet)")
        st.dataframe(bs_layout_df_all)

        bs_layout_csv = io.StringIO()
        bs_layout_df_all.to_csv(bs_layout_csv, index=False)
        st.download_button(
            label="Download Balance Sheet (template style, CSV)",
            data=bs_layout_csv.getvalue(),
            file_name="balance_sheet_template_style.csv",
            mime="text/csv",
        )

        st.markdown("#### CAC / Equity Inputs Summary")
        st.dataframe(eq_df_all)

        st.markdown("#### Suggested CAC Opening Equity Journal (for your main books)")
        journal_suggestions = []

        if computed_share_capital > 0:
            journal_suggestions.append({
                "Dr / Cr": "Dr",
                "Account": "Retained Earnings / Capital Introduced",
                "Amount": computed_share_capital,
                "Narration": "To recognise issued share capital as per CAC"
            })
            journal_suggestions.append({
                "Dr / Cr": "Cr",
                "Account": "Share Capital",
                "Amount": computed_share_capital,
                "Narration": "To recognise issued share capital as per CAC"
            })

        if share_premium_other > 0:
            journal_suggestions.append({
                "Dr / Cr": "Dr",
                "Account": "Retained Earnings / Capital Introduced",
                "Amount": share_premium_other,
                "Narration": "To recognise share premium / other equity as per CAC"
            })
            journal_suggestions.append({
                "Dr / Cr": "Cr",
                "Account": "Share Premium / Other CAC Equity",
                "Amount": share_premium_other,
                "Narration": "To recognise share premium / other equity as per CAC"
            })

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

    with tab_cf:
        st.markdown(f"### IFRS-style Cashflow for {bank_account_name}")

        if cashbook_df_all.empty:
            st.warning(f"No entries found for bank account '{bank_account_name}'. Check the account name in settings.")
        else:
            st.markdown("#### Detailed Bank Movements (with Cash Flow Category)")
            st.dataframe(cashbook_df_all)

            cf_csv = io.StringIO()
            cashbook_df_all.to_csv(cf_csv, index=False)
            st.download_button(
                label="Download Cashbook (CSV)",
                data=cf_csv.getvalue(),
                file_name="cashbook_cashflow_ifrs.csv",
                mime="text/csv",
            )

            # Add reconciliation context
            st.markdown("#### Reconciliation Totals (Sanity Check)")
            bank_inflow = float(cashbook_df_all["Dr Amount"].sum() or 0.0)
            bank_outflow = float(cashbook_df_all["Cr Amount"].sum() or 0.0)
            bank_net = bank_inflow - bank_outflow

            recon_df = pd.DataFrame([
                {"Metric": "Bank-leg inflow (sum Dr Amount)", "Value": bank_inflow},
                {"Metric": "Bank-leg outflow (sum Cr Amount)", "Value": bank_outflow},
                {"Metric": "Bank-leg net (inflow - outflow)", "Value": bank_net},
                {"Metric": "Filtered statement net (credit - debit)", "Value": filtered_net},
                {"Metric": "Difference (bank-leg net - filtered net)", "Value": bank_net - filtered_net},
                {"Metric": "Statement rows processed", "Value": journal_audit["processed_statement_rows"]},
                {"Metric": "Statement rows skipped", "Value": journal_audit["skipped_statement_rows"]},
            ])
            st.dataframe(recon_df)

            # Build summary display with manual closing comparison
            cf_summary_display = cf_summary_all.copy()
            cf_summary_display = pd.concat(
                [
                    cf_summary_display,
                    pd.DataFrame([{
                        "Line": "Closing bank balance (per bank statement/manual input)",
                        "Amount": closing_bank_balance_manual,
                    }]),
                ],
                ignore_index=True,
            )

            closing_calc = None
            try:
                closing_calc_row = cf_summary_all[cf_summary_all["Line"] == "Closing bank balance (from running ledger)"]
                if not closing_calc_row.empty:
                    closing_calc = float(closing_calc_row["Amount"].iloc[0])
            except Exception:
                closing_calc = None

            if closing_calc is not None:
                difference_manual = closing_calc - closing_bank_balance_manual
                cf_summary_display = pd.concat(
                    [
                        cf_summary_display,
                        pd.DataFrame([{
                            "Line": "Difference vs manual closing (computed - manual)",
                            "Amount": difference_manual,
                        }]),
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
                        f"Difference: {difference_manual:,.2f}.\n\n"
                        "Now you can locate the cause using:\n"
                        "• Processing Audit (skipped rows report)\n"
                        "• Amount-parse errors report\n"
                        "• Reconciliation Totals (bank-leg net vs filtered net)\n"
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

    # -------------------------
    # Download all reports as one Excel
    # -------------------------
    st.markdown("### ⬇️ Download ALL reports as one Excel file (multiple sheets)")

    excel_buffer = io.BytesIO()

    try:
        with pd.ExcelWriter(excel_buffer, engine="xlsxwriter") as writer:
            journal_df.to_excel(writer, sheet_name="Journal", index=False)
            gl_df_all.to_excel(writer, sheet_name="GeneralLedger", index=False)
            tb_df_all.to_excel(writer, sheet_name="TrialBalance", index=False)
            revenue_df_all.to_excel(writer, sheet_name="IS_Revenue", index=False)
            cos_df_all.to_excel(writer, sheet_name="IS_CostOfSales", index=False)
            expense_df_all.to_excel(writer, sheet_name="IS_Expenses", index=False)
            is_summary_df_all.to_excel(writer, sheet_name="IS_Summary", index=False)
            bs_layout_df_all.to_excel(writer, sheet_name="BS_Layout", index=False)
            cashbook_df_all.to_excel(writer, sheet_name="Cashbook", index=False)

            cf_summary_display_excel = cf_summary_all.copy()
            cf_summary_display_excel = pd.concat(
                [
                    cf_summary_display_excel,
                    pd.DataFrame([{
                        "Line": "Closing bank balance (per bank statement/manual input)",
                        "Amount": closing_bank_balance_manual,
                    }]),
                ],
                ignore_index=True,
            )

            try:
                closing_calc_row_x = cf_summary_all[cf_summary_all["Line"] == "Closing bank balance (from running ledger)"]
                if not closing_calc_row_x.empty:
                    closing_calc_x = float(closing_calc_row_x["Amount"].iloc[0])
                    diff_manual_x = closing_calc_x - closing_bank_balance_manual
                    cf_summary_display_excel = pd.concat(
                        [
                            cf_summary_display_excel,
                            pd.DataFrame([{
                                "Line": "Difference vs manual closing (computed - manual)",
                                "Amount": diff_manual_x,
                            }]),
                        ],
                        ignore_index=True,
                    )
            except Exception:
                pass

            cf_summary_display_excel.to_excel(writer, sheet_name="Cashflow_Summary", index=False)
            eq_df_all.to_excel(writer, sheet_name="CAC_Equity", index=False)

            # Add diagnostics sheets
            audit_top.to_excel(writer, sheet_name="Audit_Totals", index=False)
            pd.DataFrame(audit_summary_rows).to_excel(writer, sheet_name="Audit_Counts", index=False)
            if not bad_any.empty:
                bad_any.to_excel(writer, sheet_name="Amount_Parse_Errors", index=False)
            if not skipped_df.empty:
                skipped_df.to_excel(writer, sheet_name="Skipped_Rows", index=False)

    except ImportError:
        with pd.ExcelWriter(excel_buffer, engine="openpyxl") as writer:
            journal_df.to_excel(writer, sheet_name="Journal", index=False)
            gl_df_all.to_excel(writer, sheet_name="GeneralLedger", index=False)
            tb_df_all.to_excel(writer, sheet_name="TrialBalance", index=False)
            revenue_df_all.to_excel(writer, sheet_name="IS_Revenue", index=False)
            cos_df_all.to_excel(writer, sheet_name="IS_CostOfSales", index=False)
            expense_df_all.to_excel(writer, sheet_name="IS_Expenses", index=False)
            is_summary_df_all.to_excel(writer, sheet_name="IS_Summary", index=False)
            bs_layout_df_all.to_excel(writer, sheet_name="BS_Layout", index=False)
            cashbook_df_all.to_excel(writer, sheet_name="Cashbook", index=False)

            cf_summary_display_excel = cf_summary_all.copy()
            cf_summary_display_excel = pd.concat(
                [
                    cf_summary_display_excel,
                    pd.DataFrame([{
                        "Line": "Closing bank balance (per bank statement/manual input)",
                        "Amount": closing_bank_balance_manual,
                    }]),
                ],
                ignore_index=True,
            )
            try:
                closing_calc_row_x = cf_summary_all[cf_summary_all["Line"] == "Closing bank balance (from running ledger)"]
                if not closing_calc_row_x.empty:
                    closing_calc_x = float(closing_calc_row_x["Amount"].iloc[0])
                    diff_manual_x = closing_calc_x - closing_bank_balance_manual
                    cf_summary_display_excel = pd.concat(
                        [
                            cf_summary_display_excel,
                            pd.DataFrame([{
                                "Line": "Difference vs manual closing (computed - manual)",
                                "Amount": diff_manual_x,
                            }]),
                        ],
                        ignore_index=True,
                    )
            except Exception:
                pass

            cf_summary_display_excel.to_excel(writer, sheet_name="Cashflow_Summary", index=False)
            eq_df_all.to_excel(writer, sheet_name="CAC_Equity", index=False)

            audit_top.to_excel(writer, sheet_name="Audit_Totals", index=False)
            pd.DataFrame(audit_summary_rows).to_excel(writer, sheet_name="Audit_Counts", index=False)
            if not bad_any.empty:
                bad_any.to_excel(writer, sheet_name="Amount_Parse_Errors", index=False)
            if not skipped_df.empty:
                skipped_df.to_excel(writer, sheet_name="Skipped_Rows", index=False)

    excel_buffer.seek(0)
    st.download_button(
        label="Download ALL reports (Excel, multi-sheet)",
        data=excel_buffer,
        file_name="all_reports.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


if __name__ == "__main__":
    main()
