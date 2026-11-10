"""CFPB complaints WITH narratives, from the CC0 HuggingFace mirror (BEE-spoke-data).

Why a mirror: the CFPB search API ignores has_narrative and returns no narrative text, and the
official bulk CSV has no narrative column. Mirror data ends 2024-02-09.
Output: data/raw/cfpb/cfpb_filtered.parquet (2022+, kept products, usable labels).
"""
from pathlib import Path

import pandas as pd
import requests

BASE = "https://huggingface.co/datasets/BEE-spoke-data/consumer-finance-complaints/resolve/refs%2Fconvert%2Fparquet/has-text/train/{:04d}.parquet"
DIR = Path("data/raw/cfpb/hf")
OUT = Path("data/raw/cfpb/cfpb_filtered.parquet")
COLS = ["Date received", "Product", "Sub-product", "Issue", "Sub-issue", "Consumer complaint narrative",
        "Company", "State", "Company response to consumer", "Complaint ID"]
PRODUCTS = {  # user-chosen: card + money transfer (all name variants over the years)
    "Credit card or prepaid card", "Credit card", "Prepaid card",
    "Money transfer, virtual currency, or money service", "Money transfers",
}
LABELS = {"Closed with monetary relief": "approve", "Closed with non-monetary relief": "partial",
          "Closed with explanation": "deny"}  # self-reported proxy; others dropped


def main() -> None:
    DIR.mkdir(parents=True, exist_ok=True)
    parts = []
    for i in range(3):
        p = DIR / f"{i:04d}.parquet"
        if not p.exists():
            tmp = p.with_suffix(".part")
            with requests.get(BASE.format(i), stream=True, timeout=120) as r:
                r.raise_for_status()
                with tmp.open("wb") as f:
                    for c in r.iter_content(1 << 20):
                        f.write(c)
            tmp.rename(p)
        df = pd.read_parquet(p, columns=COLS)
        df = df[df["Product"].isin(PRODUCTS) & (df["Date received"] >= "2022-01-01")]
        parts.append(df)
    df = pd.concat(parts)
    df["label"] = df["Company response to consumer"].map(LABELS)
    df = df.dropna(subset=["label"]).drop_duplicates("Complaint ID")
    df.to_parquet(OUT, index=False)
    print(f"{OUT}: {len(df):,} rows")


if __name__ == "__main__":
    main()
