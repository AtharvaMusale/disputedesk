"""Keep dispute-type issues, then draw 10k per group (card, money_transfer) with a label-balanced
sample: up to a third per label, shortfalls (rare approve/partial) topped up with deny."""
import pandas as pd

IN, OUT = "data/raw/cfpb/cfpb_filtered.parquet", "data/processed/cfpb_sample.parquet"
N, SEED = 10_000, 42
KEEP = {
    "card": ["Problem with a purchase shown on your statement", "Fees or interest", "Problem when making payments",
             "Problem with a purchase or transfer", "Unexpected or other fees", "Trouble using your card",
             "Trouble using the card"],
    "money_transfer": ["Fraud or scam", "Unauthorized transactions or other transaction problem",
                       "Other transaction problem", "Money was not available when promised",
                       "Wrong amount charged or received", "Incorrect exchange rate", "Lost or stolen check",
                       "Lost or stolen money order"],
}


def sample_group(g: pd.DataFrame) -> pd.DataFrame:
    share = N // 3
    out = [g[g.label == lab].sample(min(share, (g.label == lab).sum()), random_state=SEED)
           for lab in ("approve", "partial")]
    rest = g[g.label == "deny"]
    out.append(rest.sample(min(N - sum(map(len, out)), len(rest)), random_state=SEED))
    return pd.concat(out)


def main() -> None:
    df = pd.read_parquet(IN)
    df["group"] = df.Product.map(lambda p: "money_transfer" if "oney" in p else "card")
    df = df[[i in KEEP[g] for i, g in zip(df.Issue, df.group)]]
    df = df[df["Consumer complaint narrative"].str.len() >= 100]  # drop near-empty narratives
    s = pd.concat(sample_group(g) for _, g in df.groupby("group")).assign(is_synthetic=False)
    s.to_parquet(OUT, index=False)
    print(f"eligible after issue filter: {len(df):,}\n")
    print(pd.crosstab(df.group, df.label, margins=True), "\n")
    print(pd.crosstab(s.group, s.label, margins=True))


if __name__ == "__main__":
    main()
