CODING AGENT: Can update but NEVER DELETE!

# gcloud Cheatsheet

Common commands for listing resources and managing Secret Manager. All commands
assume you have the right project set; add `--project <project-id>` if needed.

## Auth & Project

```bash
gcloud auth login
gcloud auth application-default login
gcloud config set project <project-id>
gcloud config list
gcloud projects list
```

## Regions & Zones

```bash
gcloud compute regions list
gcloud compute zones list
```

## Compute (VMs)

```bash
gcloud compute instances list
gcloud compute instances describe <instance-name> --zone <zone>
gcloud compute disks list
gcloud compute addresses list
gcloud compute firewall-rules list
```

## IAM

```bash
gcloud iam service-accounts list
gcloud iam service-accounts describe <sa-name>@<project-id>.iam.gserviceaccount.com
gcloud projects get-iam-policy <project-id>
gcloud projects get-iam-policy <project-id> --flatten="bindings[].members" --format="table(bindings.role,bindings.members)"
```

## Secret Manager (Create, Paste, List)

List secrets and versions:

```bash
gcloud secrets list
gcloud secrets versions list <secret-name>
```

Create a new secret and paste the first value:

```bash
gcloud secrets create <secret-name> --replication-policy="automatic" --data-file=-
# After you press Enter, paste the value, then press Ctrl-D
```

One-liner (pipe the value in):

```bash
printf '%s' "YOUR_SECRET_HERE" | gcloud secrets create <secret-name> --replication-policy="automatic" --data-file=-
```

Add a new version by pasting the value:

```bash
gcloud secrets versions add <secret-name> --data-file=-
# After you press Enter, paste the value, then press Ctrl-D
```

Add a new version from an env var:

```bash
printf '%s' "$CLAUDE_CODE_ADMIN_API_KEY" | gcloud secrets versions add appforge-anthropic-api-key --data-file=- --project appforge-483920
```

Access a secret (latest version):

```bash
gcloud secrets versions access latest --secret=<secret-name>
```

Delete a secret version (use with care):

```bash
gcloud secrets versions destroy <version-number> --secret=<secret-name>
```

## Storage (GCS)

```bash
gcloud storage buckets list
gcloud storage buckets describe gs://<bucket-name>
gcloud storage buckets create gs://<bucket-name> --location=<region>
```

## Cloud Run

```bash
gcloud run services list --region <region>
gcloud run services describe <service-name> --region <region>
gcloud run logs read --service <service-name> --region <region>
```

## Cloud Scheduler

```bash
gcloud scheduler jobs list --location <region>
gcloud scheduler jobs describe <job-name> --location <region>
```
