import io
from typing import List, Dict

import pandas as pd
import streamlit as st


# -----------------------------
# Helper functions
# -----------------------------

def create_template_df() -> pd.DataFrame:
    columns = [
        "account type",
        "account",
        "start date",
        "details",
        "debit",
        "credit",
    ]
    return pd.DataFrame(columns=columns)


def normalize_columns(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.columns = [c.strip().lower() for c in df.columns]
    return df


def to_number(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str).str.replace(",", "").str.strip(),
        errors="coerce"
    ).fillna(0)


def classify_cash_flow_type(main_account_type: str) -> str:
    if not main_account_type:
        return "Operating"

    t = main_account_type.strip().lower()

    if any(k in t for k in [
        "non current asset", "non-current asset", "noncurrent asset",
        "fixed asset", "investment"
    ]):
        return "Investing"

    if any(k in t for k in [
        "equity", "share capital", "capital", "retained",
        "reserve", "loan", "borrowings", "non current liability"
    ]):
        return "Financing"

    return "Operating"


# -----------------------------
# Journal generation
# -----------------------------

def generate_journal_rows(
    df: pd.DataFrame,
    bank_account_name: str,
    bank_account_type: str,
    suspense_account_name: str,
    suspense_account_type: str
) -> pd.DataFrame:

    rows: List[Dict] = []

    df = df.copy()
    df["debit"] = to_number(df.get("debit", 0))
    df["credit"] = to_number(df.get("credit", 0))

    for _, row in df.iterrows():
        debit = float(row["debit"])
        credit = float(row["credit"])

        if debit == 0 and credit == 0:
            continue

        direction = "out" if debit > 0 else "in"
        amount = debit if debit > 0 else credit

        account_type = str(row.get("account type", "")).strip()
        account_name = str(row.get("account", "")).strip()
        date_val = row.get("start date")
        details = str(row.get("details", "")).strip()

        cf_category = classify_cash_flow_type(account_type)

        if direction == "out":
            rows += [
                {
                    "Date": date_val,
                    "Transaction / Details": details,
                    "Leg": 1,
                    "Account Type": account_type or "Expense",
                    "Account": account_name or "Main Account",
                    "Dr Amount": amount,
                    "Cr Amount": 0.0,
                    "Narration": "Expense recognised",
                    "Cash Flow Category": "",
                },
                {
                    "Date": date_val,
                    "Transaction / Details": details,
                    "Leg": 2,
                    "Account Type": suspense_account_type,
                    "Account": suspense_account_name,
                    "Dr Amount": 0.0,
                    "Cr Amount": amount,
                    "Narration": "To suspense",
                    "Cash Flow Category": "",
                },
                {
                    "Date": date_val,
                    "Transaction / Details": details,
                    "Leg": 3,
                    "Account Type": suspense_account_type,
                    "Account": suspense_account_name,
                    "Dr Amount": amount,
                    "Cr Amount": 0.0,
                    "Narration": "Clear suspense",
                    "Cash Flow Category": "",
                },
                {
                    "Date": date_val,
                    "Transaction / Details": details,
                    "Leg": 4,
                    "Account Type": bank_account_type,
                    "Account": bank_account_name,
                    "Dr Amount": 0.0,
                    "Cr Amount": amount,
                    "Narration": "Bank payment",
                    "Cash Flow Category": cf_category,
                },
            ]
        else:
            rows += [
                {
                    "Date": date_val,
                    "Transaction / Details": details,
                    "Leg": 1,
                    "Account Type": suspense_account_type,
                    "Account": suspense_account_name,
                    "Dr Amount": amount,
                    "Cr Amount": 0.0,
                    "Narration": "Receipt to suspense",
                    "Cash Flow Category": "",
                },
                {
                    "Date": date_val,
                    "Transaction / Details": details,
                    "Leg": 2,
                    "Account Type": account_type or "Income",
                    "Account": account_name or "Main Account",
                    "Dr Amount": 0.0,
                    "Cr Amount": amount,
                    "Narration": "Income recognised",
                    "Cash Flow Category": "",
                },
                {
                    "Date": date_val,
                    "Transaction / Details": details,
                    "Leg": 3,
                    "Account Type": bank_account_type,
                    "Account": bank_account_name,
                    "Dr Amount": amount,
                    "Cr Amount": 0.0,
                    "Narration": "Bank receipt",
                    "Cash Flow Category": cf_category,
                },
                {
                    "Date": date_val,
                    "Transaction / Details": details,
                    "Leg": 4,
                    "Account Type": suspense_account_type,
                    "Account": suspense_account_name,
                    "Dr Amount": 0.0,
                    "Cr Amount": amount,
                    "Narration": "Clear suspense",
                    "Cash Flow Category": "",
                },
            ]

    journal_df = pd.DataFrame(rows)
    return journal_df.sort_values(["Date", "Leg"]).reset_index(drop=True)


