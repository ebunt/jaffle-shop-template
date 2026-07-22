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
06:00 UTC) is disabled so it doesn't fire while testing locally. This is
now controlled by `SCHEDULE_STATE` in `infra/jaffle_shop_infra/stack.py`
(currently `"DISABLED"`) -- **not** by toggling it via the AWS CLI/console.

That distinction matters: an earlier version of this doc said "unrelated
deploys (image updates, IAM changes, etc.)" wouldn't reset a manually-set
state, which is wrong -- [Codex caught this on #9](https://github.com/ebunt/jaffle-shop-template/pull/9#discussion_r3626530385).
The schedule's `Target` embeds the task definition's ARN
(`Target.EcsParameters.TaskDefinitionArn`), which changes on *every* new
task definition revision -- including plain image or environment-variable
updates, not just changes to the schedule itself. That means
CloudFormation has to update the `DailyDbtBuildSchedule` resource on
nearly every deploy, and applies the full desired state from the
template each time. With no explicit `state=` in the code, that desired
state defaults to `ENABLED` -- silently re-enabling a manually-disabled
schedule. Confirmed this actually happened: the CLI-disabled schedule
flipped back to `ENABLED` after the YEARS-fix deploy (which only changed
a container env var), before `SCHEDULE_STATE` existed. Re-verified after
adding it: deployed again (task definition revision bump, same as
before), schedule stayed `DISABLED`.

**To change it**: edit `SCHEDULE_STATE` in `stack.py` and `cdk deploy` --
not the AWS CLI/console, since the next deploy will silently override
whatever you set out-of-band.
