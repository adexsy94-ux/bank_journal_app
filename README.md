# Bank Statement → Journal & Financial Reports (Streamlit App)

This Streamlit app converts a structured bank statement into:

-  4-leg journal entries using a **Suspense Account** (Option B)
-  General Ledger (with running balances)
-  Trial Balance
-  Income Statement
-  Statement of Financial Position (Balance Sheet, template-style)
-  IFRS-style Cashflow (Operating / Investing / Financing) with bank reconciliation

It’s designed to take a CSV/Excel bank statement in a simple template format and produce accounting-ready reports for further processing or import into your accounting system.

---

##  Key Features

- **Template-driven input**  
  Download a ready-made CSV template from the app and paste your bank statement into it.

- **4-leg journal using Suspense (Option B)**  
  For each transaction, the app generates 4 double-entry legs via a Suspense Account:
  - **Payments (money out):**  
    1. Dr Main Account / Cr Suspense  
    2. Dr Suspense / Cr Bank  
  - **Receipts (money in):**  
    1. Dr Suspense / Cr Main Account  
    2. Dr Bank / Cr Suspense  

- **IFRS-style cashflow classification**  
  Cash movements in the bank account are tagged as:
  - Operating  
  - Investing  
  - Financing  
  based on the **Account Type** of the main account.

- **Automatic financial statements**
  - Trial Balance (net balances only)  
  - Income Statement with:
    - Total Revenue  
    - Cost of Sales  
    - Gross Profit  
    - Operating Expenses  
    - Net Profit / (Loss)
  - Balance Sheet layout:
    - Current Assets  
    - Property and Equipment (Non-Current Assets)  
    - Other Assets  
    - Current Liabilities  
    - Long-Term Liabilities  
    - Capital (Equity + Net Income)

- **CAC / Equity helper**
  - Input number of shares, nominal value, and share premium
  - Compute share capital
  - Suggest opening CAC equity journal:
    - Dr Retained Earnings / Capital Introduced  
    - Cr Share Capital / Share Premium

---

- **Needed Account Type**

  - Expense
  - Income
  - Current Asset
  - Non-Current Liability
  - Share Capital
  - Cost of Sales

##  Requirements

- Python 3.9+ (recommended)
- Packages:
  - `streamlit`
  - `pandas`

Install dependencies:

```bash
pip install streamlit pandas
