### baseline-sonnet  (claude-sonnet-5-5, prompt 491c9d99)

| metric | value |
|---|---|
| accuracy (all 30) | **30/30 = 100%** |
| accuracy (dev, 18 questions) | 18/18 = 100% |
| accuracy (test, 12 questions) | 12/12 = 100% |
| latency p50 / p95 | 2.9s / 4.6s |
| cost per question (avg) | $0.0036 |
| total cost of this run | $0.108 |
| needed a retry | 0/30 |
| false refusals by the guard | 0/30 |

| category | passed |
|---|---|
| lookup | 4/4 |
| dates | 5/5 |
| aggregation | 2/2 |
| join | 7/7 |
| weather | 5/5 |
| statistics | 3/3 |
| unanswerable | 2/2 |
| adversarial | 2/2 |
