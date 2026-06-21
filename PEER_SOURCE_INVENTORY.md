# Peer Source Inventory: FY2023-FY2025

The project has complete FY2023–FY2025 official-source coverage for Uber, Lyft, DoorDash, and Sea in [`grab-finance-copilot-pkg/data/peer_source_inventory.json`](grab-finance-copilot-pkg/data/peer_source_inventory.json). Source coverage is complete; numeric extraction is intentionally a smaller common-metric layer and is recorded separately as `extracted_metric_families`.

| Company | FY2023 | FY2024 | FY2025 |
|---|---|---|---|
| Uber | [Released 2024-02-07](https://investor.uber.com/news-events/news/press-release-details/2024/Uber-Announces-Results-for-Fourth-Quarter-and-Full-Year-2023/) | [Released 2025-02-05](https://investor.uber.com/news-events/news/press-release-details/2025/Uber-Announces-Results-for-Fourth-Quarter-and-Full-Year-2024/default.aspx) | [Released 2026-02-04](https://investor.uber.com/news-events/news/press-release-details/2026/Uber-Announces-Results-for-Fourth-Quarter-and-Full-Year-2025/default.aspx) |
| Lyft | [Corrected release 2024-02-13](https://investor.lyft.com/news-events-presentations/press-releases/detail/119/correcting-and-replacing-lyft-announces-fourth-quarter-and-full-year-2023-results) | [Released 2025-02-11](https://investor.lyft.com/news-events-presentations/press-releases/detail/103/lyft-reports-record-q4-and-full-year-2024-results) | [Released 2026-02-10](https://investor.lyft.com/news-events-presentations/press-releases/detail/191/lyft-reports-record-q4-and-full-year-2025-results) |
| DoorDash | [Released 2024-02-15](https://ir.doordash.com/news/news-details/2024/DoorDash-Releases-Fourth-Quarter-and-Full-Year-2023-Financial-Results/default.aspx) | [Released 2025-02-11](https://ir.doordash.com/news/news-details/2025/DoorDash-Releases-Fourth-Quarter-and-Full-Year-2024-Financial-Results/default.aspx) | [Released 2026-02-18](https://ir.doordash.com/news/news-details/2026/DoorDash-Releases-Fourth-Quarter-and-Full-Year-2025-Financial-Results/default.aspx) |
| Sea | [Released 2024-03-04](https://cdn.sea.com/webmain/static/resource/seagroup/website/investornews/4Q2023/STWdlYqdqwmJntN4lrDk/2024.03.04%20Sea%20Fourth%20Quarter%20and%20Full%20Year%202023%20Results.pdf) | [Released 2025-03-04](https://cdn.sea.com/webmain/static/resource/seagroup/website/investornews/4Q2024/PiuK2bhIyLtug8iucOux/2025.03.04%20Sea%20Fourth%20Quarter%20and%20Full%20Year%202024%20Results.pdf) | [Released 2026-03-03](https://cdn.sea.com/investor/4Q2025/JcKns4LaJC8bxcQdJwXz/2026.03.03%20Sea%20Fourth%20Quarter%20and%20Full%20Year%202025%20Results.pdf) |

## Controlling-source rules

1. Prefer the official annual earnings release or SEC filing.
2. If a later release explicitly recasts a prior period, use the recast value for comparison and preserve the original value as a versioned fact.
3. Store reported metric name, canonical metric name, denominator, exclusions, period, currency, and source citation.
4. Refuse a peer ranking when definitions cannot be normalized defensibly.

## Material caveats already identified

- Lyft FY2023 must use the corrected release because the original misstated margin-guidance expansion.
- Uber GAAP net income contains material investment-revaluation and tax effects; operating comparisons should emphasize operating income, Adjusted EBITDA, and FCF.
- DoorDash FY2025 includes Deliveroo and FY2023 includes Wolt-related comparability considerations.
- Sea renamed Digital Financial Services to Monee and Shopee GMV is not directly comparable to delivery GMV.
