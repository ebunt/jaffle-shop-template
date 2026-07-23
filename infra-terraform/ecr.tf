# Unlike CDK's ecr_assets.DockerImageAsset (which always builds/pushes into
# a shared, CDK-managed bootstrap repo -- see infra/'s "The Docker image"
# doc section), Terraform has no built-in asset-bundling system: the
# kreuzwerker/docker provider builds and pushes directly to whatever repo
# you point it at. No copy step needed, unlike the CDK stack's
# cdk_ecr_deployment.ECRDeployment workaround.
resource "aws_ecr_repository" "dbt" {
  name         = "${var.name_prefix}-dbt"
  force_delete = true # mirrors CDK's empty_on_delete=True -- sandbox, no safety net
}

data "aws_ecr_authorization_token" "this" {}

# Content hash over exactly what the Dockerfile COPYs (pyproject.toml,
# uv.lock, README.md, Makefile, dbt/) -- mirrors CDK's asset_hash tagging so
# an unchanged build produces the same tag, and dbt/seeds/jaffle-data (the
# gitignored, locally-generated synthetic seed CSVs -- see Taskfile.yml's
# `gen`/`clean-data` tasks) is excluded since it isn't part of the image
# either way (the Dockerfile's CMD regenerates it inside the container).
locals {
  dockerfile_inputs = concat(
    [for f in ["Dockerfile", "pyproject.toml", "uv.lock", "README.md", "Makefile"] : "${path.module}/../${f}"],
    [for f in fileset("${path.module}/..", "dbt/**") : "${path.module}/../${f}" if !startswith(f, "dbt/seeds/jaffle-data/")],
  )
  image_tag = substr(sha256(join("", [for f in local.dockerfile_inputs : filesha256(f)])), 0, 16)
}

resource "docker_image" "dbt" {
  name = "${aws_ecr_repository.dbt.repository_url}:${local.image_tag}"
  build {
    context    = "${path.module}/.."
    dockerfile = "Dockerfile"
    platform   = "linux/arm64"
  }
  triggers = {
    hash = local.image_tag
  }
}

resource "docker_registry_image" "dbt" {
  name          = docker_image.dbt.name
  keep_remotely = true # don't delete older tags on every apply
}
