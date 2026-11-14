#!/usr/bin/env bash
# Deploy IQ's ECS services onto one image, or roll them back (G60). Called by .github/workflows/deploy.yml and
# rollback.yml; tested against a stand-in `aws` in scripts/deploy/tests/.
#
#   ecs.sh deploy     roll every service onto IMAGE_REPO:IMAGE_TAG (migrations first unless RUN_MIGRATIONS=false)
#   ecs.sh rollback   TARGET=previous (the image before the one running) or TARGET=<commit sha>; never migrates
#
# Environment: CLUSTER, SERVICES ("web worker beat", commas or spaces; the first one runs the migrations),
# IMAGE_REPO (ECR repository URL), IMAGE_TAG (deploy), TARGET (rollback), APP_CONTAINER (optional: the container
# that runs `migrate`; default the first container using the image), SUMMARY_FILE (optional, markdown appended).
#
# Every aws call asks for JSON and is read with jq (no --query), so the stand-in only has to return whole answers.
# Written for bash 3.2 (macOS) as well as Linux: no associative arrays, guarded empty arrays.

set -euo pipefail

say() { echo "$*" >&2; }
fail() { echo "::error title=${1}::${2}" >&2; exit 1; }
summary() { if [ -n "${SUMMARY_FILE:-}" ]; then echo "$*" >> "$SUMMARY_FILE"; fi; }

need() { [ -n "${!1:-}" ] || fail "Not configured" "$1 is not set."; }

read_services() {
  SERVICE_LIST=()
  local s
  for s in $(echo "$SERVICES" | tr ',' ' '); do SERVICE_LIST+=("$s"); done
  [ "${#SERVICE_LIST[@]}" -gt 0 ] || fail "Not configured" "SERVICES is empty."
}

service_json() {
  local json
  json=$(aws ecs describe-services --cluster "$CLUSTER" --services "$1" --output json)
  if ! jq -e '.services[0].status == "ACTIVE"' <<< "$json" > /dev/null; then
    fail "Service not found" "Service $1 is not active in cluster $CLUSTER."
  fi
  jq -c '.services[0]' <<< "$json"
}

# The tag of the image from our repository that a task definition runs ('' if none).
image_tag_of() {
  aws ecs describe-task-definition --task-definition "$1" --output json \
    | jq -r --arg repo "$IMAGE_REPO" \
      '[.taskDefinition.containerDefinitions[].image | select(startswith($repo + ":"))][0] // "" | ltrimstr($repo + ":")'
}

image_exists() {
  aws ecr describe-images --repository-name "${IMAGE_REPO#*/}" --image-ids "imageTag=$1" --output json > /dev/null 2>&1
}

# Register a copy of the service's task definition with every container of our repository on $1.
register_revision() {
  local current_arn=$1 image=$2 tmp
  tmp=$(mktemp)
  aws ecs describe-task-definition --task-definition "$current_arn" --output json \
    | jq --arg repo "$IMAGE_REPO" --arg image "$image" '
        .taskDefinition
        | del(.taskDefinitionArn, .revision, .status, .requiresAttributes, .compatibilities,
              .registeredAt, .registeredBy, .deregisteredAt)
        | .containerDefinitions |= map(
            if (.image == $repo) or (.image | startswith($repo + ":")) then .image = $image else . end)
      ' > "$tmp"
  if ! jq -e --arg image "$image" 'any(.containerDefinitions[]; .image == $image)' "$tmp" > /dev/null; then
    rm -f "$tmp"
    fail "Nothing to update" "No container in $current_arn uses $IMAGE_REPO."
  fi
  aws ecs register-task-definition --cli-input-json "file://$tmp" --output json | jq -r '.taskDefinition.taskDefinitionArn'
  rm -f "$tmp"
}

