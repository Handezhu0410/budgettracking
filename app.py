from flask import Flask, render_template, request, redirect, url_for, flash
from datetime import date, datetime, timedelta
import sqlite3
import os

app = Flask(__name__)
app.secret_key = "dev"  # 開發用

DB_NAME = "finance.db"
DEFAULT_BUDGET = 30000  # 預設預算（可在頁面上調整顯示）


# ---------- DB 工具函式 ----------
def get_db_connection():
    con = sqlite3.connect(DB_NAME)
    con.row_factory = sqlite3.Row
    return con


def init_db():
    """建立資料表（若不存在）"""
    con = get_db_connection()
    cur = con.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            amount REAL NOT NULL,
            type TEXT CHECK(type IN ('income', 'expense')) NOT NULL,
            category TEXT NOT NULL,
            date TEXT NOT NULL,               -- YYYY-MM-DD
            note TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    con.commit()
    con.close()


# ---- 這裡改成「程式啟動時」就初始化，不再用 before_first_request ----
if not os.path.exists(DB_NAME):
    open(DB_NAME, "a").close()
init_db()
# ------------------------------------------------------------------


# ---------- 輔助函式 ----------
def get_current_month_range():
    today = date.today()
    first_day = today.replace(day=1)
    if today.month == 12:
        next_month_first = date(today.year + 1, 1, 1)
    else:
        next_month_first = date(today.year, today.month + 1, 1)
    last_day = next_month_first - timedelta(days=1)
    return first_day, last_day


def get_stats(start_date, end_date, category=None, min_amount=None, max_amount=None):
    """
    根據條件計算：
    - 總收入
    - 總支出
    - 各分類支出總額
    - 該區間所有紀錄列表
    """
    con = get_db_connection()
    cur = con.cursor()

    conditions = ["date BETWEEN ? AND ?"]
    params = [start_date, end_date]

    if category and category.strip():
        conditions.append("category = ?")
        params.append(category.strip())

    if min_amount is not None:
        conditions.append("amount >= ?")
        params.append(min_amount)

    if max_amount is not None:
        conditions.append("amount <= ?")
        params.append(max_amount)

    where_clause = " AND ".join(conditions)

    # 總收入
    cur.execute(
        f"""
        SELECT COALESCE(SUM(amount), 0) AS total_income
        FROM records
        WHERE type = 'income' AND {where_clause}
        """,
        params,
    )
    total_income = cur.fetchone()["total_income"] or 0

    # 總支出
    cur.execute(
        f"""
        SELECT COALESCE(SUM(amount), 0) AS total_expense
        FROM records
        WHERE type = 'expense' AND {where_clause}
        """,
        params,
    )
    total_expense = cur.fetchone()["total_expense"] or 0

    # 各分類支出
    cur.execute(
        f"""
        SELECT category, COALESCE(SUM(amount), 0) AS total
        FROM records
        WHERE type = 'expense' AND {where_clause}
        GROUP BY category
        ORDER BY total DESC
        """,
        params,
    )
    rows = cur.fetchall()
    category_labels = [r["category"] for r in rows]
    category_amounts = [r["total"] for r in rows]

    # 該區間內所有明細
    cur.execute(
        f"""
        SELECT id, amount, type, category, date, note
        FROM records
        WHERE {where_clause}
        ORDER BY date DESC, id DESC
        """,
        params,
    )
    records = cur.fetchall()

    con.close()

    return {
        "total_income": float(total_income),
        "total_expense": float(total_expense),
        "category_labels": category_labels,
        "category_amounts": category_amounts,
        "records": records,
    }


# ---------- Routes ----------
@app.route("/", methods=["GET", "POST"])
def index():
    # 預設：當月第一天 ~ 當月最後一天
    month_start, month_end = get_current_month_range()

    # 頁面上的預設值
    start_date = month_start.isoformat()
    end_date = month_end.isoformat()
    category_filter = ""
    min_amount = None
    max_amount = None
    budget = DEFAULT_BUDGET

    if request.method == "POST":
        action = request.form.get("action")

        # 新增記錄
        if action == "add_record":
            try:
                amount_raw = request.form.get("amount", "").strip()
                amount = float(amount_raw)
            except ValueError:
                flash("金額格式不正確，請輸入數字。", "danger")
                return redirect(url_for("index"))

            record_type = request.form.get("type")
            category = request.form.get("category", "").strip()
            date_str = request.form.get("date") or date.today().isoformat()
            note = request.form.get("note", "").strip()

            if record_type not in ("income", "expense"):
                flash("類型必須為收入或支出。", "danger")
                return redirect(url_for("index"))

            if not category:
                flash("請輸入分類。", "danger")
                return redirect(url_for("index"))

            con = get_db_connection()
            cur = con.cursor()
            cur.execute(
                """
                INSERT INTO records (amount, type, category, date, note)
                VALUES (?, ?, ?, ?, ?)
                """,
                (amount, record_type, category, date_str, note),
            )
            con.commit()
            con.close()

            flash("成功新增一筆記錄！", "success")
            return redirect(url_for("index"))

        # 篩選 / 設定預算
        elif action == "filter":
            start_date = request.form.get("start_date") or start_date
            end_date = request.form.get("end_date") or end_date
            category_filter = request.form.get("category_filter", "").strip()

            # 金額篩選
            min_amount_str = request.form.get("min_amount", "").strip()
            max_amount_str = request.form.get("max_amount", "").strip()

            try:
                if min_amount_str:
                    min_amount = float(min_amount_str)
            except ValueError:
                flash("最低金額格式錯誤，已忽略此條件。", "warning")

            try:
                if max_amount_str:
                    max_amount = float(max_amount_str)
            except ValueError:
                flash("最高金額格式錯誤，已忽略此條件。", "warning")

            # 預算輸入（不存 DB，只影響顯示）
            budget_str = request.form.get("budget", "").strip()
            try:
                if budget_str:
                    budget = float(budget_str)
            except ValueError:
                flash("預算金額格式錯誤，已使用預設值。", "warning")
                budget = DEFAULT_BUDGET

    # 取得統計結果
    stats = get_stats(
        start_date=start_date,
        end_date=end_date,
        category=category_filter,
        min_amount=min_amount,
        max_amount=max_amount,
    )

    balance = stats["total_income"] - stats["total_expense"]
    budget_diff = budget - stats["total_expense"]

    return render_template(
        "index.html",
        # 篩選條件
        start_date=start_date,
        end_date=end_date,
        category_filter=category_filter,
        min_amount=min_amount if min_amount is not None else "",
        max_amount=max_amount if max_amount is not None else "",
        budget=budget,
        # 統計
        total_income=stats["total_income"],
        total_expense=stats["total_expense"],
        balance=balance,
        budget_diff=budget_diff,
        category_labels=stats["category_labels"],
        category_amounts=stats["category_amounts"],
        records=stats["records"],
        DEFAULT_BUDGET=DEFAULT_BUDGET,
    )


if __name__ == "__main__":
    # 直接執行 app.py 後，瀏覽 http://127.0.0.1:5000
    app.run(debug=True)
