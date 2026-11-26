# CloudWatch: log retention, alarms -> SNS email, and a dashboard.
# App metrics (namespace "Insightly") are sent by app/cloudwatch.py; container logs and
# node/pod metrics by the amazon-cloudwatch-observability EKS add-on (Container Insights).

# Pre-create the Container Insights log groups so we control retention
resource "aws_cloudwatch_log_group" "containers" {
  for_each          = toset(["application", "dataplane", "host", "performance"])
  name              = "/aws/containerinsights/${local.name}/${each.value}"
  retention_in_days = var.log_retention_days
}

# Count failed chats from the API's JSON logs
resource "aws_cloudwatch_log_metric_filter" "chat_failures" {
  name           = "${local.name}-chat-failures"
  log_group_name = aws_cloudwatch_log_group.containers["application"].name
  pattern        = "\"chat failed\""
  metric_transformation {
    name      = "ChatFailuresFromLogs"
    namespace = "Insightly"
    value     = "1"
  }
}

resource "aws_sns_topic" "alerts" {
  name = "${local.name}-alerts"
}

resource "aws_sns_topic_subscription" "email" {
  count     = var.alert_email == "" ? 0 : 1
  topic_arn = aws_sns_topic.alerts.arn
  protocol  = "email"
  endpoint  = var.alert_email
}

locals {
  app_dims = { Environment = var.environment }
  alarms = {
    errors = {
      metric = "Errors", stat = "Sum", period = 300, threshold = 5, op = "GreaterThanThreshold",
      text = "More than 5 failed chat requests in 5 minutes"
    }
    latency = {
      metric = "LatencySeconds", stat = "Average", period = 300, threshold = 30, op = "GreaterThanThreshold",
      text = "Average answer time above 30 s for 5 minutes"
    }
    guardrail_spike = {
      metric = "GuardrailBlocks", stat = "Sum", period = 300, threshold = 25, op = "GreaterThanThreshold",
      text = "Guardrails blocked more than 25 requests in 5 minutes (possible attack)"
    }
    llm_cost = {
      metric = "CostUSD", stat = "Sum", period = 86400, threshold = var.daily_cost_alarm_usd, op = "GreaterThanThreshold",
      text = "LLM spend above the daily budget"
    }
  }
}

resource "aws_cloudwatch_metric_alarm" "app" {
  for_each            = local.alarms
  alarm_name          = "${local.name}-${each.key}"
  alarm_description   = each.value.text
  namespace           = "Insightly"
  metric_name         = each.value.metric
  dimensions          = local.app_dims
  statistic           = each.value.stat
  period              = each.value.period
  evaluation_periods  = 1
  threshold           = each.value.threshold
  comparison_operator = each.value.op
  treat_missing_data  = "notBreaching"
  alarm_actions       = [aws_sns_topic.alerts.arn]
  ok_actions          = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "rds_cpu" {
  alarm_name          = "${local.name}-rds-cpu"
  alarm_description   = "RDS CPU above 80% for 10 minutes"
  namespace           = "AWS/RDS"
  metric_name         = "CPUUtilization"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Average"
  period              = 300
  evaluation_periods  = 2
  threshold           = 80
  comparison_operator = "GreaterThanThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_metric_alarm" "rds_storage" {
  alarm_name          = "${local.name}-rds-free-storage"
  alarm_description   = "RDS free storage below 2 GB"
  namespace           = "AWS/RDS"
  metric_name         = "FreeStorageSpace"
  dimensions          = { DBInstanceIdentifier = aws_db_instance.main.identifier }
  statistic           = "Minimum"
  period              = 300
  evaluation_periods  = 1
  threshold           = 2 * 1024 * 1024 * 1024
  comparison_operator = "LessThanThreshold"
  alarm_actions       = [aws_sns_topic.alerts.arn]
}

resource "aws_cloudwatch_dashboard" "main" {
  dashboard_name = local.name
  dashboard_body = jsonencode({
    widgets = [
      for i, w in [
        { title = "Chat requests / errors", metrics = [["Insightly", "Requests", "Environment", var.environment, { stat = "Sum" }], ["Insightly", "Errors", "Environment", var.environment, { stat = "Sum" }]] },
        { title = "Answer latency (s)", metrics = [["Insightly", "LatencySeconds", "Environment", var.environment, { stat = "Average" }], ["Insightly", "LatencySeconds", "Environment", var.environment, { stat = "Maximum" }]] },
        { title = "LLM cost (USD) and tokens", metrics = [["Insightly", "CostUSD", "Environment", var.environment, { stat = "Sum" }], ["Insightly", "Tokens", "Environment", var.environment, { stat = "Sum", yAxis = "right" }]] },
        { title = "Guardrail blocks / SQL errors", metrics = [["Insightly", "GuardrailBlocks", "Environment", var.environment, { stat = "Sum" }], ["Insightly", "SqlErrors", "Environment", var.environment, { stat = "Sum" }]] },
        { title = "RDS CPU %", metrics = [["AWS/RDS", "CPUUtilization", "DBInstanceIdentifier", aws_db_instance.main.identifier]] },
        { title = "RDS connections", metrics = [["AWS/RDS", "DatabaseConnections", "DBInstanceIdentifier", aws_db_instance.main.identifier]] },
        ] : {
        type   = "metric"
        x      = (i % 2) * 12
        y      = floor(i / 2) * 6
        width  = 12
        height = 6
        properties = {
          title   = w.title
          metrics = w.metrics
          region  = var.region
          period  = 300
          view    = "timeSeries"
        }
      }
    ]
  })
}
