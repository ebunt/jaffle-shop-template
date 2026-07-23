# S3 bucket names are globally unique, not just account-unique -- account ID
# + region are baked in alongside the prefix, same reasoning as the CDK
# stack's bucket_name (see infra/jaffle_shop_infra/stack.py).
resource "aws_s3_bucket" "data" {
  bucket        = "${var.name_prefix}-data-${data.aws_caller_identity.current.account_id}-${var.aws_region}"
  force_destroy = true # mirrors CDK's auto_delete_objects=True -- sandbox, no safety net

  tags = { Name = "${var.name_prefix}-data" }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket                  = aws_s3_bucket.data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "enforce_ssl" {
  bucket = aws_s3_bucket.data.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid       = "EnforceSSL"
      Effect    = "Deny"
      Principal = "*"
      Action    = "s3:*"
      Resource = [
        aws_s3_bucket.data.arn,
        "${aws_s3_bucket.data.arn}/*",
      ]
      Condition = {
        Bool = { "aws:SecureTransport" = "false" }
      }
    }]
  })
}
