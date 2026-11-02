variable "project" {
  description = "Prefix for every resource name."
  type        = string
  default     = "newsletter"
}

variable "region" {
  description = "Region for SES and everything else (same as the site)."
  type        = string
  default     = "eu-west-1"
}

variable "domain" {
  description = "Domain the newsletter is sent from."
  type        = string
  default     = "adan.cloud"
}

variable "mail_from_subdomain" {
  description = "Subdomain SES uses for bounces (custom MAIL FROM), so SPF aligns with the sending domain."
  type        = string
  default     = "bounce"
}

variable "from_address" {
  description = "Sender shown to readers, e.g. \"Adan Álvarez <adan@adan.cloud>\". Must be on var.domain."
  type        = string
}

variable "reply_to" {
  description = "Where replies go. Leave empty to reply to the sender address."
  type        = string
  default     = ""
}

variable "site_url" {
  description = "The site the sign-up form lives on; every redirect goes back there."
  type        = string
  default     = "https://adan.cloud"
}

variable "consent_versions" {
  description = "Versions (dates) of the privacy notice the site may show, current first. The site posts the version it showed and it's stored with the consent record; add the new date first when the notice changes."
  type        = list(string)
}

variable "roles" {
  description = "Allowed values for the optional role field. Must match src/data/newsletter.js on the site."
  type        = list(string)
  default = [
    "CISO / head of security",
    "CTO",
    "Founder",
    "Security engineering or architecture",
    "Detection engineering",
    "SOC / IR",
    "Cloud security",
    "Software engineering",
    "IT",
    "Student",
    "Other",
  ]
}

variable "daily_confirmation_limit" {
  description = "Most confirmation emails sent in one day, so a flood of fake sign-ups can't hurt the sending reputation. An alarm fires when it's reached. Raise it for a big announcement day."
  type        = number
  default     = 100
}

variable "retention_days" {
  description = "How long a record (with its consent history) is kept after someone unsubscribes, bounces or complains. To be confirmed by the legal review."
  type        = number
  default     = 1095
}

variable "unconfirmed_retention_days" {
  description = "How long an address that never confirmed is kept after it bounces or complains (someone else may have typed it in)."
  type        = number
  default     = 30
}

variable "require_edge_secret" {
  description = "Refuse /api requests that didn't come through Cloudflare (needs the Cloudflare Transform Rule from the README)."
  type        = bool
  default     = true
}

variable "previous_origin_secrets" {
  description = "Old origin or edge secrets still accepted during a rotation. Empty normally."
  type        = list(string)
  default     = []
  sensitive   = true
}

variable "alert_email" {
  description = "Gets alarms: bounce or complaint rates rising, or the sign-up failing."
  type        = string
}

variable "log_retention_days" {
  description = "How long Lambda logs are kept. Logs hold no email addresses, only short hashes."
  type        = number
  default     = 90
}

variable "site_bucket" {
  description = "The site's S3 bucket. The feedback function reads newsletter/polls.json (the open questions) from it."
  type        = string
  default     = "adanalvarez"
}

variable "comment_retention_days" {
  description = "How long anonymous comments left with an answer are kept."
  type        = number
  default     = 365
}
