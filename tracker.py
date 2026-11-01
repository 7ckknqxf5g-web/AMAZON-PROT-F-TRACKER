"""Amazon profit & loss tracker (Python + SQLite).

Products and sales are stored in a local SQLite file (tracker.db) that is
created automatically next to this script. Money is stored as whole cents to
avoid rounding errors. Fees on a product are amounts per unit sold.
"""

import csv
import os
import sqlite3
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

CURRENCY = "$"
DB_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "tracker.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS products (
    product_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    sku            TEXT NOT NULL UNIQUE,
    product_name   TEXT NOT NULL,
    stock_quantity INTEGER NOT NULL DEFAULT 0 CHECK (stock_quantity >= 0),
    cost_cents     INTEGER NOT NULL CHECK (cost_cents >= 0),
    price_cents    INTEGER NOT NULL CHECK (price_cents >= 0),
    shipping_cents INTEGER NOT NULL DEFAULT 0 CHECK (shipping_cents >= 0),
    fee_cents      INTEGER NOT NULL DEFAULT 0 CHECK (fee_cents >= 0),
    created_at     TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

-- Each sale keeps the prices that were valid at the time of the sale,
-- so changing a product's price later does not change past profits.
CREATE TABLE IF NOT EXISTS sales_log (
    sale_id            INTEGER PRIMARY KEY AUTOINCREMENT,
    product_id         INTEGER NOT NULL REFERENCES products(product_id),
    quantity_sold      INTEGER NOT NULL CHECK (quantity_sold > 0),
    unit_price_cents   INTEGER NOT NULL,
    unit_cost_cents    INTEGER NOT NULL,
    shipping_cents     INTEGER NOT NULL,
    fee_cents          INTEGER NOT NULL,
    net_profit_cents   INTEGER NOT NULL,
    sale_date          TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_sales_date ON sales_log (sale_date);
"""


# ---------- money helpers ----------

def to_cents(amount):
    return int((amount * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def from_cents(cents):
    return Decimal(cents) / 100


def money(cents):
    return f"{CURRENCY}{from_cents(cents):.2f}"


# ---------- input helpers ----------

def ask_decimal(prompt, minimum=Decimal("0")):
    while True:
        raw = input(prompt).strip().replace(",", ".")
        try:
            value = Decimal(raw)
        except InvalidOperation:
            print("Please enter a number.")
            continue
        if value < minimum:
            print(f"The value must be at least {minimum}.")
            continue
        return value


def ask_int(prompt, minimum=0):
    while True:
        raw = input(prompt).strip()
        try:
            value = int(raw)
        except ValueError:
            print("Please enter a whole number.")
            continue
        if value < minimum:
            print(f"The value must be at least {minimum}.")
            continue
        return value


# ---------- database operations ----------

def get_connection(path=DB_FILE):
    conn = sqlite3.connect(path, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.executescript(SCHEMA)
    return conn


def add_product(conn, sku, name, stock, cost, price, shipping, fee):
    try:
        conn.execute(
            "INSERT INTO products (sku, product_name, stock_quantity, cost_cents, "
            "price_cents, shipping_cents, fee_cents) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (sku, name, stock, to_cents(cost), to_cents(price),
             to_cents(shipping), to_cents(fee)),
        )
        return True
    except sqlite3.IntegrityError:
        print("A product with this SKU already exists.")
        return False


def list_products(conn):
    return conn.execute(
        "SELECT sku, product_name, stock_quantity, cost_cents, price_cents "
        "FROM products ORDER BY product_name"
    ).fetchall()


def restock(conn, sku, quantity):
    cur = conn.execute(
        "UPDATE products SET stock_quantity = stock_quantity + ? WHERE sku = ?",
        (quantity, sku),
    )
    return cur.rowcount == 1


def record_sale(conn, sku, quantity):
    """Saves a sale and lowers the stock in one transaction.

    Returns (net_profit_in_cents, error_message). One of them is always None.
    """
    try:
        conn.execute("BEGIN IMMEDIATE")
        product = conn.execute(
            "SELECT product_id, stock_quantity, cost_cents, price_cents, "
            "shipping_cents, fee_cents FROM products WHERE sku = ?",
            (sku,),
        ).fetchone()
        if product is None:
            conn.rollback()
            return None, "Product not found."
        if product["stock_quantity"] < quantity:
            conn.rollback()
            return None, f"Not enough stock (available: {product['stock_quantity']})."

        profit_per_unit = (
            product["price_cents"]
            - product["cost_cents"]
            - product["shipping_cents"]
            - product["fee_cents"]
        )
        net_profit = profit_per_unit * quantity

        conn.execute(
            "INSERT INTO sales_log (product_id, quantity_sold, unit_price_cents, "
            "unit_cost_cents, shipping_cents, fee_cents, net_profit_cents) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (product["product_id"], quantity, product["price_cents"],
             product["cost_cents"], product["shipping_cents"],
             product["fee_cents"], net_profit),
        )
        conn.execute(
            "UPDATE products SET stock_quantity = stock_quantity - ? WHERE product_id = ?",
            (quantity, product["product_id"]),
        )
        conn.commit()
        return net_profit, None
    except sqlite3.Error as err:
        if conn.in_transaction:
            conn.rollback()
        return None, f"Database error: {err}"


def get_summary(conn):
    row = conn.execute(
        "SELECT COUNT(*) AS sales_count, "
        "COALESCE(SUM(quantity_sold), 0) AS units_sold, "
        "COALESCE(SUM(unit_price_cents * quantity_sold), 0) AS revenue, "
        "COALESCE(SUM((unit_cost_cents + shipping_cents + fee_cents) * quantity_sold), 0) AS costs, "
        "COALESCE(SUM(net_profit_cents), 0) AS profit FROM sales_log"
    ).fetchone()
    summary = dict(row)
    summary["margin"] = (
        Decimal(summary["profit"]) / Decimal(summary["revenue"]) * 100
        if summary["revenue"] > 0
        else Decimal(0)
    )
    return summary


def export_sales_csv(conn, filename="sales_report.csv"):
    rows = conn.execute(
        "SELECT s.sale_id, s.sale_date, p.sku, p.product_name, s.quantity_sold, "
        "s.unit_price_cents, s.unit_cost_cents, s.shipping_cents, s.fee_cents, "
        "s.net_profit_cents FROM sales_log s "
        "JOIN products p ON p.product_id = s.product_id ORDER BY s.sale_date, s.sale_id"
    ).fetchall()

    if not rows:
        print("No sales to export yet.")
        return
    with open(filename, "w", newline="", encoding="utf-8") as file:
        writer = csv.writer(file)
        writer.writerow(["sale_id", "sale_date", "sku", "product_name", "quantity_sold",
                         "unit_price", "unit_cost", "shipping_fee", "amazon_fee", "net_profit"])
        for r in rows:
            writer.writerow([
                r["sale_id"], r["sale_date"], r["sku"], r["product_name"],
                r["quantity_sold"],
                f"{from_cents(r['unit_price_cents']):.2f}",
                f"{from_cents(r['unit_cost_cents']):.2f}",
                f"{from_cents(r['shipping_cents']):.2f}",
                f"{from_cents(r['fee_cents']):.2f}",
                f"{from_cents(r['net_profit_cents']):.2f}",
            ])
    print(f"Exported {len(rows)} sales to {filename}")


# ---------- command line menu ----------

def menu_add_product(conn):
    sku = input("SKU: ").strip()
    name = input("Product name: ").strip()
    if not sku or not name:
        print("SKU and product name cannot be empty.")
        return
    stock = ask_int("Stock quantity: ")
    cost = ask_decimal(f"Cost per unit ({CURRENCY}): ")
    price = ask_decimal(f"Selling price ({CURRENCY}): ")
    shipping = ask_decimal(f"Shipping fee per unit ({CURRENCY}): ")
    fee = ask_decimal(f"Amazon fee per unit ({CURRENCY}): ")
    if add_product(conn, sku, name, stock, cost, price, shipping, fee):
        print("Product saved.")


def menu_list_products(conn):
    products = list_products(conn)
    if not products:
        print("No products yet.")
        return
    print(f"\n{'SKU':<12}{'Name':<24}{'Stock':>6}{'Cost':>10}{'Price':>10}")
    for p in products:
        print(
            f"{p['sku']:<12}{p['product_name'][:22]:<24}{p['stock_quantity']:>6}"
            f"{money(p['cost_cents']):>10}{money(p['price_cents']):>10}"
        )


def menu_restock(conn):
    sku = input("SKU: ").strip()
    quantity = ask_int("Units to add: ", minimum=1)
    if restock(conn, sku, quantity):
        print("Stock updated.")
    else:
        print("Product not found.")


def menu_record_sale(conn):
    sku = input("SKU: ").strip()
    quantity = ask_int("Units sold: ", minimum=1)
    profit, error = record_sale(conn, sku, quantity)
    if error:
        print(error)
    else:
        print(f"Sale saved. Net profit: {money(profit)}")


def menu_summary(conn):
    s = get_summary(conn)
    if s["sales_count"] == 0:
        print("No sales recorded yet.")
        return
    print("\n--- Financial overview ---")
    print(f"Sales recorded : {s['sales_count']}")
    print(f"Units sold     : {s['units_sold']}")
    print(f"Revenue        : {money(s['revenue'])}")
    print(f"Costs          : {money(s['costs'])}")
    print(f"Net profit     : {money(s['profit'])}")
    print(f"Profit margin  : {s['margin']:.2f}%")


def run_cli():
    conn = get_connection()
    actions = {
        "1": menu_add_product,
        "2": menu_list_products,
        "3": menu_restock,
        "4": menu_record_sale,
        "5": menu_summary,
        "6": export_sales_csv,
    }
    print("--- Amazon Profit & Loss Tracker ---")
    while True:
        print("\n1. Add product")
        print("2. List products")
        print("3. Restock a product")
        print("4. Record a sale")
        print("5. View summary")
        print("6. Export sales to CSV")
        print("7. Exit")
        choice = input("Select an option (1-7): ").strip()

        if choice == "7":
            break
        action = actions.get(choice)
        if action is None:
            print("Invalid option. Please choose between 1 and 7.")
            continue
        try:
            action(conn)
        except sqlite3.Error as err:
            print(f"Database error: {err}")

    conn.close()
    print("Goodbye.")


if __name__ == "__main__":
    run_cli()
