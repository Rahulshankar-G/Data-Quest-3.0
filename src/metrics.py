import os
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
SAMPLE_DATA_PATH = ROOT / "data" / "engineered_d2c_dataset.csv"
LIVE_DATA_PATH = Path(
    os.getenv(
        "PROFITPILOT_LIVE_DATA_PATH",
        str(ROOT / "data" / "live_campaign_data.csv"),
    )
)


def load_data():
    data_source = os.getenv("PROFITPILOT_DATA_SOURCE", "sample").strip().lower()
    if data_source == "live":
        if not LIVE_DATA_PATH.is_file():
            raise RuntimeError(
                f"Live data source is selected, but no synchronized snapshot exists at {LIVE_DATA_PATH}."
            )
        data_path = LIVE_DATA_PATH
    elif data_source == "sample":
        data_path = SAMPLE_DATA_PATH
    else:
        raise RuntimeError(
            "PROFITPILOT_DATA_SOURCE must be either 'sample' or 'live'."
        )
    df = pd.read_csv(data_path)

    df["date"] = pd.to_datetime(
        df["date"],
        format="mixed", 
        dayfirst=False,
        errors="coerce"
    )

    df["contribution_profit"] = (
        df["sales_revenue"]
        - df["total_cogs"]
        - df["ad_spend"]
    )

    df["roas"] = (
        df["sales_revenue"]
        / df["ad_spend"].replace(0, pd.NA)
    )

    df["cpa"] = (
        df["ad_spend"]
        / df["conversions"].replace(0, pd.NA)
    )

    df["conversion_rate"] = (
        df["conversions"]
        / df["estimated_clicks"].replace(0, pd.NA)
    )

    df["ctr"] = (
        df["estimated_clicks"]
        / df["impressions"].replace(0, pd.NA)
    )

    df["inventory_risk"] = df["stock_level"] < 100

    return df


def campaign_summary(df):
    summary = (
        df.groupby(
            ["ad_platform", "campaign_id", "sku_id"],
            as_index=False
        )
        .agg(
            spend=("ad_spend", "sum"),
            revenue=("sales_revenue", "sum"),
            conversions=("conversions", "sum"),
            units_sold=("units_sold", "sum"),
            total_cogs=("total_cogs", "sum"),
            contribution_profit=("contribution_profit", "sum"),
            avg_inventory=("stock_level", "mean"),
            avg_margin_pct=("profit_margin_pct", "mean"),
            impressions=("impressions", "sum"),
            clicks=("estimated_clicks", "sum")
        )
    )

    summary["roas"] = (
        summary["revenue"]
        / summary["spend"].replace(0, pd.NA)
    )

    summary["cpa"] = (
        summary["spend"]
        / summary["conversions"].replace(0, pd.NA)
    )

    summary["conversion_rate"] = (
        summary["conversions"]
        / summary["clicks"].replace(0, pd.NA)
    )

    summary["ctr"] = (
        summary["clicks"]
        / summary["impressions"].replace(0, pd.NA)
    )

    summary["inventory_risk"] = summary["avg_inventory"] < 100

    return summary


if __name__ == "__main__":
    df = load_data()
    summary = campaign_summary(df)

    print("Dataset loaded successfully.")
    print("Raw rows:", len(df))
    print("Campaign-SKU groups:", len(summary))

    print("\nTop 10 campaigns by contribution profit:")
    print(
        summary[
            [
                "ad_platform",
                "campaign_id",
                "sku_id",
                "spend",
                "revenue",
                "contribution_profit",
                "roas",
                "avg_inventory"
            ]
        ]
        .sort_values(
            "contribution_profit",
            ascending=False
        )
        .head(10)
        .to_string(index=False)
    )