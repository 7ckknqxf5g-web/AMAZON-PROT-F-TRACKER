# Amazon Profit & Loss Tracker

A small command-line tool in Python with a local SQLite database. I use it to
track the products I sell on Amazon: stock, costs, fees and the profit of every sale.

## What it does
- Stores products with stock, cost, selling price, shipping fee and Amazon fee
- Records sales and lowers the stock in the same database transaction
- Keeps the prices of each sale, so changing a price later does not change past profits
- Stores money as whole cents to avoid rounding errors
- Shows total revenue, costs, net profit and profit margin
- Exports all sales to a CSV file

Fees on a product are amounts per unit sold.

## Run
You only need Python 3. No extra packages and no database server.

    python3 tracker.py

The database file `tracker.db` is created automatically next to the script.

## Ideas for later
- Fees as a percentage of the price
- Monthly reports
- A simple web interface

## Note
I built this project while learning, with help from an AI assistant.


