arm: hard

| Mode | Runs | Success | Median input tokens | Median output tokens | Median turns | Median wall s | Median cost-equivalent $ | ripple calls | Invalid (not counted) | Runs with a ripple error |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| with-ripple | 27 | 25/27 (93%) | 225,561 | 4,776 | 25.0 | 41.8 | 0.152 | 11 | 0 | 1 |
| without | 27 | 27/27 (100%) | 246,333 | 5,507 | 27.0 | 40.0 | 0.149 | 0 | 0 | 0 |

| Task | with-ripple | without |
|---|---:|---:|
| 20-neverthrow-rename-resultasync-combine | 3/3 | 3/3 |
| 21-neverthrow-rename-resultasync-orelse | 2/3 | 3/3 |
| 22-superstruct-rename-assert | 3/3 | 3/3 |
| 23-superstruct-rename-create | 3/3 | 3/3 |
| 24-tsyringe-rename-transform | 3/3 | 3/3 |
| 25-tsyringe-rename-registry | 2/3 | 3/3 |
| 26-react-pro-sidebar-rename-submenu | 3/3 | 3/3 |
| 27-react-pro-sidebar-rename-usemediaquery | 3/3 | 3/3 |
| 28-react-pro-sidebar-rename-menu-slideduration | 3/3 | 3/3 |
