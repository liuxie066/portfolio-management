# Feishu Manual Field Ownership Failures

- A field dictionary that only lists required fields is not enough for manual maintenance; it must say who owns each field.
- `cash_flow` previously documented `direction` and `broker`, while code uses `flow_type`, `cny_amount`, `exchange_rate`, `dedup_key`, `source`, and `remark`; following the old doc would create rows the code interprets incorrectly.
- `nav_history` previously omitted `total_value` and derived fields, making the schema doc unsafe for repair work.
- The schema parser kept reading backticked bullets after an Optional section, so enum examples could be misclassified as optional fields.