run_migrations() {
  local service=$1 task_def=$2 image=$3 svc container overrides run_json task_arn task exit_code
  svc=$(service_json "$service") || exit 1
  container="${APP_CONTAINER:-}"
  if [ -z "$container" ]; then
    container=$(aws ecs describe-task-definition --task-definition "$task_def" --output json \
      | jq -r --arg image "$image" '[.taskDefinition.containerDefinitions[] | select(.image == $image)][0].name')
  fi

  # Placed like the service: launch type or capacity providers, subnets and security groups.
  local args=()
  if [ -n "$(jq -r '.launchType // empty' <<< "$svc")" ]; then args+=(--launch-type "$(jq -r '.launchType' <<< "$svc")"); fi
  if [ "$(jq '(.capacityProviderStrategy // []) | length' <<< "$svc")" -gt 0 ]; then
    args+=(--capacity-provider-strategy "$(jq -c '.capacityProviderStrategy' <<< "$svc")")
  fi
  if [ "$(jq '.networkConfiguration != null' <<< "$svc")" = "true" ]; then
    args+=(--network-configuration "$(jq -c '.networkConfiguration' <<< "$svc")")
  fi
  overrides=$(jq -nc --arg name "$container" \
    '{containerOverrides: [{name: $name, command: ["python", "manage.py", "migrate", "--noinput"]}]}')

  run_json=$(aws ecs run-task --cluster "$CLUSTER" --task-definition "$task_def" --count 1 \
    ${args[@]+"${args[@]}"} --overrides "$overrides" --started-by "deploy-migrate" --output json)
  if [ "$(jq '.failures | length' <<< "$run_json")" -gt 0 ]; then
    fail "Migrations failed" "Could not start the migration task: $(jq -c '.failures' <<< "$run_json"). Nothing was deployed."
  fi
  task_arn=$(jq -r '.tasks[0].taskArn' <<< "$run_json")
  say "Waiting for migration task $task_arn ..."
  aws ecs wait tasks-stopped --cluster "$CLUSTER" --tasks "$task_arn"
  task=$(aws ecs describe-tasks --cluster "$CLUSTER" --tasks "$task_arn" --output json)
  exit_code=$(jq -r --arg name "$container" '.tasks[0].containers[] | select(.name == $name) | .exitCode // "none"' <<< "$task")
  if [ "$exit_code" != "0" ]; then
    fail "Migrations failed" "Exit code $exit_code ($(jq -r '.tasks[0].stoppedReason // "no reason given"' <<< "$task")). Nothing was deployed; see the task's logs ($task_arn)."
  fi
  say "Migrations applied."
  summary "- Migrations applied (task \`$task_arn\`)"
}

update_service() {
  local service=$1 task_def=$2 config
  # Keep the service's own percentages (beat: never two at once) and switch the circuit breaker on.
  config=$(service_json "$service" | jq -c '.deploymentConfiguration + {deploymentCircuitBreaker: {enable: true, rollback: true}}')
  aws ecs update-service --cluster "$CLUSTER" --service "$service" --task-definition "$task_def" \
    --deployment-configuration "$config" --output json > /dev/null
}

# The task definition a service's PRIMARY deployment runs, and whether that rollout completed.
primary_of() {
  service_json "$1" | jq -r '(.deployments[] | select(.status == "PRIMARY")) | "\(.taskDefinition) \(.rolloutState)"'
}

wait_stable() {
  # One wait for all services (aws accepts up to 10); a timeout is judged by the PRIMARY check that follows.
  aws ecs wait services-stable --cluster "$CLUSTER" --services "${SERVICE_LIST[@]}" || say "Not every service settled in time."
}

