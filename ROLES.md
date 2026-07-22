# IAM roles in this stack

Three IAM roles exist in `infra/jaffle_shop_infra/stack.py`, each scoped to
exactly what its assumer needs and nothing else. It's easy to conflate them
("why does dbt need three roles to run a query?") — it doesn't; only one of
the three ever touches your data.

## Diagram

```
                                            +-----------------------------+
                                            |    EventBridge Scheduler    |
                                            | jaffle-shop-daily-dbt-build |
                                            |      (state: DISABLED)      |
                                            +-----------------------------+
                                                        assumes
                                                           |
                                                           v
                                         +-----------------------------------+
                                         |     jaffle-shop-scheduler-role    |
                                         |   trust: scheduler.amazonaws.com  |
                                         |   (condition: aws:SourceAccount)  |
                                         |                                   |
                                         |            ecs:RunTask            |
                                         | iam:PassRole  (the 2 roles below) |
                                         +-----------------------------------+
                                                      ecs:RunTask
                                                           |
                                                           v
                         =====================================================================
                         | ECS FARGATE TASK                                                  |
                         | cluster: jaffle-shop-cluster                                      |
                         | family:  jaffle-shop-dbt-build                                    |
                         |                                                                   |
                         | +----------------------------+    +-----------------------------+ |
                         | |   jaffle-shop-dbt-task-    |    |  jaffle-shop-dbt-task-role  | |
                         | |       execution-role       |    |                             | |
                         | |                            |    |                             | |
                         | |    "gets the container     |    |     "what dbt does once     | |
                         | |          running"          |    |        it is running"       | |
                         | |                            |    |                             | |
                         | |     ecr:BatchGetImage      |    |  s3:Get*/Put*/List*/Delete* | |
                         | | ecr:GetDownloadUrlForLayer |    |  glue:CreateTable/GetTable/ | |
                         | | ecr:GetAuthorizationToken  |    |          UpdateTable/...    | |
                         | |    logs:CreateLogStream    |    | athena:StartQueryExecution/ | |
                         | |     logs:PutLogEvents      |    |         GetQueryResults/... | |
                         | +----------------------------+    +-----------------------------+ |
                         =====================================================================
                                          |                                 |
         +-------------------------+------+                   +-------------+---------+----------------------+
         v                         v                          v                       v                      v
+-----------------+   +------------------------+   +--------------------+   +------------------+   +------------------+
|     ECR repo    |   |    CloudWatch Logs     |   |     S3 bucket      |   |     Glue DBs     |   | Athena workgroup |
| jaffle-shop-dbt |   | /jaffle-shop/dbt-build |   | jaffle-shop-data-* |   | raw, jaffle_shop |   |     primary      |
|                 |   |                        |   |                    |   |  (pre-existing)  |   |                  |
+-----------------+   +------------------------+   +--------------------+   +------------------+   +------------------+
```

## The three roles

| Role | Assumed by | Purpose |
|---|---|---|
| **`jaffle-shop-dbt-task-role`** | ECS tasks (`ecs-tasks.amazonaws.com`) | What the *running dbt process* can do: read/write the `DataBucket` (Iceberg table data), Glue Catalog access (`GetDatabase`/`GetTable`/`CreateTable`/`UpdateTable`/`DeleteTable`/partition ops) scoped to the `raw` and `jaffle_shop` databases, and Athena query execution (`StartQueryExecution`/`GetQueryExecution`/`GetQueryResults`/`StopQueryExecution`/`GetWorkGroup`) scoped to the `primary` workgroup. This is the identity dbt itself runs as. |
| **`jaffle-shop-dbt-task-execution-role`** | ECS tasks | The *infrastructure* role ECS needs to launch the container at all — not visible to dbt/your code. Pulls the image from `jaffle-shop-dbt` (ECR), writes container stdout/stderr to `/jaffle-shop/dbt-build` (CloudWatch Logs). Every Fargate task needs one of these regardless of what the task does. |
| **`jaffle-shop-scheduler-role`** | EventBridge Scheduler (`scheduler.amazonaws.com`), restricted to this account via an `aws:SourceAccount` condition | What lets the (currently `DISABLED`) daily cron actually start the Fargate task: `ecs:RunTask` on the `jaffle-shop-dbt-build` task definition family, plus `iam:PassRole` for the two roles above — restricted via an `iam:PassedToService=ecs-tasks.amazonaws.com` condition so it can't be used to pass either role to some other service. |

## Boundaries

- **The execution role never touches data.** No S3, Glue, or Athena
  permissions — only ECR pull and CloudWatch Logs write. If it were
  compromised, it could see container logs and know what image is running;
  it couldn't read a single row of table data.
- **The task role never touches the image or logs.** No ECR or CloudWatch
  Logs permissions — only S3/Glue/Athena. It's the one role that matters if
  you're reasoning about "what can dbt read or write."
- **The scheduler role never touches data or images at all.** Its only
  power is starting the task (`ecs:RunTask`) and handing it the two roles
  above (`iam:PassRole`, itself scoped so it can only pass roles *to ECS
  tasks*, not to arbitrary services). It can trigger a run; it can't see
  what that run does.
- **If you add a new AWS integration to the dbt project** (say, a second S3
  bucket the task needs to read from), it's `jaffle-shop-dbt-task-role` you
  grant permissions to — never the execution role, never the scheduler
  role.
