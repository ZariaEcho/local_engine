# SIP task output contract

Return one SIP object where possible. Prefer YAML and do not wrap it in a Markdown fence.

Required fields: `type`, `skill`, `task_id`, `confidence`, `assumptions`, `unknowns`, `risks`, `warnings`, `dependencies`, `artifacts`, `body`.

Optional failure field: `failure_type` (`network`, `format`, `logic`, `timeout`, or `unknown`).
