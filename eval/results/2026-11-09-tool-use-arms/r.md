arm: restricted

| Mode | Runs | Success | Median input tokens | Median output tokens | Median turns | Median wall s | Median cost-equivalent $ | ripple calls | Invalid (not counted) | Runs with a ripple error |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| with-ripple | 27 | 19/27 (70%) | 345,619 | 5,200 | 30.0 | 42.2 | 0.234 | 41 | 0 | 1 |
| without | 27 | 21/27 (78%) | 610,230 | 6,417 | 31.0 | 57.0 | 0.351 | 0 | 0 | 0 |

| Task | with-ripple | without |
|---|---:|---:|
| 20-neverthrow-rename-resultasync-combine | 3/3 | 3/3 |
| 21-neverthrow-rename-resultasync-orelse | 3/3 | 3/3 |
| 22-superstruct-rename-assert | 2/3 | 3/3 |
| 23-superstruct-rename-create | 2/3 | 2/3 |
| 24-tsyringe-rename-transform | 2/3 | 2/3 |
| 25-tsyringe-rename-registry | 2/3 | 2/3 |
| 26-react-pro-sidebar-rename-submenu | 1/3 | 2/3 |
| 27-react-pro-sidebar-rename-usemediaquery | 2/3 | 2/3 |
| 28-react-pro-sidebar-rename-menu-slideduration | 2/3 | 2/3 |
