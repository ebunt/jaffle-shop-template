# Analysis: CDK vs Terraform vs Pulumi

Pros/cons and metrics from actually building the same stack three times
(`infra/`, `infra-terraform/`, `infra-pulumi/`). Every number below is
measured from the code and tool output in this repo, not estimated. For the
narrative version of what happened while building these (gotchas hit,
specific design choices), see [IAC-COMPARISON.md](IAC-COMPARISON.md).

## Metrics

| | CDK | Terraform | Pulumi |
|---|---|---|---|
| Language | Python | HCL | Python |
| Lines of code | 384 (+ 19 in `app.py`) | 550 | 500 |
| Files | 3 | 10 | 1 |
| Modules | 1 | 1 (root only, no submodules) | 1 |
| Distinct resource-producing types used | 11 (excl. prop/value-only helpers like `ecs.RuntimePlatform`, `iam.PolicyStatement`) | 20 (`resource` blocks only, excl. `data` sources) | 18 (excl. `*Args` value-only helpers) |
| **Actual resources at deploy time** | **35** | **32** | **32** |
| Third-party / non-core dependency needed | `cdk-ecr-deployment` (for a named ECR repo) | `kreuzwerker/docker` (for image build/push) | `pulumi-docker-build` (first-party Pulumi package, not core `pulumi_aws`) |
| State model | None -- CloudFormation is the state | Local file (this repo) or remote backend | Local file (this repo) or Pulumi Cloud (default) |
| Read-only validation command | `cdk diff` / `cdk synth` | `terraform plan` | `pulumi preview` |
| Hand-written IAM policies needed | 3 of 5 (task-role S3 access and the execution role's ECR+logs access both came free from `grant_read_write()`/automatic execution-role grants) | 5 of 5 | 5 of 5 |

**"Actual resources at deploy time" is the most interesting row.** CDK's 35
is *higher* than Terraform/Pulumi's 32, despite CDK's source being
shortest -- because CDK auto-generates two Lambda-backed custom resources
(and their IAM roles/policies) to implement `autoDeleteObjects` on the S3
bucket and the `cdk-ecr-deployment` image copy, machinery that's invisible
in `stack.py` but real in the deployed account. Terraform's
`force_destroy`/`force_delete` flags and Pulumi's `force_destroy`/
`force_delete` args get the same end behavior (empty the bucket/repo on
delete) with zero extra resources, because emptying-on-delete is a property
of the resource itself in their provider model, not a bolted-on custom
resource.

Terraform declares 30 `resource` blocks across 20 distinct types, but two of
those blocks (`aws_subnet.public`, `aws_route_table_association.public`)
use `count = 2`, producing 4 real resources between them -- hence 32 total
despite 30 blocks.

## CDK (Python)

**Pros**
- Shortest source for the *application-specific* logic. `grant_read_write()`
  computing the exact S3 IAM action list for you is real leverage --
  Terraform and Pulumi both required copying that same action list by hand.
- Type-checked, IDE-navigable Python -- `task_definition.family`,
  `data_bucket.bucket_name`, etc. are real attributes with real types, not
  string interpolation into a template.
- No state file to lose or reconcile -- CloudFormation owns it, `cdk diff`
  always reflects live account reality.

**Cons**
- Highest actual resource count (35) despite shortest source -- the two
  auto-generated Lambda custom resources are real infrastructure (real
  IAM roles, real cold-start-prone Lambda invocations on every deploy)
  that the stack author didn't explicitly choose to add.
- No native way to publish a Docker image to a specific, named ECR repo --
  `ecr_assets.DockerImageAsset` is hardwired to CDK's shared bootstrap
  repo. Getting a named repo (this project's actual requirement) needed a
  third-party construct (`cdk-ecr-deployment`) plus an explicit dependency
  edge, adding two more resources (the Lambda + its role) on top.
- Deploys are synchronous CloudFormation operations -- slower iteration
  loop than Terraform/Pulumi's plan-then-apply when you're only checking
  whether something's syntactically valid (though `cdk synth`/`cdk diff`
  alone are fast and don't deploy anything).

## Terraform (HCL)

**Pros**
- Most explicit and inspectable: every resource, every IAM statement, every
  route is spelled out with no hidden machinery. What you read in `.tf`
  files is what gets created -- 30 blocks, 32 resources, and the only gap
  is the well-understood `count` meta-argument.
- Mature, huge provider ecosystem -- `kreuzwerker/docker` (needed here) is
  one of thousands of available providers, and the core `hashicorp/aws`
  provider is the most complete of the three for AWS resource coverage.
- Declarative HCL keeps resource definitions and their relationships
  (`aws_iam_role.dbt_task.arn` referenced from three different places)
  readable without needing to trace through a general-purpose language's
  control flow.

**Cons**
- Longest source (550 lines) and most files (10) for the least amount of
  actual application logic -- most of the extra length over CDK is
  boilerplate: hand-written `jsonencode()` IAM policy documents, and a
  hand-rolled content-hash (`sha256` over `filesha256()` of every file the
  `Dockerfile` `COPY`s) to get the asset-tagging behavior CDK gives for
  free.
- No native Docker image support -- needed a third-party provider not
  otherwise required by anything else in this stack.
- State is a real operational burden this comparison sidesteps: local
  `terraform.tfstate` (what's used here) doesn't scale past one person on
  one machine; real usage needs a remote backend (S3 + DynamoDB lock table,
  or Terraform Cloud) that's infrastructure in its own right, set up before
  you can safely collaborate.

## Pulumi (Python)

**Pros**
- Single file, real language: same 384-line ballpark as CDK's
  application-specific logic would be if you subtracted CDK's hand-holding
  (`grant()`, asset bundling) -- loops, conditionals, and helper functions
  are just Python, same as CDK.
- Best-in-class Docker image handling of the three: `pulumi_docker_build`
  builds and pushes directly to a *named* repo in one resource, matching
  what this project actually wanted, with no copy step and no third-party
  provider (it's a first-party Pulumi package) and no hand-rolled hashing
  (Pulumi's own diffing handles change detection).
- Resource count (32) matches Terraform's, with visibly less code required
  to get there -- no hand-rolled content hashing, no separate `.tf` files
  per concern.

**Cons**
- Same "no `grant()` equivalent" gap as Terraform -- the S3 IAM policy is
  hand-written JSON here too.
- Defaults toward Pulumi Cloud as the state backend if you don't explicitly
  configure otherwise -- building this comparison actually hit that (see
  `infra-pulumi/README.md`): an unconfigured `pulumi login` silently
  auto-provisioned a throwaway cloud account rather than erroring or
  defaulting local. Neither CDK nor Terraform has a comparable
  surprise-cloud-signup default.
- Python tooling integration is rougher at the edges than CDK's: the
  `uv`-based auto-install (`pulumi install`) silently targeted the wrong
  virtualenv when an unrelated `VIRTUAL_ENV` was set in the shell,
  reporting success while installing nothing usable. Reproducible, fixable
  (build the venv explicitly), but a real papercut CDK's `uv sync`-based
  workflow doesn't have.

## Takeaway

If the deciding factor is **least code for the application-specific
logic**, CDK wins, but its convenience costs real infrastructure (35 vs 32
resources) and forced a third-party workaround for the one requirement
(a named ECR repo) that mattered here. If the deciding factor is **fewest
surprises and most explicit state**, Terraform wins, at the cost of the
most boilerplate. If the deciding factor is **native Docker image handling
and general-purpose-language ergonomics without CloudFormation's
synchronous deploy model**, Pulumi wins, provided its cloud-backend default
and Python-toolchain rough edges are known going in -- both are one-time
setup costs, not ongoing ones.
