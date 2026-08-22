# infra-terraform

A Terraform (HCL) rebuild of `infra/`'s CDK stack, resource-for-resource, for
comparison. See [IAC-COMPARISON.md](../IAC-COMPARISON.md) at the repo root
for what actually differs between the three implementations.

All resources are prefixed `jaffle-shop-tf-` (vs `jaffle-shop-` for CDK and
`jaffle-shop-pulumi-` for the Pulumi version) so all three can coexist in the
same AWS account without colliding on account-unique names.

## Prerequisites

- Terraform >= 1.5 (`brew install terraform`)
- Docker running locally -- the `kreuzwerker/docker` provider builds the
  image directly (see [IAC-COMPARISON.md](../IAC-COMPARISON.md) for why this
  differs from CDK's asset pipeline)
- AWS credentials configured (`aws sts get-caller-identity` should work)

## Usage

```bash
cd infra-terraform
terraform init      # downloads the aws + docker providers
terraform plan       # read-only -- see what would be created/changed
terraform apply
```

State is local (`terraform.tfstate`, gitignored) -- there's no S3/Dynamo
backend configured, matching this being a comparison sandbox rather than
something meant to be collaborated on by a team. Point `versions.tf`'s
`terraform {}` block at a real backend before using this for anything real.

```bash
terraform destroy
```

removes everything, including the S3 bucket's contents (`force_destroy =
true`) and the ECR repo's images (`force_delete = true`) -- same "sandbox,
no safety net" trade-off as the CDK stack's removal policy.

## Caveat: don't run this alongside the CDK/Pulumi stacks' dbt tasks

The `jaffle_shop`/`raw` Glue databases and the `primary` Athena workgroup
are genuinely shared (pre-existing, referenced by name, not created by any
of the three stacks). Deploying this stack's *infrastructure* alongside the
others is fine -- the resource names don't collide. But if you actually run
this stack's Fargate task (not just deploy the infra), it'll `dbt build`
into the *same* Glue tables the CDK stack's task populates, just pointed at
this stack's own S3 bucket -- last writer wins. Pick one stack's task to
actually run at a time.
