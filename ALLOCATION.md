# ALLOCATION — 6 Rings

Team: **6 Rings** · Members: TODO (full names of all four teammates)
Repository: https://github.com/WingKei67/2026Autumn-6

## The $1,000,000 split

| Seat id            | Role   | Capital      | Run by |
|--------------------|--------|-------------:|--------|
| `6_rings_broker`   | Broker | $400,000     | TODO   |
| `6_rings_trader_1` | Trader | $150,000     | TODO   |
| `6_rings_trader_2` | Trader | $150,000     | TODO   |
| `6_rings_trader_3` | Trader | $150,000     | TODO   |
| `6_rings_trader_4` | Trader | $150,000     | TODO   |
| Exchange license   | —      | $0 (not bought) |     |
| **Total**          |        | **$1,000,000** |      |

$400,000 + 4 × $150,000 = $1,000,000. This is the allocation approved by the
instructor on 2026-10-08. We first registered with an exchange license
($300k, broker $350k, two traders at $175k) and reversed it before scored play.

## Why this split

**One well-capitalised broker.** A market maker earns the spread but carries
inventory: every fill leaves it long or short, and the arena charges margin
interest and liquidates teams below maintenance. A thick capital buffer is what
lets the desk keep quoting through an adverse move instead of being flattened.
We give it the largest single allocation, 40% of the budget.

**Four equal trader seats.** Each teammate owns one bot, so four strategies
run in parallel and we learn from four sets of fills. Equal $150k seats keep
results comparable across strategies, and no single bad strategy can sink more
than 15% of the budget. We chose several mid-sized seats over one or two large
ones because early on we do not yet know which strategy works; diversification
across seats is cheaper than being wrong with all our capital in one.

**No exchange license.** A venue only earns fees if it attracts order flow and
stays up, and it competes with the other venues on fees and reliability. That
is a $300k bet on operational uptime that does not trade a single share, and it
would have left us with only $700k across the seats that earn P&L directly. We
preferred to put every dollar into the broker and trader seats.

**Consistency with what we run.** Five seats are staffed by four people: the
broker plus four traders, one process each (one teammate runs two).
