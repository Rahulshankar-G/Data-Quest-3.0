import pandas as pd
import sqlite3

# 1. Load your engineered CSV
csv_file = "data/engineered_d2c_dataset.csv"
df = pd.read_csv(csv_file)

# 2. Connect to (or create) the SQLite Database file
conn = sqlite3.connect("d2c_engine.db")
cursor = conn.cursor()

print("Connected to SQLite database: 'd2c_engine.db'")

# -------------------------------------------------------------------
# TABLE 1: PRODUCTS
# Static product catalog with financial unit economics
# -------------------------------------------------------------------
products_df = df[['sku_id', 'product_category', 'price', 'unit_cogs']].drop_duplicates(subset=['sku_id'])

products_df.to_sql('products', conn, if_exists='replace', index=False)
print(f"Table 'products' created ({len(products_df)} rows)")


# -------------------------------------------------------------------
# TABLE 2: AD_CAMPAIGNS
# Unique campaign mapping and channel metadata
# -------------------------------------------------------------------
campaigns_df = df[['campaign_id', 'sku_id', 'ad_platform', 'channel', 'campaign_duration_days', 'region', 'customer_segment']].drop_duplicates(subset=['campaign_id'])

campaigns_df.to_sql('ad_campaigns', conn, if_exists='replace', index=False)
print(f"Table 'ad_campaigns' created ({len(campaigns_df)} rows)")


# -------------------------------------------------------------------
# TABLE 3: INVENTORY
# Current stock levels per SKU
# -------------------------------------------------------------------
# Taking the latest recorded stock level per SKU
inventory_df = df.groupby('sku_id')['stock_level'].last().reset_index()

inventory_df.to_sql('inventory', conn, if_exists='replace', index=False)
print(f"Table 'inventory' created ({len(inventory_df)} rows)")


# -------------------------------------------------------------------
# TABLE 4: DAILY_METRICS
# Time-series metrics (spend, performance, conversions, revenue)
# -------------------------------------------------------------------
metrics_df = df[[
    'date', 'campaign_id', 'ad_spend', 'impressions', 
    'click_through_rate', 'estimated_clicks', 'conversions', 
    'units_sold', 'sales_revenue', 'total_cogs', 
    'gross_profit', 'profit_margin_pct', 'competition_index', 'seasonality_index'
]]

metrics_df.to_sql('daily_metrics', conn, if_exists='replace', index=False)
print(f"Table 'daily_metrics' created ({len(metrics_df)} rows)")

# Commit changes and close connection
conn.commit()
conn.close()

print("\nDatabase setup successfully completed!")