# TODO

## RESOLVED: default `make gen` (YEARS=6) OOM'd the task

`make gen` defaults to 6 years of synthetic data (`YEARS ?= 6` in the
`Makefile`), and the deployed Fargate task definition's container
environment didn't override it. The task is sized at 1024 CPU / 2048
MiB, which OOM-killed (`exitCode=2`,
`OutOfMemoryError: container killed due to memory usage`) generating 6
years of data.

Fixed by setting `"YEARS": GEN_YEARS` (`"1"`) in the container's
`environment` in `stack.py`, keeping the task size fixed rather than
scaling it up. `?=` in the Makefile only applies when `YEARS` isn't
already set, so this overrides the default for the deployed task without
touching local `make gen`/`make load`, which still default to 6.
Confirmed with a live `aws ecs run-task` using no override (i.e. the
exact config the schedule uses): seed 7/7, build 42/45 pass + 3 expected
no-op, exit code 0.

If you want more historical data later, bump `cpu`/`memory_limit_mib` on
`task_definition` in `stack.py` to fit before raising `GEN_YEARS`.

## EventBridge Scheduler is currently DISABLED (as of 2026-07-22)

The daily dbt build schedule (`JaffleShopStack-DailyDbtBuildSchedule-D5KBZ4XI3XY2`,
06:00 UTC) was manually disabled via the AWS CLI so it wouldn't fire while
testing something locally. This was **not** done through a CDK code
change — `infra/jaffle_shop_infra/stack.py` still declares the schedule
with no explicit `state=`, which defaults to `ENABLED`. A `cdk deploy`
that happens to touch the `DailyDbtBuildSchedule` resource itself (e.g.
changing the cron expression) would reset it back to enabled; unrelated
deploys (image updates, IAM changes, etc.) won't.

The OOM issue above (the reason it was unsafe to re-enable) is now fixed
and validated live. Re-enable when ready:

```bash
aws scheduler update-schedule --region us-east-1 --cli-input-json file://<(aws scheduler get-schedule --name JaffleShopStack-DailyDbtBuildSchedule-D5KBZ4XI3XY2 --region us-east-1 | jq 'del(.CreationDate,.LastModificationDate,.Arn) | .State = "ENABLED"')
```
