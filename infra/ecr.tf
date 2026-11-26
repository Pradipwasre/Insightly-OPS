# One private repository per image. Tags are immutable (git SHA), images are scanned on push.
locals {
  images = ["insightly-api", "insightly-ui", "insightly-ingest"]
}

resource "aws_ecr_repository" "app" {
  for_each             = toset(local.images)
  name                 = each.value
  image_tag_mutability = "IMMUTABLE"
  force_delete         = var.environment != "prod"

  image_scanning_configuration {
    scan_on_push = true
  }
  encryption_configuration {
    encryption_type = "AES256"
  }
}

resource "aws_ecr_lifecycle_policy" "app" {
  for_each   = aws_ecr_repository.app
  repository = each.value.name
  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the last 20 images"
      selection    = { tagStatus = "any", countType = "imageCountMoreThan", countNumber = 20 }
      action       = { type = "expire" }
    }]
  })
}