# Roll SERVICE_LIST onto IMAGE_REPO:$1. Migrations first when $2 is true.
roll_out() {
  local tag=$1 migrate=$2 image="$IMAGE_REPO:$1" i service svc arn
  CURRENT=()
  image_exists "$tag" || fail "Image not found" "$image is not in ECR."

  for service in "${SERVICE_LIST[@]}"; do
    svc=$(service_json "$service") || exit 1
    CURRENT+=("$(jq -r '.taskDefinition' <<< "$svc")")
  done
  for i in "${!SERVICE_LIST[@]}"; do
    arn=$(register_revision "${CURRENT[$i]}" "$image") || exit 1
    NEW+=("$arn")
    say "${SERVICE_LIST[$i]}: ${CURRENT[$i]} -> ${NEW[$i]}"
  done

  if [ "$migrate" = "true" ]; then run_migrations "${SERVICE_LIST[0]}" "${NEW[0]}" "$image"; fi

  for i in "${!SERVICE_LIST[@]}"; do update_service "${SERVICE_LIST[$i]}" "${NEW[$i]}"; done
  wait_stable

  local failed=() running state
  for i in "${!SERVICE_LIST[@]}"; do
    read -r running state <<< "$(primary_of "${SERVICE_LIST[$i]}")"
    if [ "$running" != "${NEW[$i]}" ] || [ "$state" != "COMPLETED" ]; then
      failed+=("${SERVICE_LIST[$i]} (running $running, $state)")
    fi
  done

  if [ "${#failed[@]}" -gt 0 ]; then
    # One version for the whole app: put every service back on what it ran before.
    say "Rollout failed for: ${failed[*]}. Putting every service back on its previous task definition."
    for i in "${!SERVICE_LIST[@]}"; do
      read -r running state <<< "$(primary_of "${SERVICE_LIST[$i]}")"
      if [ "$running" != "${CURRENT[$i]}" ]; then update_service "${SERVICE_LIST[$i]}" "${CURRENT[$i]}"; fi
    done
    wait_stable
    local back=()
    for i in "${!SERVICE_LIST[@]}"; do
      read -r running state <<< "$(primary_of "${SERVICE_LIST[$i]}")"
      if [ "$running" != "${CURRENT[$i]}" ] || [ "$state" != "COMPLETED" ]; then back+=("${SERVICE_LIST[$i]}"); fi
    done
    summary "### Deployment of \`$tag\` failed"
    summary "- Did not become healthy: ${failed[*]}"
    if [ "${#back[@]}" -gt 0 ]; then
      fail "Deployment failed" "Not healthy: ${failed[*]}. NOT back on the previous version: ${back[*]}. Act now: see docs/runbooks/deploy.md."
    fi
    fail "Deployment failed" "Not healthy: ${failed[*]}. Every service is back on its previous task definition."
  fi

  DEPLOYED=true
  for i in "${!SERVICE_LIST[@]}"; do
    summary "- \`${SERVICE_LIST[$i]}\`: \`${CURRENT[$i]}\` → \`${NEW[$i]}\`"
  done
}

# The image tag to roll back to: the newest earlier revision of the first service's family on another image.
previous_tag() {
  local svc current current_tag family revision arn tag
  svc=$(service_json "${SERVICE_LIST[0]}") || exit 1
  current=$(jq -r '.taskDefinition' <<< "$svc")
  current_tag=$(image_tag_of "$current")
  family=$(aws ecs describe-task-definition --task-definition "$current" --output json | jq -r '.taskDefinition.family')
  revision=${current##*:}
  for arn in $(aws ecs list-task-definitions --family-prefix "$family" --sort DESC --output json \
      | jq -r --argjson rev "$revision" '.taskDefinitionArns[] | select((split(":") | last | tonumber) < $rev)'); do
    tag=$(image_tag_of "$arn")
    if [ -n "$tag" ] && [ "$tag" != "$current_tag" ]; then echo "$tag"; return; fi
  done
  fail "Nothing to roll back to" "No earlier revision of $family runs another image than $current_tag."
}

# A deploy that does not finish leaves no new revision ACTIVE, so `rollback previous` never picks an image that
# failed its migrations or its health checks.
NEW=()
DEPLOYED=false
discard_unused() {
  local arn
  if [ "$DEPLOYED" = "true" ]; then return; fi
  for arn in ${NEW[@]+"${NEW[@]}"}; do
    aws ecs deregister-task-definition --task-definition "$arn" --output json > /dev/null 2>&1 \
      && say "Deregistered unused $arn" || say "Could not deregister $arn"
  done
}
trap discard_unused EXIT

main() {
  need CLUSTER; need SERVICES; need IMAGE_REPO
  read_services
  case "${1:-}" in
    deploy)
      need IMAGE_TAG
      summary "### Deployed \`$IMAGE_TAG\`"
      roll_out "$IMAGE_TAG" "${RUN_MIGRATIONS:-true}"
      ;;
    rollback)
      need TARGET
      local tag
      if [ "$TARGET" = "previous" ]; then tag=$(previous_tag) || exit 1; else tag=$TARGET; fi
      [[ "$tag" =~ ^[0-9a-f]{7,40}$ ]] || fail "Bad target" "'$tag' is not a commit SHA."
      say "Rolling back to $tag (no migrations)."
      summary "### Rolled back to \`$tag\`"
      roll_out "$tag" false
      ;;
    *) echo "usage: ecs.sh deploy|rollback" >&2; exit 2 ;;
  esac
}

main "$@"
