# infra-pulumi

A Pulumi (Python) rebuild of `infra/`'s CDK stack, resource-for-resource,
for comparison. See [IAC-COMPARISON.md](../IAC-COMPARISON.md) at the repo
root for what actually differs between the three implementations.

All resources are prefixed `jaffle-shop-pulumi-` (vs `jaffle-shop-` for CDK
and `jaffle-shop-tf-` for the Terraform version) so all three can coexist in
the same AWS account without colliding on account-unique names.

## Prerequisites

- Pulumi CLI >= 3.150 (`brew install pulumi`)
- Docker running locally -- `pulumi_docker_build` builds the image directly
  (see [IAC-COMPARISON.md](../IAC-COMPARISON.md) for why this differs from
  CDK's asset pipeline)
- AWS credentials configured (`aws sts get-caller-identity` should work)

## Setup

This uses Pulumi's **local filesystem backend**, not Pulumi Cloud -- state
lives in `.pulumi-state/` (gitignored), matching this being a comparison
sandbox rather than something meant to be collaborated on by a team.

```bash
cd infra-pulumi
mkdir -p .pulumi-state
pulumi login file://$PWD/.pulumi-state
export PULUMI_CONFIG_PASSPHRASE=""   # local backend needs one; empty is fine for a sandbox
pulumi stack init dev
pulumi config set aws:region us-east-1
```

**Don't just run `pulumi login` with no arguments, or let a bare `pulumi`
command run before you've logged in to something explicit.** Without an
explicit backend, the CLI silently falls back to auto-provisioning a
throwaway Pulumi Cloud "ephemeral agent account" and pushes stack state
there instead of locally -- not what you want for a local sandbox. If you
ever see a `CLAIM_URL=https://app.pulumi.com/claim/...` in the output, that's
what happened; `pulumi logout` and redo the `pulumi login file://...` step
above.

**If `pulumi install`/`pulumi preview` fails with `ModuleNotFoundError: No
module named 'pulumi'`** despite claiming to have installed dependencies,
an ambient `VIRTUAL_ENV` environment variable (from this repo's own Python
venv, or any other) is confusing Pulumi's `uv`-based auto-install into
targeting the wrong environment. Build the venv explicitly instead:

```bash
env -u VIRTUAL_ENV uv venv venv
env -u VIRTUAL_ENV uv pip install --python venv/bin/python -r requirements.txt
```

and prefix `pulumi` commands with `env -u VIRTUAL_ENV` if the problem
persists.

## Usage

```bash
pulumi preview       # read-only -- see what would be created/changed
pulumi up
pulumi destroy
```

`pulumi destroy` removes everything, including the S3 bucket's contents
(`force_destroy=True`) and the ECR repo's images (`force_delete=True`) --
same "sandbox, no safety net" trade-off as the CDK stack's removal policy.

## Caveat: don't run this alongside the CDK/Terraform stacks' dbt tasks

The `jaffle_shop`/`raw` Glue databases and the `primary` Athena workgroup
are genuinely shared (pre-existing, referenced by name, not created by any
of the three stacks). Deploying this stack's *infrastructure* alongside the
others is fine -- the resource names don't collide. But if you actually run
this stack's Fargate task (not just deploy the infra), it'll `dbt build`
into the *same* Glue tables the CDK stack's task populates, just pointed at
this stack's own S3 bucket -- last writer wins. Pick one stack's task to
actually run at a time.
