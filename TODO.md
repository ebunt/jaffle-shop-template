# TODO

## EventBridge Scheduler is currently DISABLED (as of 2026-07-22)

The daily dbt build schedule (`JaffleShopStack-DailyDbtBuildSchedule-D5KBZ4XI3XY2`,
06:00 UTC) was manually disabled via the AWS CLI so it wouldn't fire while
testing something locally. This was **not** done through a CDK code
change — `infra/jaffle_shop_infra/stack.py` still declares the schedule
with no explicit `state=`, which defaults to `ENABLED`. A `cdk deploy`
that happens to touch the `DailyDbtBuildSchedule` resource itself (e.g.
changing the cron expression) would reset it back to enabled; unrelated
deploys (image updates, IAM changes, etc.) won't.

**Before re-enabling**, fix the known issue below — otherwise the next
scheduled run will very likely fail the same way it did before.

### Re-enable command

```bash
aws scheduler update-schedule --region us-east-1 --cli-input-json file://<(aws scheduler get-schedule --name JaffleShopStack-DailyDbtBuildSchedule-D5KBZ4XI3XY2 --region us-east-1 | jq 'del(.CreationDate,.LastModificationDate,.Arn) | .State = "ENABLED"')
```

## Known issue: default `make gen` (YEARS=6) OOMs the task

`make gen` defaults to 6 years of synthetic data (`YEARS ?= 6` in the
`Makefile`), and the deployed Fargate task definition's container
environment doesn't override `YEARS`. The task is sized at 1024 CPU /
2048 MiB, which OOM-killed (`exitCode=2`,
`OutOfMemoryError: container killed due to memory usage`) generating 6
years of data. All the validation runs done so far used a `YEARS=1`
container override via `aws ecs run-task --overrides` specifically to
avoid this — the task definition itself was never changed, at the
request of keeping infra size fixed while testing with a smaller
workload instead.

Pick one before re-enabling the schedule:

- Bump the task's `cpu`/`memory_limit_mib` in `stack.py` (e.g. 2048 CPU /
  4096 MiB) to fit the default 6-year run, or
- Add a `YEARS` entry to the container's `environment` in `stack.py` so
  the scheduled run uses a smaller default (e.g. `"1"`) instead of 6.