# -----------------------------
# Cashflow (IFRS) — FIXED
# -----------------------------

def build_cashbook_ifrs(
    journal_df: pd.DataFrame,
    bank_account_name: str,
    opening_balance: float
):
    df = journal_df.copy()
    df["Date_dt"] = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True)

    cb = df[df["Account"].astype(str).str.strip() == bank_account_name.strip()].copy()
    cb = cb.sort_values(["Date_dt", "Leg"])

    if cb.empty:
        return cb, pd.DataFrame()

    cb["Cash_Movement"] = (cb["Dr Amount"] - cb["Cr Amount"]).round(2)

    # --- NORMALISE CASH FLOW CATEGORY ---
    cat = cb.get("Cash Flow Category", "").astype(str).str.strip()
    cat = cat.replace("", "Operating")
    cat_lower = cat.str.lower()

    norm = pd.Series("Operating", index=cb.index)
    norm[cat_lower.str.contains("opening")] = "Opening Balance"
    norm[cat_lower.str.contains("invest")] = "Investing"
    norm[cat_lower.str.contains("financ|loan|borrow")] = "Financing"
    norm[cat_lower.str.contains("operat")] = "Operating"

    cb["Cash Flow Category"] = norm

    cb["Running_Cash_Balance"] = cb["Cash_Movement"].cumsum().round(2)

    opening_journal_balance = cb.loc[
        cb["Cash Flow Category"] == "Opening Balance", "Cash_Movement"
    ].sum()

    operating_total = cb.loc[cb["Cash Flow Category"] == "Operating", "Cash_Movement"].sum()
    investing_total = cb.loc[cb["Cash Flow Category"] == "Investing", "Cash_Movement"].sum()
    financing_total = cb.loc[cb["Cash Flow Category"] == "Financing", "Cash_Movement"].sum()

    # ✅ THE KEY FIX
    net_change = cb.loc[
        cb["Cash Flow Category"] != "Opening Balance", "Cash_Movement"
    ].sum()

    closing_calc = opening_journal_balance + net_change
    closing_running = cb["Running_Cash_Balance"].iloc[-1]

    diff = round(closing_calc - closing_running, 2)

    summary = pd.DataFrame([
        {"Line": "Net cash from Operating activities", "Amount": operating_total},
        {"Line": "Net cash from Investing activities", "Amount": investing_total},
        {"Line": "Net cash from Financing activities", "Amount": financing_total},
        {"Line": "Net increase / (decrease) in cash", "Amount": net_change},
        {"Line": "Opening bank balance (per journal)", "Amount": opening_journal_balance},
        {"Line": "Closing bank balance (Opening + Net cash)", "Amount": closing_calc},
        {"Line": "Closing bank balance (from running ledger)", "Amount": closing_running},
        {"Line": "Difference (should be 0)", "Amount": diff},
    ])

    return cb, summary


# -----------------------------
# Streamlit App
# -----------------------------

def main():
    st.set_page_config(page_title="Bank → Journal & Cashflow", layout="wide")
    st.title("Bank Statement → Journal & IFRS Cashflow")

    bank_account_name = st.sidebar.text_input("Bank Account Name", "PROVIDUS BANK - MAIN")
    opening_balance = st.sidebar.number_input("Opening Bank Balance", value=0.0)

    uploaded_file = st.file_uploader("Upload bank statement", type=["csv", "xlsx"])

    if uploaded_file:
        df = pd.read_csv(uploaded_file) if uploaded_file.name.endswith(".csv") else pd.read_excel(uploaded_file)
        df = normalize_columns(df)

        journal = generate_journal_rows(
            df,
            bank_account_name,
            "Current Asset",
            "Suspense Account",
            "Other",
        )

        cashbook, summary = build_cashbook_ifrs(journal, bank_account_name, opening_balance)

        st.subheader("Cashbook")
        st.dataframe(cashbook)

        st.subheader("Cashflow Summary")
        st.dataframe(summary)


if __name__ == "__main__":
    main()
