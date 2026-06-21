# Power Copilot Strategy Source Notes

> Status: roadmap artifact. The current demo implements the common peer fact layer and typed comparison controls; scenario, guidance-monitoring, and management-signal capabilities described in the strategy are not implemented claims.

## Decision

Recommend a role-based peer intelligence layer plus deterministic diagnostics and scenario tools. Do not frame the expansion as a larger filing chatbot.

## Sources reviewed

- Complete FY2023-FY2025 peer inventory: `grab-finance-copilot-pkg/data/peer_source_inventory.json`.
- Existing Grab Finance Copilot specification and FY2023-FY2025 dataset in this workspace.
- Uber FY2025 official investor-relations results: https://investor.uber.com/news-events/news/press-release-details/2026/Uber-Announces-Results-for-Fourth-Quarter-and-Full-Year-2025/default.aspx
- Lyft FY2025 official investor-relations results: https://investor.lyft.com/news-events-presentations/press-releases/detail/191/lyft-reports-record-q4-and-full-year-2025-results
- DoorDash FY2025 official investor-relations results: https://ir.doordash.com/news/news-details/2026/DoorDash-Releases-Fourth-Quarter-and-Full-Year-2025-Financial-Results/default.aspx
- Sea FY2025 official investor-relations results: https://cdn.sea.com/investor/4Q2025/JcKns4LaJC8bxcQdJwXz/2026.03.03%20Sea%20Fourth%20Quarter%20and%20Full%20Year%202025%20Results.pdf

## Comparability notes

- Uber: Gross Bookings and segment Adjusted EBITDA provide strong Mobility and Delivery comparisons, but geographic scale and Freight complicate group comparisons.
- Lyft: useful pure Mobility benchmark; FY2025 GAAP net income is distorted by a tax valuation allowance release.
- DoorDash: best public Deliveries benchmark; Marketplace GOV still requires definition mapping to Grab GMV.
- Sea: useful for SEA ecosystem, marketplace monetization, and digital lending; Shopee GMV is not a direct substitute for food-delivery GMV.

## Visualization decision

No scale chart was included. Absolute GMV, Gross Bookings, and Marketplace GOV differ materially by business definition and scope. A role-and-metric table communicates the peer design without implying false numeric comparability.

## Report structure mapping

- Title: From Finance Copilot to Decision Intelligence.
- Executive summary: three answer-first recommendations.
- Key findings/evidence: role-based peer table, capability cards, and closed-loop workflow.
- Recommended next steps: four-stage build sequence.
- Further questions: persona, killer decision, and time grain.
- Caveats: company-specific non-GAAP definitions and normalization limits.
