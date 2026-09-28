import sqlite3

conn = sqlite3.connect('quant_trades.db')
c = conn.cursor()
print("--- HOLDING in my_portfolio ---")
c.execute("SELECT id, ticker, status, quantity, buy_price, total_cost FROM my_portfolio WHERE status = 'HOLDING'")
for r in c.fetchall():
    print(r)
conn.close()


