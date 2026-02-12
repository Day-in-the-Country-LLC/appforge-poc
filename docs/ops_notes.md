# Ops Notes (Cloud Run)

## Why tmux is a bad fit on Cloud Run

- A tmux session created by a Cloud Run container lives inside that specific container instance.
- You cannot attach to it from your local machine.
- Instances are ephemeral and can be restarted at any time; in-flight tmux sessions can disappear.
- tmux is useful for local/SSH-hosted execution where a human can attach. Cloud Run is designed for non-interactive workloads.

Practical implication: on Cloud Run, tmux mostly becomes an implementation detail for multiplexing/logging, not an interactive debugging tool.

## What “split listener and worker” means here

Today the `appforge-webhooks` service is doing two things:

1. Listener responsibilities:
   - Receive GitHub webhook requests.
   - Verify signatures.
   - Parse event types and decide what to do.

2. Worker responsibilities:
   - Clone repos, create worktrees/branches.
   - Spawn an agent run (Claude/Codex).
   - Wait for completion markers, update GitHub, post Slack notifications.

Splitting them means:

- `appforge-webhooks` becomes thin: validate + enqueue work + return quickly.
- A separate worker (Cloud Run service or Cloud Run Job) pulls tasks and runs the expensive/long orchestration.

This gives:

- Reliability: retries/backoff at the queue level, fewer “webhook request timed out” risks.
- Better scaling: listener stays small/fast; worker scales only when needed.
- Better observability: each unit of work becomes its own run with an ID and durable logs.
- Fewer “interactive terminal” assumptions: the worker is explicitly non-interactive.

## Queue options (GCP)

### Cloud Tasks (recommended for “HTTP worker”)

Flow:

1. Listener receives webhook.
2. Listener enqueues a Cloud Task with a payload like `{repo, issue_number, delivery_id}`.
3. Cloud Tasks calls the worker endpoint (HTTP) with the payload.

Pros:

- Built-in retries, rate limiting, and dedupe patterns.
- Good fit for “do this one thing by calling a URL”.

### Pub/Sub (good for event fanout)

Flow:

1. Listener publishes a message.
2. Worker subscribes and processes.

Pros:

- Good if you expect multiple downstream consumers.

### Cloud Run Jobs (recommended for “one issue = one run”)

Flow:

1. Listener triggers a Job execution.
2. Job runs and exits.

Pros:

- Very clean for long-running tasks.
- No always-on worker; pay only for execution time.

## Costs: will splitting increase cost?

Yes, it can, but often it’s close to neutral because you’re paying for compute time either way.

Key points:

- Cloud Run billing is primarily CPU/memory time while a container is actually running a request, plus any configured min instances.
- If you set `min-instances=0` on both listener and worker/job, the “second service” overhead is usually small.
- Splitting can reduce wasted compute because the listener stops doing heavy work and returns quickly.

Cost increases happen when:

- You run `min-instances > 0` on multiple services.
- Worker retries repeatedly due to transient failures.
- Worker is over-provisioned (too much CPU/mem) relative to actual need.

Cost control knobs:

- `min-instances=0` for listener and worker.
- Keep listener on small resources.
- Give worker higher resources only if needed.
- Add dedupe so repeated webhook deliveries do not spawn repeated runs.

## Recommended runtime model for Cloud Run

If you want Cloud Run to be reliable and debuggable without tmux attach:

- Run the agent non-interactively.
- Capture stdout/stderr and persist:
  - Post summaries to Slack/GitHub issue comments.
  - Optionally upload full logs/artifacts to GCS and link them.
- Use a queue (Cloud Tasks / PubSub) and treat each issue run as an idempotent job.

## What we should fix next (practical)

1. Ensure the agent always receives a prompt explicitly (no reliance on tmux echo).
2. Ensure completion signals are robust:
   - Prefer the agent writing `ACE_TASK_DONE.json`.
   - If it fails, the orchestrator should still produce a clear failure summary and logs.
3. Consider moving to a worker/job design so webhook handling is fast and reliable.

