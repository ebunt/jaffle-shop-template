# IaC comparison: CDK vs Terraform vs Pulumi

The same infrastructure -- VPC, S3 bucket, ECR repo + image, three IAM
roles, ECS cluster/task, CloudWatch log group, security group, EventBridge
Scheduler schedule -- built three times: `infra/` (CDK, Python), `infra-
terraform/` (Terraform, HCL), `infra-pulumi/` (Pulumi, Python). All three
validate to the same **32 resources** to create from empty state. This
documents what actually differed while building them, not a generic
CDK-vs-Terraform-vs-Pulumi essay.

## Size

| | Language | Lines | Files |
|---|---|---|---|
| CDK | Python | 384 | 1 |
| Terraform | HCL | 550 | 9 |
| Pulumi | Python | 500 | 1 |

CDK is shortest mainly because of `grant_read_write()` (see below) and
`ecr_assets.DockerImageAsset` needing zero image-build code. Terraform and
Pulumi both had to hand-write the S3 IAM policy and the image build/push
step.

## The Docker image: three different models

- **CDK**: `ecr_assets.DockerImageAsset` builds and pushes automatically as
  part of `cdk deploy`, but *only* into CDK's own shared, content-hash-tagged
  bootstrap repo (`cdk-hnb659fds-container-assets-*`) -- there's no option
  to target a different repo name. Getting a distinctly-named repo (this
  project wanted one -- see `ROLES.md`) requires a second step: the
  third-party `cdk-ecr-deployment` construct copies the image into a real
  named `ecr.Repository` after the fact, adding a Lambda-backed custom
  resource to the stack.
- **Terraform**: no built-in asset-bundling concept at all. The
  `kreuzwerker/docker` provider (third-party) builds and pushes directly to
  whatever repo you name -- one step, but it's a whole separate provider
  most Terraform users wouldn't otherwise need, and content-hash tagging has
  to be hand-rolled (`ecr.tf`'s `local.image_tag` -- a `sha256` over exactly
  the files the `Dockerfile` `COPY`s, since Terraform has no directory-hash
  primitive).
- **Pulumi**: `pulumi_docker_build.Image` builds and pushes directly to a
  named repo in one resource, first-party, no extra copy step, no extra
  content-hash bookkeeping needed (Pulumi's own resource diffing handles
  that). This was the smoothest of the three for this specific piece.

All three confirmed the same safety property under investigation: none of
them actually build or push during a plan/preview when the destination repo
doesn't exist yet (`cdk synth`, `terraform plan`, and `pulumi preview` all
stayed read-only -- verified by watching for build/push output, not just
assumed).

## IAM: `grant()` vs hand-written policies

CDK's `data_bucket.grant_read_write(task_role)` is one line and computes the
exact right S3 action list. Neither Terraform's AWS provider nor Pulumi's
have an equivalent -- both `infra-terraform/iam.tf` and
`infra-pulumi/__main__.py` spell out the same
`s3:GetObject*`/`PutObject`/`DeleteObject*`/... list by hand, copied from
what CDK's grant computed (verified against the deployed CDK stack's actual
IAM policy document, not guessed). This is the single biggest ergonomic gap
between CDK and the other two: CDK's construct library encodes a lot of
"what permissions does *this kind of access* actually need" knowledge that
Terraform/Pulumi's more resource-literal models don't.

## State model

- **CDK**: no separate state file -- CloudFormation *is* the state, held by
  AWS. `cdk diff` talks to a live change set.
- **Terraform**: local `terraform.tfstate` by default (what this comparison
  uses -- see `infra-terraform/README.md`); real usage would point at an S3
  backend. Explicit, file-based, yours to lose if you don't configure a
  remote backend.
- **Pulumi**: the same shape as Terraform (a state file), but
  Pulumi's CLI defaults to *Pulumi Cloud* as the backend unless you
  explicitly configure otherwise -- see the "don't just run `pulumi login`
  with no arguments" warning in `infra-pulumi/README.md`. Building this
  comparison actually hit that: an unconfigured `pulumi login` silently
  auto-provisioned a throwaway Pulumi Cloud account rather than erroring,
  which is a meaningfully different (and more surprising) default than
  either CDK or Terraform.

## Two rough edges hit while building this (both fixed, both documented)

1. **Pulumi Cloud auto-provisioning.** Covered above --
   `infra-pulumi/README.md` has the fix.
2. **Pulumi's Python toolchain auto-detection got confused by an ambient
   `VIRTUAL_ENV`** pointing at an unrelated Python environment, causing
   `pulumi install` to report success while installing into the wrong
   place. Fixed by building the venv explicitly with `uv` rather than
   trusting Pulumi's auto-install; documented in `infra-pulumi/README.md`.

Neither of these are really "Pulumi is worse" findings -- they're both
environment-specific gotchas that would bite in similar ways with any tool
sharing a machine with other Python/cloud tooling. Worth knowing about
regardless.

## Coexistence

All three stacks can be deployed into the same AWS account simultaneously
without name collisions (`jaffle-shop-`, `jaffle-shop-tf-`,
`jaffle-shop-pulumi-` prefixes). What they *can't* safely do simultaneously
is actually **run** their dbt Fargate tasks -- all three point at the same
pre-existing `jaffle_shop`/`raw` Glue databases and `primary` Athena
workgroup, so whichever task runs last overwrites the Glue table metadata
the others wrote. Fine for comparing the infrastructure-as-code; not fine
for running all three pipelines at once. See each tool's README for the
same caveat.
