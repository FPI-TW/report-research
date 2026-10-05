#!/usr/bin/env bash
set -euo pipefail

PROFILE="${AWS_PROFILE:-report-research}"
REGION="${AWS_REGION:-ap-southeast-1}"
STACK_NAME="${STACK_NAME:-report-research-staging}"
EXPECTED_ACCOUNT="${EXPECTED_ACCOUNT:-607063196781}"

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

account_id="$(aws sts get-caller-identity \
  --profile "$PROFILE" \
  --region "$REGION" \
  --query Account \
  --output text)"
[[ "$account_id" == "$EXPECTED_ACCOUNT" ]] || die "AWS account mismatch: expected $EXPECTED_ACCOUNT, got $account_id"

stack_outputs="$(aws cloudformation describe-stacks \
  --profile "$PROFILE" \
  --region "$REGION" \
  --stack-name "$STACK_NAME" \
  --query 'Stacks[0].Outputs' \
  --output json)"

output_value() {
  local key="$1"
  jq -er --arg key "$key" '.[] | select(.OutputKey == $key) | .OutputValue' <<<"$stack_outputs"
}

instance_id="$(output_value Ec2InstanceId)"
db_endpoint="$(output_value RdsEndpointAddress)"
db_port="$(output_value RdsEndpointPort)"
db_identifier="$(output_value RdsDbInstanceIdentifier)"
secret_arn="$(output_value RdsMasterSecretArn)"

[[ "$instance_id" =~ ^i-[0-9a-f]+$ ]] || die "invalid EC2 instance ID from stack output"
[[ "$db_endpoint" =~ ^[A-Za-z0-9.-]+$ ]] || die "invalid RDS endpoint from stack output"
[[ "$db_port" == "5432" ]] || die "unexpected RDS port: $db_port"
[[ "$db_identifier" =~ ^[A-Za-z][A-Za-z0-9-]+$ ]] || die "invalid RDS identifier from stack output"
[[ "$secret_arn" == arn:aws:secretsmanager:* ]] || die "invalid secret ARN from stack output"

read -r parameter_group parameter_apply_status < <(aws rds describe-db-instances \
  --profile "$PROFILE" \
  --region "$REGION" \
  --db-instance-identifier "$db_identifier" \
  --query 'DBInstances[0].DBParameterGroups[0].[DBParameterGroupName,ParameterApplyStatus]' \
  --output text)
[[ "$parameter_apply_status" == "in-sync" ]] || die "RDS parameter group is not in-sync: $parameter_apply_status"

parameter_group_json="$(aws rds describe-db-parameters \
  --profile "$PROFILE" \
  --region "$REGION" \
  --db-parameter-group-name "$parameter_group" \
  --output json)"
force_ssl_value="$(jq -er '.Parameters[] | select(.ParameterName == "rds.force_ssl") | .ParameterValue' \
  <<<"$parameter_group_json")"
[[ "$force_ssl_value" == "1" ]] || die "rds.force_ssl is not 1: $force_ssl_value"

ping_status="$(aws ssm describe-instance-information \
  --profile "$PROFILE" \
  --region "$REGION" \
  --filters "Key=InstanceIds,Values=$instance_id" \
  --query 'InstanceInformationList[0].PingStatus' \
  --output text)"
[[ "$ping_status" == "Online" ]] || die "SSM node is not Online: $ping_status"

