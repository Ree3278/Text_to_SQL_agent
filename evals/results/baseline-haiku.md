### baseline-haiku  (claude-haiku-4-5-20251001, prompt 491c9d99)

| metric | value |
|---|---|
| accuracy (all 30) | **28/30 = 93%** |
| accuracy (dev, 18 questions) | 17/18 = 94% |
| accuracy (test, 12 questions) | 11/12 = 92% |
| latency p50 / p95 | 1.7s / 2.1s |
| cost per question (avg) | $0.0012 |
| total cost of this run | $0.036 |
| needed a retry | 0/30 |
| false refusals by the guard | 0/30 |

| category | passed |
|---|---|
| lookup | 4/4 |
| dates | 4/5 |
| aggregation | 2/2 |
| join | 7/7 |
| weather | 5/5 |
| statistics | 3/3 |
| unanswerable | 1/2 |
| adversarial | 2/2 |