remote_script="$(printf '%s\n' \
  'set -euo pipefail' \
  "REGION='$REGION'" \
  "DB_ENDPOINT='$db_endpoint'" \
  "DB_PORT='$db_port'" \
  "SECRET_ARN='$secret_arn'" \
  'CA_FILE=/opt/report-research-infra/rds-global-bundle.pem' \
  'test -f /opt/report-research-infra/bootstrap-complete' \
  'test -s "$CA_FILE"' \
  'command -v aws >/dev/null' \
  'command -v jq >/dev/null' \
  'command -v psql >/dev/null' \
  'secret_json="$(aws secretsmanager get-secret-value --region "$REGION" --secret-id "$SECRET_ARN" --query SecretString --output text)"' \
  'db_user="$(jq -er .username <<<"$secret_json")"' \
  'db_password="$(jq -er .password <<<"$secret_json")"' \
  'unset secret_json' \
  'export PGPASSWORD="$db_password"' \
  'psql "host=$DB_ENDPOINT port=$DB_PORT dbname=research user=$db_user sslmode=verify-full sslrootcert=$CA_FILE" -v ON_ERROR_STOP=1 <<'"'"'SQL'"'"'' \
  'CREATE EXTENSION IF NOT EXISTS vector;' \
  'CREATE EXTENSION IF NOT EXISTS pg_trgm;' \
  'DO $$' \
  'DECLARE' \
  '  vector_version text;' \
  '  vector_major integer;' \
  '  vector_minor integer;' \
  'BEGIN' \
  '  SELECT extversion INTO vector_version FROM pg_extension WHERE extname = '"'"'vector'"'"';' \
  '  vector_major := split_part(vector_version, '"'"'.'"'"', 1)::integer;' \
  '  vector_minor := split_part(vector_version, '"'"'.'"'"', 2)::integer;' \
  '  IF vector_major = 0 AND vector_minor < 8 THEN' \
  '    RAISE EXCEPTION '"'"'pgvector % is below required 0.8'"'"', vector_version;' \
  '  END IF;' \
  '  IF NOT EXISTS (SELECT 1 FROM pg_stat_ssl WHERE pid = pg_backend_pid() AND ssl) THEN' \
  '    RAISE EXCEPTION '"'"'current connection is not using TLS'"'"';' \
  '  END IF;' \
  'END' \
  '$$;' \
  'SELECT current_database() AS database, current_user AS db_user, version() AS server_version;' \
  'SELECT ssl, version AS tls_version, cipher FROM pg_stat_ssl WHERE pid = pg_backend_pid();' \
  'SELECT extname, extversion FROM pg_extension WHERE extname IN ('"'"'vector'"'"', '"'"'pg_trgm'"'"') ORDER BY extname;' \
  'SQL' \
  'if psql "host=$DB_ENDPOINT port=$DB_PORT dbname=research user=$db_user sslmode=disable connect_timeout=10" -c '"'"'SELECT 1'"'"' >/tmp/non-tls.out 2>&1; then' \
  '  echo '"'"'non-TLS connection unexpectedly succeeded'"'"' >&2' \
  '  exit 1' \
  'fi' \
  'grep -Eq '"'"'SSL off|no encryption'"'"' /tmp/non-tls.out' \
  'rm -f /tmp/non-tls.out' \
  'echo '"'"'rds.force_ssl enforced: non-TLS connection rejected'"'"'' \
  'unset PGPASSWORD db_password db_user' \
  'test ! -d /home/ubuntu/report-research' \
  '! command -v docker >/dev/null 2>&1' \
  '! command -v nginx >/dev/null 2>&1' \
  '! command -v cloudflared >/dev/null 2>&1')"

remote_script_b64="$(printf '%s' "$remote_script" | base64 | tr -d '\n')"
run_with_bash="printf '%s' '$remote_script_b64' | base64 --decode | bash"
parameters_json="$(jq -cn --arg command "$run_with_bash" '{commands: [$command]}')"
command_id="$(aws ssm send-command \
  --profile "$PROFILE" \
  --region "$REGION" \
  --instance-ids "$instance_id" \
  --document-name AWS-RunShellScript \
  --comment "Validate report-research EC2 to RDS TLS connectivity" \
  --parameters "$parameters_json" \
  --query 'Command.CommandId' \
  --output text)"

aws ssm wait command-executed \
  --profile "$PROFILE" \
  --region "$REGION" \
  --command-id "$command_id" \
  --instance-id "$instance_id" || true

invocation="$(aws ssm get-command-invocation \
  --profile "$PROFILE" \
  --region "$REGION" \
  --command-id "$command_id" \
  --instance-id "$instance_id" \
  --output json)"

status="$(jq -r .Status <<<"$invocation")"
printf '%s\n' "$(jq -r .StandardOutputContent <<<"$invocation")"
if [[ "$status" != "Success" ]]; then
  printf '%s\n' "$(jq -r .StandardErrorContent <<<"$invocation")" >&2
  die "SSM connectivity validation failed with status $status"
fi

printf 'EC2 -> RDS TLS connectivity and extension validation passed.\n'
printf 'RDS parameter group %s is in-sync with rds.force_ssl=%s.\n' "$parameter_group" "$force_ssl_value"
